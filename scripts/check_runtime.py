"""Experimento HTTP real: API vulnerável local -> proxy -> monitor -> replay offline."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from importlib import metadata
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cpn_bola import replay_trace
from cpn_export import jsonable, write_json
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn
from scripts.demo_api import DemoHandler
from scripts.feed_events import equivalent


def request(port, method, path, body=None, token=None):
    connection = HTTPConnection('127.0.0.1', port, timeout=10)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader('X-Demo-API')
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'docs/avaliacao_runtime.json'))
    args = parser.parse_args(argv)
    api = ThreadingHTTPServer(('127.0.0.1', 0), DemoHandler)
    thread = Thread(target=api.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        proxy_port = sock.getsockname()[1]
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix='cpn-runtime-') as directory:
            output = Path(directory) / 'monitor'
            with (Path(directory) / 'proxy.log').open('w+') as console:
                process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_proxy.py'),
                    str(ROOT / 'examples/contas.openapi.json'), '--upstream',
                    f'http://127.0.0.1:{api.server_port}', '--port', str(proxy_port),
                    '--output', str(output), '--capture-events'], stdout=console, stderr=console)
                try:
                    deadline = monotonic() + 20
                    while True:
                        if process.poll() is not None:
                            console.seek(0)
                            raise RuntimeError('Proxy encerrou: ' + console.read())
                        try:
                            with socket.create_connection(('127.0.0.1', proxy_port), timeout=.1):
                                break
                        except OSError:
                            if monotonic() >= deadline:
                                raise RuntimeError('Timeout iniciando proxy.')
                            sleep(.05)
                    calls = [
                        ('alice-login', 'POST', '/login', {'user': 'alice'}, None),
                        ('alice-own', 'GET', '/accounts/6', None, 'lab-alice'),
                        ('bob-login', 'POST', '/login', {'user': 'bob'}, None),
                        ('bob-own', 'GET', '/accounts/9', None, 'lab-bob'),
                        ('alice-bola', 'GET', '/accounts/9', None, 'lab-alice'),
                        ('alice-after-alert', 'GET', '/accounts/6', None, 'lab-alice'),
                        ('outside-model', 'GET', '/health', None, 'lab-alice'),
                    ]
                    for name, method, path, body, token in calls:
                        direct = request(api.server_port, method, path, body, token)
                        proxied = request(proxy_port, method, path, body, token)
                        rows.append(dict(name=name, status_code=proxied[0],
                                         response_preserved=direct == proxied))
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            summary = json.loads((output / 'resultado.json').read_text())
            events = [json.loads(line) for line in (output / 'monitor.jsonl').read_text().splitlines()]
            statuses = [r['status'] for r in events]
            expected = ['fired', 'fired', 'fired', 'fired', 'divergent', 'fired', 'outside_model']
            model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
            captured_rows = [json.loads(line) for line in
                (output / 'eventos_capturados.jsonl').read_text().splitlines()]
            traces = load_traces(output / 'eventos_capturados.jsonl')
            # Compara cada prefixo HTTP capturado com referência offline independente.
            from cpn_incremental import ReplaySession
            comparisons = []
            for trace in traces:
                session = ReplaySession(model, trace.trace_id, trace.user_id,
                    unknown_policy='skip', continue_after_divergence=True)
                from scripts.feed_events import session_result
                for index, event in enumerate(trace.events):
                    result = session.process(event)
                    offline = replay_trace(model, model.initial_places,
                        type(trace)(trace.trace_id, trace.user_id, trace.events[:index+1]),
                        unknown_policy='skip', continue_after_divergence=True)
                    comparison = equivalent(offline, session_result(session))
                    runtime_row = next(r for r in events if r.get('trace_id') == trace.trace_id
                                       and r['result']['event_index'] == index)
                    captured = next(r for r in captured_rows
                        if r['source_index'] == runtime_row['event_id'])
                    def canonical(marking):
                        return {p: Counter(json.dumps(t, sort_keys=True) for t in tokens)
                                for p, tokens in marking.items()}
                    comparison = (comparison and runtime_row['result']['status'] == result.status
                        and canonical(captured['runtime_marking']) ==
                            canonical(jsonable(offline.final_marking)))
                    comparisons.append(dict(trace_id=trace.trace_id, event_index=index,
                                            equivalent=comparison))
                runtime_final = next(r for r in summary['results'] if r['trace_id'] == trace.trace_id)
                comparison = runtime_final == jsonable(session.finish())
                comparisons.append(dict(trace_id=trace.trace_id, final_equivalent=comparison,
                                        equivalent=comparison))
            passed = (statuses == expected and all(r['response_preserved'] for r in rows)
                and all(c['equivalent'] for c in comparisons)
                and summary['counts']['capture_error'] == 0
                and summary['counts']['transport_error'] == 0)
            report = dict(passed=passed, python=sys.version.split()[0],
                mitmproxy=metadata.version('mitmproxy'), calls=rows, expected_statuses=expected,
                actual_statuses=statuses, counts=summary['counts'], comparisons=comparisons,
                source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [ROOT/'cpn_runtime.py', ROOT/'runtime/mitmproxy_addon.py',
                              ROOT/'scripts/run_proxy.py', ROOT/'scripts/demo_api.py',
                              ROOT/'scripts/check_runtime.py', ROOT/'cpn_incremental.py',
                              ROOT/'examples/contas.openapi.json']},
                scope='HTTP real em localhost, API de laboratório deliberadamente vulnerável. '
                      'Não executa Juice Shop/Memos nem avalia carga. Preservação compara status, '
                      'bytes do corpo e cabeçalho X-Demo-API. Captura bruta temporária descartada.')
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, report)
            print(f'Runtime: sucesso={passed}; {dict(Counter(statuses))}; relatório em {path}')
            return 0 if passed else 1
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
