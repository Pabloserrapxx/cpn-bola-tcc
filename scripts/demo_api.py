"""API de laboratório deliberadamente vulnerável a BOLA; apenas localhost.

POST /login {"user":"alice"|"bob"} -> ID e token sintético.
GET /accounts/{id} retorna qualquer conta para qualquer token conhecido.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

USERS = {'alice': 6, 'bob': 9}


class DemoHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Demo-API', 'cpn-lab')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size < 0 or size > 4096:
                self.close_connection = True
                return self.send_json(413, {'error': 'body_limit'})
            body = json.loads(self.rfile.read(size))
        except (ValueError, TypeError):
            return self.send_json(400, {'error': 'invalid_json'})
        user = body.get('user') if isinstance(body, dict) else None
        if self.path != '/login':
            return self.send_json(404, {'error': 'not_found'})
        if user not in USERS:
            return self.send_json(401, {'error': 'unknown_user'})
        self.send_json(200, {'authentication': {'id': USERS[user], 'token': 'lab-' + user}})

    def do_GET(self):
        if self.path == '/health':
            return self.send_json(200, {'status': 'ok'})
        if self.headers.get('Authorization') not in ('Bearer lab-alice', 'Bearer lab-bob'):
            return self.send_json(401, {'error': 'unauthorized'})
        try:
            path = self.path.split('?', 1)[0]
            if not path.startswith('/accounts/'):
                raise ValueError()
            account = int(path.removeprefix('/accounts/'))
        except ValueError:
            return self.send_json(404, {'error': 'not_found'})
        if account not in USERS.values():
            return self.send_json(404, {'error': 'not_found'})
        # Vulnerabilidade intencional: o proprietário não é verificado.
        self.send_json(200, {'id': account})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=3000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), DemoHandler)
    print(f'API de laboratório em http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
