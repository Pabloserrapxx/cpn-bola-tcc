"""Gera especificações e logs SINTÉTICOS autorais inspirados nos cenários da tese."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': required or list(properties)}


ID = {'type': 'integer', 'minimum': 1}
TEXT = {'type': 'string'}


def body(schema):
    return {'content': {'application/json': {'schema': schema}}}


def response(schema, links=None, equals=None):
    value = {'description': 'Resposta modelada', **body(schema)}
    if links:
        value['links'] = links
    if equals:
        value['x-cpn-equals'] = equals
    return value


def param(name):
    return {'in': 'path', 'name': name, 'required': True, 'schema': ID}


def document(title, paths):
    return {'openapi': '3.1.0', 'info': {'title': title, 'version': '1.0.0'}, 'paths': paths}


def event(method, uri, request=None, response=None, status=200):
    return {'method': method, 'uri': uri, 'statusCode': status,
            'requestBody': request or {}, 'responseBody': response if response is not None else {}}


def trace(name, events, user='alice'):
    return {'trace_id': name, 'user_id': user, 'events': events}


def write(name, spec, traces):
    for suffix, value in [('openapi', spec), ('logs', traces)]:
        (ROOT / 'examples' / f'{name}.{suffix}.json').write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    login_schema = obj({'authentication': obj({'id': ID})})
    account = document('Contas — exemplo da tese', {
        '/login': {'post': {'operationId': 'login', 'responses': {'200': response(login_schema, {
            'account': {'operationId': 'getAccount', 'parameters': {'id': '$response.body#/authentication/id'}}})}}},
        '/accounts/{id}': {'get': {'operationId': 'getAccount', 'parameters': [param('id')],
                                 'responses': {'200': response(obj({'id': ID}), equals={'/id': '$request.path.id'})}}}
    })
    login = event('POST', '/login', response={'authentication': {'id': 6}})
    own = event('GET', '/accounts/6', response={'id': 6})
    write('contas', account, [trace('legitimo', [login, own, own]),
          trace('bola-id-alheio', [login, event('GET', '/accounts/9', response={'id': 9})]),
          trace('fora-de-ordem', [own]),
          trace('resposta-corrompida', [login, event('GET', '/accounts/6', response={'id': 9})])])

    for manipulate in (False, True):
        name = 'juice_manipulate' if manipulate else 'juice_view'
        target, method = ('/api/BasketItems', 'post') if manipulate else ('/rest/basket/{id}', 'get')
        mapping = {'requestBody': {'BasketId': '$response.body#/authentication/bid'}} if manipulate else {
            'parameters': {'id': '$response.body#/authentication/bid'}}
        target_op = {'operationId': 'basket', 'responses': {'200': response(
            obj({'data': obj({'BasketId' if manipulate else 'id': ID})}),
            equals={'/data/BasketId' if manipulate else '/data/id':
                    '$request.body#/BasketId' if manipulate else '$request.path.id'})}}
        if manipulate:
            target_op['requestBody'] = body(obj({'BasketId': ID, 'ProductId': ID,
                                                 'quantity': {'type': 'integer', 'minimum': 1}}))
        else:
            target_op['parameters'] = [param('id')]
        spec = document('Juice Shop — ' + ('Manipulate Basket' if manipulate else 'View Basket'), {
            '/rest/user/login': {'post': {'operationId': 'login', 'responses': {'200': response(
                obj({'authentication': obj({'bid': ID})}), {'basket': {'operationId': 'basket', **mapping}})}}},
            target: {method: target_op}})
        start = event('POST', '/rest/user/login', response={'authentication': {'bid': 6}})
        def access(n, quantity=1):
            return event(method.upper(), '/api/BasketItems/' if manipulate else f'/rest/basket/{n}',
                         {'BasketId': n, 'ProductId': 1, 'quantity': quantity} if manipulate else {},
                         {'data': {'BasketId' if manipulate else 'id': n}})
        traces = [trace('legitimo', [start, access(6), access(6)]),
                  trace('bola-id-alheio', [start, access(9)]),
                  trace('fora-de-ordem', [access(6)])]
        if manipulate:
            traces.append(trace('regra-quantidade', [start, access(6, 0)]))
        write(name, spec, traces)

    memo_schema = obj({'id': ID})
    memos = document('Memos — listar/criar/arquivar', {
        '/api/v1/memo': {
            'get': {'operationId': 'listMemos', 'responses': {'200': response(
                {'type': 'array', 'items': memo_schema}, {'archive': {
                    'operationId': 'patchMemo', 'parameters': {'memoId': '$response.body#/id'}, 'x-cpn-many': True}})}},
            'post': {'operationId': 'createMemo', 'requestBody': body(obj({'content': TEXT})),
                     'responses': {'200': response(memo_schema, {'archive': {
                         'operationId': 'patchMemo', 'parameters': {'memoId': '$response.body#/id'}}})}}},
        '/api/v1/memo/{memoId}': {'patch': {'operationId': 'patchMemo', 'parameters': [param('memoId')],
            'requestBody': body(obj({'rowStatus': {'type': 'string', 'enum': ['NORMAL', 'ARCHIVED']}})),
            'responses': {'200': response(obj({'id': ID, 'rowStatus': TEXT}), equals={
                '/id': '$request.path.memoId', '/rowStatus': '$request.body#/rowStatus'})}}}
    })
    def archive(n, status='ARCHIVED'):
        return event('PATCH', f'/api/v1/memo/{n}', {'rowStatus': status}, {'id': n, 'rowStatus': status})
    listing = event('GET', '/api/v1/memo?creatorUsername=alice', response=[{'id': 4}, {'id': 6}])
    create = event('POST', '/api/v1/memo', {'content': 'Nota sintética'}, {'id': 7})
    write('memos', memos, [trace('legitimo', [listing, create, archive(7), archive(4)]),
          trace('bola-id-alheio', [event('GET', '/api/v1/memo', response=[{'id': 1}, {'id': 3}]), archive(6)]),
          trace('fora-de-ordem', [archive(6)]),
          trace('regra-estado', [listing, archive(4, 'INVALIDO')]),
          trace('lista-vazia', [event('GET', '/api/v1/memo', response=[])])])


if __name__ == '__main__':
    main()
