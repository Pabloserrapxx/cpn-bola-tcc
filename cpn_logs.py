"""Logs JSON/JSONL da seção 5.3 e traces explícitos, sem inferir identidade por IP."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path

from cpn_bola import Event, InvalidEvent, Trace


def _body(value, field, index, *, strict=True):
    if value is None or value == '':
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError as exc:
            if not strict:
                return value
            raise InvalidEvent(f'Registro {index}: {field} não contém JSON válido.') from exc
    return value


def _headers(value):
    if not isinstance(value, dict):
        raise InvalidEvent('Cabeçalhos devem ser um objeto.')
    return {k.lower(): v for k, v in value.items()}


def read_records(path):
    text = Path(path).read_text(encoding='utf-8')
    if not text.strip():
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = []
        for line, raw in enumerate(text.splitlines(), 1):
            if raw.strip():
                try:
                    value.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    raise InvalidEvent(f'JSON inválido na linha {line}: {exc.msg}') from exc
    return value if isinstance(value, list) else [value]


def event_from_record(record, index, identity_field='user_id', default_user=None, *, strict_bodies=True):
    if not isinstance(record, dict):
        raise InvalidEvent(f'Registro {index}: esperado objeto.')
    response = _body(record.get('response_body', record.get('responseBody', {})),
                     'responseBody', index, strict=strict_bodies)
    user = record.get(identity_field, default_user)
    if identity_field == 'headerAuthorization':
        if not user and isinstance(response, dict):
            auth = response.get('authentication', {})
            user = auth.get('token') if isinstance(auth, dict) else None
        if user:
            # Identificador determinístico; não incluir a credencial no relatório.
            user = 'auth:' + hashlib.sha256(str(user).removeprefix('Bearer ').encode()).hexdigest()[:24]
    if user is None or str(user).strip() == '':
        raise InvalidEvent(f'Registro {index}: identidade {identity_field!r} ausente. '
                           'Escolha explicitamente --identity-field ip para logs históricos por IP.')
    method = record.get('method')
    path = record.get('path', record.get('uri'))
    status = record.get('status_code', record.get('statusCode'))
    if not isinstance(method, str) or not isinstance(path, str) or not path:
        raise InvalidEvent(f'Registro {index}: method/path ausentes.')
    if type(status) is not int or not 100 <= status <= 599:
        raise InvalidEvent(f'Registro {index}: statusCode deve ser inteiro entre 100 e 599.')
    path_params = record.get('path_params', {})
    query_params = record.get('query_params', {})
    if not isinstance(path_params, dict) or not isinstance(query_params, dict):
        raise InvalidEvent(f'Registro {index}: parâmetros devem ser objetos.')
    request_headers = _headers(record.get('request_headers', record.get('requestHeaders', {})))
    if record.get('headerAuthorization'):
        request_headers.setdefault('authorization', record['headerAuthorization'])
    return Event(method=method.upper(), path=path, status_code=status, user_id=str(user),
                 path_params=path_params, query_params=query_params,
                 request_body=_body(record.get('request_body', record.get('requestBody', {})),
                                    'requestBody', index, strict=strict_bodies),
                 response_body=response,
                 request_headers=request_headers,
                 response_headers=_headers(record.get('response_headers', record.get('responseHeaders', {}))),
                 source_index=index)


def load_traces(path, *, identity_field='user_id', strict_bodies=True):
    """Mantém ordem do arquivo. Usa trace_id se presente, senão agrupa por identidade.

    Identidades diferentes jamais compartilham um trace, mesmo com trace_id igual.
    Para separar sessões do mesmo usuário, o coletor deve fornecer trace_id distintos.
    """
    records = read_records(path)
    groups = OrderedDict()
    owners = {}
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise InvalidEvent(f'Registro {index}: esperado objeto.')
        if 'events' in record:
            trace_id = str(record.get('trace_id', f'trace-{index}'))
            if trace_id in groups:
                raise InvalidEvent(f'trace_id duplicado: {trace_id}')
            user = record.get('user_id')
            if not isinstance(record['events'], list) or not user:
                raise InvalidEvent(f'Trace {trace_id}: user_id/events inválidos.')
            events = [event_from_record(e, i, identity_field, user, strict_bodies=strict_bodies)
                      for i, e in enumerate(record['events'], 1)]
            if any(e.user_id != str(user) for e in events):
                raise InvalidEvent(f'Trace {trace_id} mistura identidades.')
            groups[trace_id], owners[trace_id] = events, str(user)
        else:
            event = event_from_record(record, index, identity_field, strict_bodies=strict_bodies)
            trace_id = str(record.get('trace_id', event.user_id))
            if trace_id in owners and owners[trace_id] != event.user_id:
                raise InvalidEvent(f'Trace {trace_id} mistura identidades.')
            owners[trace_id] = event.user_id
            groups.setdefault(trace_id, []).append(event)
    return [Trace(tid, owners[tid], tuple(events)) for tid, events in groups.items()]
