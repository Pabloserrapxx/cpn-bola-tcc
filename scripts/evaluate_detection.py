"""Etapa 4: avaliação HTTP rotulada, sem inferir gabarito pelas decisões da CPN."""
from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPConnection
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
from cpn_detection import aggregate, prediction
from cpn_export import jsonable, write_json
from cpn_incremental import ReplaySession
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn
from scripts.detection_lab import OWNERS, cases, make_server, specification
from scripts.feed_events import equivalent


def call(port, method, path, *, body=None, token=None, case_id=None):
    connection = HTTPConnection('127.0.0.1', port, timeout=10)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if case_id:
        headers['X-Evaluation-Id'] = case_id
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def offline_decisions(model, traces):
    decisions, consistent = {}, True
    for trace in traces:
        offline = replay_trace(model, model.initial_places, trace,
            unknown_policy='skip', continue_after_divergence=True, record_steps=True)
        failures = {d.event_index: d for d in offline.divergences}
        fired = {s['event_index'] for s in offline.steps}
        session = ReplaySession(model, trace.trace_id, trace.user_id,
            unknown_policy='skip', continue_after_divergence=True)
        for index, event in enumerate(trace.events):
            result = session.process(event)
            row = dict(status='divergent' if index in failures else
                       'fired' if index in fired else 'outside_model',
                       result=dict(divergence=jsonable(failures.get(index))))
            decisions[event.source_index] = dict(offline=row,
                incremental=dict(status=result.status, result=jsonable(result)))
            consistent = consistent and row['status'] == result.status
        consistent = consistent and equivalent(offline, session.finish())
    return decisions, consistent


def run(output, repetitions):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan = cases(repetitions)
    spec = specification()
    # O gabarito e o modelo são fixados em disco antes da primeira requisição.
    write_json(output / 'gabarito.json', dict(
        ownership=OWNERS, shared_read=dict(account=12, reader='alice'), cases=plan,
        unit='Requisição de acesso/alteração de objeto; login é preparação.',
        rule='Tentativa BOLA = operação não autorizada pela ACL do laboratório, mesmo se negada pela API.'))
    write_json(output / 'modelo.openapi.json', spec)
    api = make_server()
    thread = Thread(target=api.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    observations = {}
    try:
        with tempfile.TemporaryDirectory(prefix='cpn-detection-') as temporary:
            temporary = Path(temporary)
            monitor_dir = temporary / 'monitor'
            with (temporary / 'proxy.log').open('w+') as console:
                process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/run_proxy.py'),
                    str((output / 'modelo.openapi.json').resolve()), '--upstream',
                    f'http://127.0.0.1:{api.server_port}', '--port', str(port),
                    '--max-sessions', str(len(plan) + 1), '--output', str(monitor_dir),
                    '--capture-events'], stdout=console, stderr=console)
                try:
                    deadline = monotonic() + 20
                    while True:
                        if process.poll() is not None:
                            console.seek(0)
                            raise RuntimeError('Proxy encerrou: ' + console.read())
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                                break
                        except OSError:
                            if monotonic() >= deadline:
                                raise RuntimeError('Timeout iniciando proxy.')
                            sleep(.05)
                    for case in plan:
                        prep_port = api.server_port if case['context'] == 'login_direct' else port
                        status, login = call(prep_port, 'POST', '/login',
                            body=dict(user=case['user'], session=case['case_id']))
                        if status != 200:
                            raise RuntimeError('Login de preparação falhou.')
                        token = login['authentication']['token']
                        note_before = api.notes[case['target']]
                        body = dict(note=case['case_id']) if case['method'] == 'PATCH' else None
                        status, response = call(port, case['method'], case['path'],
                            body=body, token=token, case_id=case['case_id'])
                        effect = (status == 200 and response.get('id') == case['target']
                            and (case['method'] != 'PATCH' or api.notes[case['target']] == case['case_id']))
                        denied_preserved = (case['method'] != 'PATCH' or status != 403
                                            or api.notes[case['target']] == note_before)
                        observations[case['case_id']] = dict(http_status=status,
                            exploitation_succeeded=effect if case['expected_bola'] else None,
                            fixture_valid=(status == case['expected_http'] and denied_preserved
                                           and (effect if status == 200 else True)))
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            runtime = [json.loads(line) for line in (monitor_dir / 'monitor.jsonl').read_text().splitlines()]
            captured = [json.loads(line) for line in
                        (monitor_dir / 'eventos_capturados.jsonl').read_text().splitlines()]
            by_event = {r['event_id']: r for r in runtime if 'event_id' in r}
            by_case = {r['request_headers']['x-evaluation-id']: r for r in captured
                       if 'x-evaluation-id' in r['request_headers']}
            model = build_openapi_cpn(spec)
            decisions, offline_incremental_equal = offline_decisions(
                model, load_traces(monitor_dir / 'eventos_capturados.jsonl'))
            rows = []
            for case in plan:
                capture = by_case.get(case['case_id'])
                actual = by_event.get(capture['source_index']) if capture else None
                row = dict(**case, **observations[case['case_id']],
                           runtime_status=actual['status'] if actual else 'unavailable',
                           predicted_bola=prediction(actual or {}),
                           elapsed_ns=actual['result']['elapsed_ns'] if actual and 'result' in actual else None)
                if capture:
                    pair = decisions[capture['source_index']]
                    row['offline_predicted_bola'] = prediction(pair['offline'])
                    row['incremental_predicted_bola'] = prediction(pair['incremental'])
                    row['modes_agree'] = (row['predicted_bola'] == row['offline_predicted_bola'] ==
                        row['incremental_predicted_bola'] and
                        row['runtime_status'] == pair['offline']['status'] == pair['incremental']['status'])
                else:
                    row.update(offline_predicted_bola=None, incremental_predicted_bola=None, modes_agree=False)
                rows.append(row)
            counts = json.loads((monitor_dir / 'resultado.json').read_text())['counts']
    finally:
        api.shutdown()
        api.server_close()
        thread.join(timeout=5)
    classified = aggregate(rows)
    core = [r for r in rows if r['scenario'] not in ('lab_shared_read', 'lab_missing_context')]
    success = (all(r['fixture_valid'] and r['modes_agree'] for r in rows)
               and offline_incremental_equal and counts['capture_error'] == counts['transport_error'] == 0)
    report = dict(execution_valid=success, python=sys.version.split()[0],
        mitmproxy=metadata.version('mitmproxy'), repetitions=repetitions,
        alert_policy='Somente divergências de fluxo de controle/dados são alertas BOLA; '
            'outros resultados são não classificados. Login não entra na matriz.',
        metrics=classified, core_metrics=aggregate(core),
        by_mode={mode: aggregate([dict(r, predicted_bola=r[key]) for r in rows])
            for mode, key in (('runtime', 'predicted_bola'),
                              ('offline', 'offline_predicted_bola'),
                              ('incremental', 'incremental_predicted_bola'))},
        attack_outcomes=dict(
            successful=sum(r['exploitation_succeeded'] is True for r in rows),
            denied=sum(r['expected_bola'] and r['http_status'] == 403 for r in rows)),
        runtime_counts=counts,
        offline_incremental_equal=offline_incremental_equal,
        runtime_modes_equal=all(r['modes_agree'] for r in rows),
        scope='API HTTP local de laboratório: leitura e alteração, rotas vulneráveis e protegidas, '
              'compartilhamento fora do modelo e contexto inicial ausente. Não é Juice Shop/Memos. '
              'Repetições determinísticas não são amostras independentes nem estimativa de generalização.',
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'cpn_detection.py', ROOT/'scripts/detection_lab.py',
                      ROOT/'scripts/evaluate_detection.py', ROOT/'cpn_runtime.py',
                      ROOT/'cpn_incremental.py', ROOT/'cpn_bola.py', ROOT/'cpn_openapi.py',
                      ROOT/'runtime/mitmproxy_addon.py']},
        input_sha256={name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                      for name in ('gabarito.json', 'modelo.openapi.json')})
    write_json(output / 'resultado.json', report)
    with (output / 'eventos_rotulados.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    lines = ['# Avaliação de detecção — laboratório', '',
        'Unidade: requisição de objeto. Login é preparação. Tentativas negadas continuam rotuladas como ataques.', '',
        '| Cenário | N | TP | FP | FN | TN | Cobertura | Precisão | Recall | F1 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    def fmt(value):
        return f'{value:.3f}' if value is not None else 'indefinido'
    for name, values in classified['by_scenario'].items():
        lines.append(f"| {name} | {values['total']} | {values['TP']} | {values['FP']} | {values['FN']} | {values['TN']} | "
                     + ' | '.join(fmt(values[k]) for k in ('coverage', 'precision', 'recall', 'f1')) + ' |')
    lines += ['', 'Os controles de compartilhamento e contexto ausente expõem limitações do modelo. '
              'Não são resultados de Juice Shop/Memos nem evidência de desempenho sob carga.', '']
    (output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'docs/avaliacao_deteccao'))
    parser.add_argument('--repetitions', type=int, default=10)
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error('--repetitions deve ser positivo.')
    try:
        report = run(args.output, args.repetitions)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report['metrics']['overall'], ensure_ascii=False))
    print(f"Execução válida={report['execution_valid']}; relatório em {args.output}")
    return 0 if report['execution_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
