"""Provisionamento e ciclo de vida dos alvos locais reais da etapa 4."""
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
MEMOS_COMMIT = 'ee5f617ea815ea079a41dde2f53193250a22c36a'
MEMOS_REPO = 'https://github.com/ailton07/memos-with-BOLA.git'
JUICE_IMAGE = 'bkimminich/juice-shop@sha256:b3449813580486f9f1087fba1c08f730df500a53abc394f61653cf716d467513'
GO_IMAGE = 'golang@sha256:2414035b086e3c42b99654c8b26e6f5b1b1598080d65fd03c7f499552ff4dc94'


def command(args):
    return subprocess.check_output(args, text=True).strip()


def prepare():
    command(['docker', 'info', '--format', '{{.ServerVersion}}'])
    subprocess.run(['docker', 'pull', JUICE_IMAGE], check=True)
    subprocess.run(['docker', 'pull', GO_IMAGE], check=True)
    checkout = ROOT / '.runtime-apps/memos'
    checkout.parent.mkdir(parents=True, exist_ok=True)
    if not checkout.exists():
        subprocess.run(['git', 'clone', '--no-checkout', MEMOS_REPO, str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'checkout', '--detach', MEMOS_COMMIT], check=True)
    if command(['git', '-C', str(checkout), 'rev-parse', 'HEAD']) != MEMOS_COMMIT:
        raise ValueError('Checkout Memos não está no commit fixado; nenhum arquivo foi alterado.')
    if command(['git', '-C', str(checkout), 'diff', 'HEAD', '--']):
        raise ValueError('Código rastreado do Memos foi alterado.')
    subprocess.run(['docker', 'run', '--rm', '-v', f'{checkout}:/src', '-w', '/src',
        '-e', 'CGO_ENABLED=0', GO_IMAGE, 'go', 'build', '-o', 'memos-tcc', '.'], check=True)


@contextmanager
def application(name):
    args = ['docker', 'run', '-d', '--rm', '-p']
    if name == 'juice':
        args += ['127.0.0.1::3000', JUICE_IMAGE]
        internal_port = '3000'
    elif name == 'memos':
        checkout = ROOT / '.runtime-apps/memos'
        if not (checkout / 'memos-tcc').exists():
            raise ValueError('Execute scripts/setup_real_apps.py antes da avaliação.')
        if command(['git', '-C', str(checkout), 'rev-parse', 'HEAD']) != MEMOS_COMMIT:
            raise ValueError('Commit do Memos não corresponde à referência fixada.')
        if command(['git', '-C', str(checkout), 'diff', 'HEAD', '--']):
            raise ValueError('Checkout Memos contém alterações rastreadas.')
        args += ['127.0.0.1::5230', '-v', f'{checkout}:/src:ro',
                 '--tmpfs', '/data', '-w', '/tmp', GO_IMAGE, '/src/memos-tcc',
                 '--mode', 'dev', '--data', '/data', '--port', '5230']
        internal_port = '5230'
    else:
        raise ValueError('Aplicação desconhecida.')
    container = command(args)
    try:
        port = int(command(['docker', 'port', container, internal_port]).rsplit(':', 1)[1])
        deadline = monotonic() + 60
        while True:
            try:
                connection = HTTPConnection('127.0.0.1', port, timeout=1)
                try:
                    connection.request('GET', '/api/v1/status' if name == 'memos'
                                       else '/rest/admin/application-version')
                    response = connection.getresponse()
                    payload = response.read()
                    if response.status != 200:
                        raise OSError(f'Aplicação ainda indisponível: HTTP {response.status}')
                    ready_payload = json.loads(payload)
                    break
                finally:
                    connection.close()
            except (OSError, HTTPException, ValueError):
                if monotonic() >= deadline or command(['docker', 'inspect', '-f', '{{.State.Running}}', container]) != 'true':
                    raise RuntimeError('Aplicação não iniciou: ' + command(['docker', 'logs', container]))
                sleep(.1)
        info = json.loads(command(['docker', 'inspect', container]))[0]
        yield port, dict(host_pid=info['State']['Pid'], image_id=info['Image'], image_reference=info['Config']['Image'],
                         memos_commit=MEMOS_COMMIT if name == 'memos' else None,
                         app_version=(ready_payload.get('profile', {}).get('version') or
                                      ready_payload.get('version')),
                         mode='dev' if name == 'memos' else 'default')
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', container], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


@contextmanager
def proxy(spec, upstream_port, directory, identity_header=None):
    directory = Path(directory)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = [sys.executable, str(ROOT / 'scripts/run_proxy.py'), str(spec),
            '--upstream', f'http://127.0.0.1:{upstream_port}', '--port', str(port),
            '--output', str(directory / 'monitor'), '--capture-events']
    if identity_header:
        args += ['--identity-header', identity_header]
    with (directory / 'proxy.log').open('w+') as console:
        process = subprocess.Popen(args, stdout=console, stderr=console)
        try:
            deadline = monotonic() + 20
            while True:
                if process.poll() is not None:
                    console.seek(0)
                    raise RuntimeError('Proxy não iniciou: ' + console.read())
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    if monotonic() >= deadline:
                        raise RuntimeError('Timeout iniciando proxy.')
                    sleep(.05)
            yield port
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
