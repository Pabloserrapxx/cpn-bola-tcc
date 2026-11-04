"""Transformação OpenAPI -> CPN (tese, seção 4.2), com extensões explícitas.

Não executa expressões Python contidas no documento. Links são interpretados
por uma gramática restrita; referências externas e recursivas são rejeitadas.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from cpn_bola import (ArcBinding, ColoredPetriNet, Event, InvalidEvent,
                      InvalidResource, Token, Transition, activity_name, user_token)

HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'}


class ModelError(ValueError):
    pass


def pointer(value: Any, path: str) -> Any:
    """RFC 6901, incluindo escapes ~0/~1 e índices de arrays; sem curingas."""
    if path == '':
        return value
    if not path.startswith('/'):
        raise ValueError(f'JSON Pointer inválido: {path}')
    for raw in path[1:].split('/'):
        key = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', key):
                raise ValueError(f'Índice de array inválido: {key}')
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError(f'Não é possível acessar {key} no recurso')
    return value


def freeze(value: Any) -> Any:
    # Tags preservam tipos JSON: True, 1 e 1.0 não viram o mesmo token.
    if isinstance(value, dict):
        return ('object', tuple(sorted((k, freeze(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ('array', tuple(freeze(v) for v in value))
    if value is None:
        return ('null', None)
    return (type(value).__name__, value)


def resource_token(user_id: str, value: Any) -> Token:
    return Token('RESOURCE', (user_id, freeze(value)))


def load_openapi(path: str | Path) -> dict:
    text = Path(path).read_text(encoding='utf-8')
    if str(path).lower().endswith('.json'):
        doc = json.loads(text)
    else:
        import yaml
        doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ModelError('OpenAPI deve ser um objeto.')
    return doc


class Resolver:
    def __init__(self, doc):
        self.doc = doc

    def resolve(self, obj, stack=()):
        if not isinstance(obj, dict) or '$ref' not in obj:
            return obj
        ref = obj['$ref']
        if not isinstance(ref, str) or not ref.startswith('#/'):
            raise ModelError(f'Apenas $ref local é suportado: {ref}')
        if ref in stack:
            raise ModelError(f'Referência recursiva não suportada: {ref}')
        try:
            target = pointer(self.doc, unquote(ref[1:]))
        except (KeyError, ValueError, IndexError) as exc:
            raise ModelError(f'Referência inexistente: {ref}') from exc
        resolved = self.resolve(target, stack + (ref,))
        if not isinstance(resolved, dict):
            raise ModelError(f'$ref não aponta para objeto: {ref}')
        return {**resolved, **{k: v for k, v in obj.items() if k != '$ref'}}

    def schema(self, obj, stack=()):
        if isinstance(obj, bool):
            return obj
        if not isinstance(obj, dict):
            raise ModelError('Schema inválido.')
        if '$ref' in obj:
            ref = obj['$ref']
            if ref in stack:
                raise ModelError(f'Schema recursivo não suportado: {ref}')
            return self.schema(self.resolve(obj), stack + (ref,))
        out = dict(obj)
        for key in ('properties', 'patternProperties', '$defs', 'dependentSchemas'):
            if key in out:
                out[key] = {k: self.schema(v, stack) for k, v in out[key].items()}
        for key in ('items', 'additionalProperties', 'not', 'contains', 'if', 'then', 'else'):
            if key in out:
                out[key] = self.schema(out[key], stack)
        for key in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
            if key in out:
                out[key] = [self.schema(v, stack) for v in out[key]]
        if out.pop('nullable', False) and isinstance(out.get('type'), str):
            out['type'] = [out['type'], 'null']
        for key, bound in [('exclusiveMinimum', 'minimum'), ('exclusiveMaximum', 'maximum')]:
            if isinstance(out.get(key), bool):
                exclusive = out.pop(key)
                if exclusive and bound in out:
                    out[key] = out.pop(bound)
        try:
            Draft202012Validator.check_schema(out)
        except SchemaError as exc:
            raise ModelError(f'Schema inválido: {exc.message}') from exc
        return out

    def body_schema(self, obj):
        obj = self.resolve(obj or {})
        content = obj.get('content', {})
        if not content:
            return {}
        if 'application/json' not in content:
            raise ModelError('Este protótipo suporta apenas corpos application/json.')
        return self.schema(content['application/json'].get('schema', {}))


def expression(expr: Any, event: Event, *, legacy=False) -> Any:
    if not isinstance(expr, str) or not expr.startswith('$'):
        return expr
    if expr == '$statusCode':
        return event.status_code
    if expr == '$method':
        return event.method.upper()
    if expr == '$url':
        raise ModelError('$url não é suportado: o evento guarda a rota normalizada.')
    match = re.fullmatch(r'\$(request|response)\.body(?:#(.*))?', expr)
    if match:
        path = match[2] or ''
        if legacy:
            path = path.replace('.', '/')
        return pointer(event.request_body if match[1] == 'request' else event.response_body, path)
    match = re.fullmatch(r'\$(request|response)\.(path|query|header)\.(.+)', expr)
    if match:
        side, location, name = match.groups()
        if side == 'response' and location != 'header':
            raise ModelError(f'Expressão inválida: {expr}')
        values = {'path': event.path_params, 'query': event.query_params,
                  'header': event.request_headers if side == 'request' else event.response_headers}[location]
        return values[name.lower() if location == 'header' else name]
    raise ModelError(f'Expressão não suportada: {expr}')


def validate_expression(expr):
    if not isinstance(expr, str) or not expr.startswith('$'):
        return
    if expr in ('$statusCode', '$method'):
        return
    if re.fullmatch(r'\$(request|response)\.body(?:#(?:/.*)?)?', expr):
        return
    if re.fullmatch(r'\$request\.(path|query|header)\..+', expr):
        return
    if re.fullmatch(r'\$response\.header\..+', expr):
        return
    raise ModelError(f'Expressão não suportada: {expr}')


@dataclass(frozen=True)
class Field:
    operation: str
    location: str
    name: str
    schema: Any

    @property
    def place(self):
        # JSON evita colisões entre nomes que contêm separadores.
        return 'p:' + json.dumps([self.operation, self.location, self.name], ensure_ascii=False)

    def read(self, event):
        if self.location == 'body':
            return pointer(event.request_body, self.name)
        values = {'path': event.path_params, 'query': event.query_params,
                  'header': event.request_headers, 'cookie': {}}[self.location]
        return values[self.name.lower() if self.location == 'header' else self.name]

    def coerce(self, value):
        # Apenas parâmetros HTTP serializados; não converter corpos JSON silenciosamente.
        kind = self.schema.get('type') if isinstance(self.schema, dict) else None
        # JSON Schema considera 6.0 um inteiro; canonicalizar números equivalentes.
        if kind == 'integer' and type(value) is float and value.is_integer():
            return int(value)
        if kind == 'number' and type(value) is int:
            return float(value)
        if self.location in ('path', 'query', 'header') and isinstance(value, str):
            if kind == 'integer' and re.fullmatch(r'-?[0-9]+', value):
                return int(value)
            if kind == 'number':
                try:
                    return float(value)
                except ValueError:
                    pass
            if kind == 'boolean' and value in ('true', 'false'):
                return value == 'true'
        return value


@dataclass(frozen=True)
class LinkArc:
    source: str
    field: Field
    expr: Any
    many: bool = False


def build_openapi_cpn(document: Mapping[str, Any] | str | Path, *,
                      legacy_expressions=False, links2cpn_compatible=False) -> ColoredPetriNet:
    """Constrói a rede.

    ``links2cpn_compatible`` reproduz as convenções observadas no programa dos
    autores: notação pontuada, IDs textuais, expansão implícita de arrays e
    ausência das validações adicionais de schema/pós-condição deste projeto.
    """
    legacy_expressions = legacy_expressions or links2cpn_compatible
    doc = load_openapi(document) if isinstance(document, (str, Path)) else dict(document)
    if not str(doc.get('openapi', '')).startswith(('3.0.', '3.1.')):
        raise ModelError('Use OpenAPI 3.0.x ou 3.1.x.')
    resolver = Resolver(doc)
    operations, refs = {}, {}
    for path, raw_item in doc.get('paths', {}).items():
        item = resolver.resolve(raw_item)
        for method, raw_op in item.items():
            if method not in HTTP_METHODS:
                continue
            op = resolver.resolve(raw_op)
            op_id = op.get('operationId', f'{method}:{path}')
            if op_id in operations:
                raise ModelError(f'operationId duplicado: {op_id}')
            params = {}
            for raw in item.get('parameters', []) + op.get('parameters', []):
                param = resolver.resolve(raw)
                params[(param['in'], param['name'])] = param
            operations[op_id] = dict(path=path, method=method, op=op, params=params)
            refs[f'#/paths/{path.replace("~", "~0").replace("/", "~1")}/{method}'] = op_id
    if not operations:
        raise ModelError('Nenhuma operação OpenAPI encontrada.')

    fields, links, responses = {}, [], {}
    for op_id, data in operations.items():
        for raw_status, raw_response in data['op'].get('responses', {}).items():
            status = str(raw_status).upper()
            if not re.fullmatch(r'[1-5][0-9]{2}|[1-5]XX|DEFAULT', status):
                raise ModelError(f'Status inválido: {status}')
            activity = activity_name(data['method'], data['path'], status)
            response = resolver.resolve(raw_response)
            responses[activity] = (op_id, status, response)
            for raw_link in response.get('links', {}).values():
                link = resolver.resolve(raw_link)
                if ('operationId' in link) == ('operationRef' in link):
                    raise ModelError('Link deve ter exatamente operationId ou operationRef.')
                target = link.get('operationId') or refs.get(link.get('operationRef'))
                if target not in operations:
                    raise ModelError(f'Destino de link inexistente: {target}')
                target_data = operations[target]
                mappings = []
                for name, expr in link.get('parameters', {}).items():
                    matches = [(loc, key, p) for (loc, key), p in target_data['params'].items()
                               if name in (key, f'{loc}.{key}')]
                    if len(matches) != 1:
                        raise ModelError(f'Parâmetro de link ausente/ambíguo: {target}.{name}')
                    loc, key, param = matches[0]
                    if loc == 'cookie':
                        raise ModelError('Parâmetros cookie não suportados.')
                    mappings.append((loc, key, resolver.schema(param.get('schema', {})), expr))
                if 'requestBody' in link:
                    schema = resolver.body_schema(target_data['op'].get('requestBody'))
                    body = link['requestBody']
                    if isinstance(body, dict):
                        # Convenção da tese: vincular propriedades individuais do corpo.
                        for key, expr in body.items():
                            prop_schema = schema.get('properties', {}).get(key)
                            if prop_schema is None:
                                raise ModelError(f'Propriedade de corpo ausente: {target}.{key}')
                            mappings.append(('body', '/' + key.replace('~', '~0').replace('/', '~1'), prop_schema, expr))
                    else:
                        mappings.append(('body', '', schema, body))
                if not mappings:
                    raise ModelError('Link sem parâmetros/corpo não define fluxo de dados.')
                for loc, key, schema, expr in mappings:
                    validate_expression(expr)
                    field = Field(target, loc, key, schema)
                    fields[field.place] = field
                    links.append(LinkArc(activity, field, expr, bool(link.get('x-cpn-many', False))))

    net = ColoredPetriNet(doc.get('info', {}).get('title', 'OpenAPI-CPN'))
    net.add_place('p_user')
    for name in fields:
        net.add_place(name)
    transition_info, drawing_arcs, omitted = {}, [], []
    for activity, (op_id, status, response) in responses.items():
        incoming = [f for f in fields.values() if f.operation == op_id]
        outgoing = [link for link in links if link.source == activity]
        if not incoming and not outgoing:
            omitted.append(activity)
            continue
        data = operations[op_id]
        request_schema = resolver.body_schema(data['op'].get('requestBody'))
        response_schema = resolver.body_schema(response)
        checks = {} if links2cpn_compatible else response.get('x-cpn-equals', {})
        for observed, expected in checks.items():
            if not observed.startswith('/'):
                raise ModelError('x-cpn-equals requer JSON Pointer da resposta.')
            validate_expression(expected)
        params = [(loc, name, resolver.schema(p.get('schema', {})), p.get('required', False))
                  for (loc, name), p in data['params'].items()]
        transition = _make_transition(activity, incoming, outgoing, request_schema,
                                      response_schema, params, checks, legacy_expressions,
                                      links2cpn_compatible)
        net.add_transition(transition)
        transition_info[activity] = dict(operation=op_id, method=data['method'], path=data['path'],
                                         status=status,
                                         guards=('nenhuma (perfil Links2CPN)' if links2cpn_compatible
                                                 else 'schemas de requisição/parâmetros'),
                                         checks=checks)
        drawing_arcs.extend([dict(source='p_user', target=activity, label='USER'),
                             dict(source=activity, target='p_user', label='USER')])
        for f in incoming:
            label = f'{f.location}:{f.name or "/"}'
            drawing_arcs.extend([dict(source=f.place, target=activity, label=label),
                                 dict(source=activity, target=f.place, label='preserva recurso')])
        for link in outgoing:
            drawing_arcs.append(dict(source=activity, target=link.field.place,
                                     label=str(link.expr) + (' [cada item]' if link.many else '')))
    if not net.transitions:
        raise ModelError('Rede vazia: adicione links que descrevam o fluxo de dados.')
    net.metadata = dict(transitions=transition_info, arcs=drawing_arcs,
                        event_only_arcs=True,
                        omitted_activities=omitted,
                        fields={name: dict(operation=f.operation, location=f.location,
                                           name=f.name, schema=f.schema) for name, f in fields.items()},
                        routes=[dict(method=d['method'], path=d['path'],
                                     parameters=[dict(location=loc, name=name, schema=resolver.schema(p.get('schema', {})))
                                                 for (loc, name), p in d['params'].items()])
                                for d in operations.values()],
                        legacy_expressions=legacy_expressions,
                        links2cpn_compatible=links2cpn_compatible)
    return net


def _make_transition(activity, incoming, outgoing, request_schema, response_schema,
                     params, checks, legacy, links2cpn_compatible=False):
    request_validator = Draft202012Validator(request_schema)
    response_validator = Draft202012Validator(response_schema)

    def bind(event, net):
        values = {}
        for f in incoming:
            try:
                values[f.place] = f.coerce(f.read(event))
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise InvalidEvent(f'Campo obrigatório ausente: {f.location}:{f.name}') from exc
        return dict(user_id=event.user_id, values=values)

    def inputs(binding, event):
        return [ArcBinding('p_user', user_token(event.user_id))] + [
            ArcBinding(f.place, resource_token(
                event.user_id,
                str(binding['values'][f.place]) if links2cpn_compatible else binding['values'][f.place]))
            for f in incoming]

    def guard(binding, event):
        if links2cpn_compatible:
            return True
        if not request_validator.is_valid(event.request_body):
            return False
        for loc, name, schema, required in params:
            f = Field('', loc, name, schema)
            try:
                value = f.coerce(f.read(event))
            except KeyError:
                if required:
                    return False
                continue
            if not Draft202012Validator(schema).is_valid(value):
                return False
        return True

    def outputs(binding, event):
        if not links2cpn_compatible:
            errors = list(response_validator.iter_errors(event.response_body))
            if errors:
                raise InvalidResource('Resposta fora do schema: ' + errors[0].message)
        arcs = inputs(binding, event)  # Recursos reutilizáveis, arcos de retorno.
        for link in outgoing:
            source_events = [event]
            expand_array = link.many or (links2cpn_compatible and isinstance(event.response_body, list))
            if expand_array:
                if not isinstance(event.response_body, list):
                    raise InvalidResource('x-cpn-many exige resposta array.')
                source_events = [replace(event, response_body=item) for item in event.response_body]
            for source_event in source_events:
                try:
                    value = expression(link.expr, source_event, legacy=legacy)
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise InvalidResource(f'Não foi possível obter o recurso do link {link.expr}') from exc
                value = link.field.coerce(value)
                if links2cpn_compatible:
                    value = str(value)
                elif not Draft202012Validator(link.field.schema).is_valid(value):
                    raise InvalidResource(f'Recurso de {link.expr} incompatível com o destino {link.field.name}')
                arc = ArcBinding(link.field.place, resource_token(event.user_id, value))
                # M' = M - entradas + saídas. Cada ocorrência produz um token;
                # a expressão depende do binding/evento, nunca da marcação global.
                arcs.append(arc)
        return arcs

    def postcondition(binding, event, net):
        for observed, expected in checks.items():
            try:
                if freeze(pointer(event.response_body, observed)) != freeze(expression(expected, event, legacy=legacy)):
                    return False
            except (KeyError, IndexError, ValueError, TypeError):
                return False
        return True

    return Transition(activity, activity, bind, inputs, outputs, guard, postcondition)


def normalize_event(net: ColoredPetriNet, event: Event) -> Event:
    """Escolhe rota antes do status; preserva maiúsculas em paths e IDs."""
    parsed = urlsplit(event.path)
    path = parsed.path.rstrip('/') or '/'
    candidates = []
    for route in net.metadata.get('routes', []):
        if route['method'].upper() != event.method.upper():
            continue
        names = re.findall(r'\{([^{}]+)\}', route['path'])
        pattern = re.escape(route['path'].rstrip('/') or '/')
        for name in names:
            pattern = pattern.replace(re.escape('{' + name + '}'), '([^/]+)')
        match = re.fullmatch(pattern, path)
        if match:
            candidates.append((len(names), route, dict(zip(names, map(unquote, match.groups())))))
    if not candidates:
        return event
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise InvalidEvent(f'Rota ambígua: {event.path}')
    _, route, extracted = candidates[0]
    # Path templated (Event construído diretamente) conserva parâmetros explícitos.
    if path == route['path']:
        extracted = dict(event.path_params)
    else:
        for key, value in event.path_params.items():
            if key in extracted and str(value) != extracted[key]:
                raise InvalidEvent(f'Parâmetro explícito contradiz URL: {key}')
        extracted = {**event.path_params, **extracted}
    query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    normalized = replace(event, path=route['path'], path_params=extracted,
                         query_params={**query, **event.query_params},
                         request_headers={k.lower(): v for k, v in event.request_headers.items()},
                         response_headers={k.lower(): v for k, v in event.response_headers.items()})
    locations = {'path': dict(normalized.path_params), 'query': dict(normalized.query_params),
                 'header': dict(normalized.request_headers)}
    for parameter in route['parameters']:
        location, name = parameter['location'], parameter['name']
        if location == 'header':
            name = name.lower()
        values = locations.get(location, {})
        if name in values:
            values[name] = Field('', location, name, parameter['schema']).coerce(values[name])
    normalized = replace(normalized, path_params=locations['path'], query_params=locations['query'],
                         request_headers=locations['header'])
    return normalized


def select_event_status(net: ColoredPetriNet, event: Event) -> Event:
    """Não modifica status real: adiciona alias de seleção para 2XX/default."""
    if net.select_transition(event.activity):
        return event
    # Resposta concreta documentada e removida não deve cair em default.
    if event.activity in net.metadata.get('omitted_activities', []):
        return event
    for status in (f'{event.status_code // 100}XX', 'DEFAULT'):
        name = activity_name(event.method, event.path, status)
        if name in net.activity_index:
            net.activity_index[event.activity] = net.activity_index[name]
            break
        if name in net.metadata.get('omitted_activities', []):
            break
    return event
