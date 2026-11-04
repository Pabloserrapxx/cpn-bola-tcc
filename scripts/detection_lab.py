"""API e gabarito ACL de laboratório para avaliar tentativas de BOLA.

A política usa proprietários/compartilhamento definidos fora da CPN. As rotas
/accounts são vulneráveis; /enforced/accounts aplicam a política de autorização.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

OWNERS = {6: 'alice', 9: 'bob', 12: 'bob'}
LOGIN_ACCOUNTS = {'alice': 6, 'bob': 9}


def authorized(user, method, account):
    return OWNERS.get(account) == user or (method == 'GET' and account == 12 and user == 'alice')


def specification():
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / 'examples/contas.openapi.json').read_text())
    spec['info']['title'] = 'Laboratório de avaliação BOLA — leitura e alteração'
    get = spec['paths']['/accounts/{id}']['get']
    get['operationId'] = 'readAccount'
    for prefix, suffix in (('/accounts/{id}', ''), ('/enforced/accounts/{id}', 'Enforced')):
        spec['paths'][prefix] = {}
        for method, operation in (('get', 'readAccount'), ('patch', 'writeAccount')):
            op = deepcopy(get)
            op['operationId'] = operation + suffix
            if method == 'patch':
                op['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                    'type': 'object', 'properties': {'note': {'type': 'string'}}, 'required': ['note']}}}}
            if suffix:
                op['responses']['403'] = {
                    'description': 'Acesso negado pela política da API',
                    'content': {'application/json': {'schema': {'type': 'object'}}}}
            spec['paths'][prefix][method] = op
    links = spec['paths']['/login']['post']['responses']['200']['links']
    links.clear()
    for operation in ('readAccount', 'writeAccount', 'readAccountEnforced', 'writeAccountEnforced'):
        links[operation] = {'operationId': operation,
                            'parameters': {'id': '$response.body#/authentication/id'}}
    # Compartilhamento de conta 12 é permitido pela API, mas não está representado
    # nos Links. Isso permite medir falsos positivos por modelo incompleto.
    return spec


def cases(repetitions):
    if repetitions < 1:
        raise ValueError('Repetições devem ser positivas.')
    result = []
    for repetition in range(repetitions):
        for scenario, method, enforced in (
                ('lab_read', 'GET', False), ('lab_write', 'PATCH', False),
                ('lab_read_denied', 'GET', True), ('lab_write_denied', 'PATCH', True)):
            for user, own, other in (('alice', 6, 9), ('bob', 9, 6)):
                for target in (own, other):
                    result.append(dict(case_id=f'{scenario}-{repetition}-{user}-{target}',
                        scenario=scenario, repetition=repetition, user=user, method=method,
                        target=target, path=('/enforced' if enforced else '') + f'/accounts/{target}',
                        expected_bola=not authorized(user, method, target),
                        expected_http=200 if not enforced or authorized(user, method, target) else 403,
                        context='login_observed'))
        # Controles de limitação: acesso legítimo compartilhado e login não capturado.
        result.append(dict(case_id=f'lab_shared_read-{repetition}', scenario='lab_shared_read',
            repetition=repetition, user='alice', method='GET', target=12, path='/accounts/12',
            expected_bola=False, expected_http=200, context='login_observed'))
        result.append(dict(case_id=f'lab_missing_context-{repetition}', scenario='lab_missing_context',
            repetition=repetition, user='alice', method='GET', target=6, path='/accounts/6',
            expected_bola=False, expected_http=200, context='login_direct'))
    return result


class LabHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send_json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        size = int(self.headers.get('Content-Length', 0))
        if not 0 <= size <= 4096:
            self.close_connection = True
            raise ValueError('body_limit')
        return json.loads(self.rfile.read(size))

    def do_POST(self):
        try:
            body = self.read_json()
            user, session = body['user'], body['session']
            if self.path != '/login' or user not in LOGIN_ACCOUNTS or not isinstance(session, str):
                raise ValueError('login')
        except (ValueError, KeyError, TypeError):
            return self.send_json(400, {'error': 'invalid_login'})
        token = f'lab-{user}-{session}'
        self.server.tokens[token] = user
        self.send_json(200, {'authentication': {'id': LOGIN_ACCOUNTS[user], 'token': token}})

    def account(self, method):
        token = self.headers.get('Authorization', '').removeprefix('Bearer ')
        user = self.server.tokens.get(token)
        if not user:
            return self.send_json(401, {'error': 'unauthorized'})
        try:
            note = self.read_json()['note'] if method == 'PATCH' else None
            path = self.path.split('?', 1)[0]
            enforced = path.startswith('/enforced/')
            prefix = '/enforced/accounts/' if enforced else '/accounts/'
            if not path.startswith(prefix):
                raise ValueError('path')
            account = int(path.removeprefix(prefix))
        except (ValueError, KeyError, TypeError):
            return self.send_json(400, {'error': 'invalid_request'})
        if account not in OWNERS:
            return self.send_json(404, {'error': 'not_found'})
        if enforced and not authorized(user, method, account):
            return self.send_json(403, {'error': 'forbidden'})
        if method == 'PATCH':
            self.server.notes[account] = note
        return self.send_json(200, {'id': account, 'note': self.server.notes[account]})

    def do_GET(self):
        self.account('GET')

    def do_PATCH(self):
        self.account('PATCH')


def make_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), LabHandler)
    server.tokens = {}
    server.notes = {account: '' for account in OWNERS}
    return server
