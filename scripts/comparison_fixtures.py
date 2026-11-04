"""Entradas controladas autorais: o MESMO OpenAPI e log alimenta os dois programas."""
from copy import deepcopy
import json
from pathlib import Path


def make(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    string = {'type': 'string'}
    def schema(props):
        return {'type': 'object', 'properties': props}
    def content(s):
        return {'content': {'application/json': {'schema': s}}}
    def response(s=None, **extra):
        return {'description': 'Controlled response', **(content(s) if s else {}), **extra}
    def event(method, uri, response_body=None, request_body=None, user='alice'):
        return dict(method=method, uri=uri, statusCode=200, ip=user,
                    message=f'{method} {uri} 200', requestBody=request_body or {},
                    responseBody=response_body if response_body is not None else {})
    def document(paths):
        return {'openapi': '3.0.3', 'info': {'title': 'Controlled comparison', 'version': '1.0.0'}, 'paths': paths}
    def parameter(name):
        return {'name': name, 'in': 'path', 'required': True, 'schema': string}
    login = lambda n='6', user='alice': event('POST', '/login', {'authentication': {'id': n}}, user=user)
    get = lambda n='6', user='alice', observed=None: event('GET', '/accounts/'+n, {'id': observed or n}, user=user)
    account = document({
        '/login': {'post': {'operationId': 'login', 'responses': {'200': response(
            schema({'authentication': schema({'id': string})}),
            links={'account': {'operationId': 'getAccount', 'parameters': {'id': '$response.body#/authentication.id'}}})}}},
        '/accounts/{id}': {'get': {'operationId': 'getAccount', 'parameters': [parameter('id')],
                                 'responses': {'200': response(schema({'id': string}))}}}})
    cases = []
    def write(name, spec, logs, purpose):
        spec_path, log_path = directory / (name+'.openapi.json'), directory / (name+'.logs.json')
        for path, value in [(spec_path, spec), (log_path, logs)]:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        cases.append(dict(name=name, spec=str(spec_path), logs=str(log_path), purpose=purpose, dataset='controlled'))
    write('normal', account, [login(), get(), get()], 'Consulta legítima repetida')
    write('sem-produtor', account, [get()], 'Operação sem recurso prévio')
    write('id-alheio', account, [login(), get('9')], 'Adulteração do identificador')
    write('apos-erro', account, [login(), get('9'), get(), login()], 'Continuação depois do primeiro erro')
    write('usuarios', account, [login(), login('9','bob'), get(), get('9','bob')], 'Isolamento por identidade')
    write('multiplicidade', account, [login(), login(), get()], 'Emissão duplicada e leitura persistente')
    post = deepcopy(account)
    post['paths']['/accounts/{id}']['get']['responses']['200']['x-cpn-equals'] = {'/id': '$request.path.id'}
    write('pos-condicao', post, [login(), get(observed='9')], 'Pós-condição explícita, ignorada pelo original')
    standard = deepcopy(account)
    standard['paths']['/login']['post']['responses']['200']['links']['account']['parameters']['id'] = '$response.body#/authentication/id'
    write('json-pointer', standard, [login(), get()], 'JSON Pointer padrão versus notação pontuada histórica')

    basket = document({
        '/login': {'post': {'operationId': 'login', 'responses': {'200': response(links={
            'items': {'operationId': 'addItem', 'requestBody': {'BasketId': '$response.body#/authentication.id'}}})}}},
        '/items': {'post': {'operationId': 'addItem', 'requestBody': content(schema({
            'BasketId': string, 'quantity': {'type': 'integer', 'minimum': 1}})),
            'responses': {'200': response()}}}})
    write('guarda', basket, [login(), event('POST','/items',request_body={'BasketId':'6','quantity':0})],
          'Guarda de schema, ausente no original')

    memo = document({
        '/memos': {'get': {'operationId': 'listMemos', 'responses': {'200': response(
            {'type':'array','items':schema({'id':string})}, links={
                'patch': {'operationId':'patchMemo','parameters':{'id':'$response.body#/id'},'x-cpn-many':True}})}}},
        '/memos/{id}': {'patch': {'operationId':'patchMemo','parameters':[parameter('id')],
                                'responses':{'200':response()}}}})
    patch = event('PATCH','/memos/4')
    write('lista-dois',memo,[event('GET','/memos',[{'id':'4'},{'id':'6'}]),patch],'Expansão de array com dois itens')
    write('lista-um',memo,[event('GET','/memos',[{'id':'4'}]),patch],'Expansão de array com um item')
    write('lista-vazia',memo,[event('GET','/memos',[])],'Resposta array vazia')
    branches = deepcopy(memo)
    branches['paths']['/other/{id}'] = {'patch':{'operationId':'other','parameters':[parameter('id')],
                                              'responses':{'200':response()}}}
    branches['paths']['/memos']['get']['responses']['200']['links']['other'] = {
        'operationId':'other','parameters':{'id':'$response.body#/id'},'x-cpn-many':True}
    write('lista-dois-destinos',branches,[event('GET','/memos',[{'id':'4'},{'id':'6'}]),event('PATCH','/other/4')],
          'Dois links de saída após expansão do array')
    return cases
