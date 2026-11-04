"""Etapa 4 com Juice Shop 14.1.1 e o fork Memos do autor, ambos em localhost."""
from __future__ import annotations

import argparse
from collections import Counter
from http.client import HTTPConnection
from importlib import metadata
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cpn_bola import Trace, replay_trace
from cpn_detection import aggregate, prediction
from cpn_export import jsonable, write_json
from cpn_incremental import ReplaySession
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn
from scripts.real_apps import application, proxy, MEMOS_COMMIT, JUICE_IMAGE, GO_IMAGE

PASSWORD = 'CPN-local-test-42!'
REQUEST_INTERVAL_SECONDS = 0.05
_last_request = 0.0


def request(port, method, path, *, body=None, token=None, cookie=None,
            session=None, case_id=None, raw=None):
    global _last_request
    delay = REQUEST_INTERVAL_SECONDS - (monotonic() - _last_request)
    if delay > 0:
        sleep(delay)
    _last_request = monotonic()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if cookie:
        headers['Cookie'] = cookie
    if session:
        headers['X-CPN-Session'] = session
    if case_id:
        headers['X-Evaluation-Id'] = case_id
    connection = HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        connection.request(method, path, raw if raw is not None else
                           json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        payload = response.read()
        cookies = '; '.join(v.split(';', 1)[0] for k, v in response.getheaders()
                            if k.lower() == 'set-cookie')
        try:
            value = json.loads(payload) if payload else None
        except ValueError:
            value = None
        return response.status, value, cookies
    finally:
        connection.close()


def require(status, expected, context):
    if status not in expected:
        raise RuntimeError(f'{context}: HTTP {status}, esperado {expected}.')


def juice_model():
    spec = json.loads((ROOT / 'examples/juice_view.openapi.json').read_text())
    manipulate = json.loads((ROOT / 'examples/juice_manipulate.openapi.json').read_text())
    spec['info']['title'] = 'Juice Shop real — View e Manipulate Basket'
    spec['paths']['/rest/basket/{id}']['get']['operationId'] = 'viewBasket'
    link = spec['paths']['/rest/user/login']['post']['responses']['200']['links']['basket']
    link['operationId'] = 'viewBasket'
    add = manipulate['paths']['/api/BasketItems']['post']
    add['operationId'] = 'addBasketItem'
    # O alvo ligado BasketId é independente do código de resposta, como no
    # construtor original. Modelar 401 permite avaliar tentativas negadas.
    add['responses']['401'] = {'description': 'BasketId negado pela aplicação'}
    spec['paths']['/api/BasketItems'] = {'post': add}
    spec['paths']['/rest/user/login']['post']['responses']['200']['links']['add'] = {
        'operationId': 'addBasketItem', 'requestBody': {'BasketId': '$response.body#/authentication/bid'}}
    return spec


def canonical(marking):
    return {p: Counter(json.dumps(t, sort_keys=True) for t in tokens) for p, tokens in marking.items()}


def assess(spec, monitor_dir, plan, observations):
    runtime_rows = [json.loads(s) for s in (monitor_dir / 'monitor.jsonl').read_text().splitlines()]
    captures = [json.loads(s) for s in (monitor_dir / 'eventos_capturados.jsonl').read_text().splitlines()]
    by_event = {r['event_id']: r for r in runtime_rows if 'event_id' in r}
    by_case = {r['request_headers']['x-evaluation-id']: r for r in captures
               if 'x-evaluation-id' in r['request_headers']}
    by_source = {r['source_index']: r for r in captures}
    decisions, state_matches = {}, True
    model = build_openapi_cpn(spec)
    for trace in load_traces(monitor_dir / 'eventos_capturados.jsonl', strict_bodies=False):
        session = ReplaySession(model, trace.trace_id, trace.user_id,
            unknown_policy='skip', continue_after_divergence=True)
        for index, event in enumerate(trace.events):
            incremental = session.process(event)
            offline = replay_trace(model, model.initial_places,
                Trace(trace.trace_id, trace.user_id, trace.events[:index+1]),
                unknown_policy='skip', continue_after_divergence=True, record_steps=True)
            failure = next((d for d in offline.divergences if d.event_index == index), None)
            status = 'divergent' if failure else 'fired' if any(
                s['event_index'] == index for s in offline.steps) else 'outside_model'
            offrow = dict(status=status, result=dict(divergence=jsonable(failure)))
            runtime = by_event[event.source_index]
            same = (status == incremental.status == runtime['status'] and
                jsonable(failure) == jsonable(incremental.divergence) ==
                runtime.get('result', {}).get('divergence') and
                canonical(jsonable(offline.final_marking)) == canonical(jsonable(session.snapshot())) ==
                canonical(by_source[event.source_index]['runtime_marking']))
            state_matches = state_matches and same
            decisions[event.source_index] = dict(offline=prediction(offrow),
                incremental=prediction(dict(status=incremental.status, result=jsonable(incremental))),
                equivalent=same)
    rows = []
    for case in plan:
        captured = by_case.get(case['case_id'])
        runtime = by_event.get(captured['source_index']) if captured else None
        pair = decisions.get(captured['source_index'], {}) if captured else {}
        rows.append(dict(**case, **observations[case['case_id']],
            predicted_bola=prediction(runtime or {}),
            offline_predicted_bola=pair.get('offline'),
            incremental_predicted_bola=pair.get('incremental'),
            modes_agree=pair.get('equivalent', False),
            runtime_status=runtime['status'] if runtime else 'unavailable',
            elapsed_ns=runtime.get('result', {}).get('elapsed_ns') if runtime else None,
            divergence_kind=((runtime or {}).get('result', {}).get('divergence') or {}).get('kind')))
    summary = json.loads((monitor_dir / 'resultado.json').read_text())
    return rows, summary['counts'], state_matches


def add_plan(stream, plan, case):
    # Rótulo fixado e descarregado antes de enviar a requisição avaliada.
    if case['expected_bola'] != (case['actor_id'] != case['owner_id']):
        raise ValueError('Gabarito incompatível com proprietário verificado.')
    plan.append(case)
    stream.write(json.dumps(case, ensure_ascii=False) + '\n')
    stream.flush()


def juice_experiment(upstream, port, repetitions, stream):
    users = {}
    for user in ('alice', 'bob'):
        email = f'cpn-{user}@tcc.invalid'
        status, registered, _ = request(upstream, 'POST', '/api/Users',
            body=dict(email=email, password=PASSWORD, passwordRepeat=PASSWORD))
        require(status, (201,), 'Juice registro')
        status, logged, _ = request(port, 'POST', '/rest/user/login',
            body=dict(email=email, password=PASSWORD))
        require(status, (200,), 'Juice login observado')
        auth = logged['authentication']
        status, basket, _ = request(upstream, 'GET', f'/rest/basket/{auth["bid"]}', token=auth['token'])
        require(status, (200,), 'Juice verificar proprietário')
        if basket['data']['UserId'] != registered['data']['id']:
            raise RuntimeError('Proprietário do carrinho não confere com o usuário criado.')
        users[user] = dict(user_id=registered['data']['id'], basket_id=auth['bid'], token=auth['token'])
    status, products, _ = request(upstream, 'GET', '/api/Products')
    require(status, (200,), 'Juice catálogo')
    product_ids = [p['id'] for p in products['data']]
    plan, observations = [], {}
    for repetition in range(repetitions):
        for actor in ('alice', 'bob'):
            own = users[actor]
            for owner in ('alice', 'bob'):
                target = users[owner]
                attack = actor != owner
                case = dict(case_id=f'juice-view-{repetition}-{actor}-{owner}',
                    scenario='juice_view_basket', repetition=repetition, actor=actor,
                    actor_id=own['user_id'], owner_id=target['user_id'], target_id=target['basket_id'],
                    method='GET', expected_bola=attack)
                add_plan(stream, plan, case)
                status, value, _ = request(port, 'GET', f'/rest/basket/{target["basket_id"]}',
                    token=own['token'], case_id=case['case_id'])
                effect = status == 200 and (value or {}).get('data', {}).get('id') == target['basket_id']
                observations[case['case_id']] = dict(http_status=status, fixture_valid=effect,
                    exploitation_succeeded=effect if attack else None)
                product = product_ids[(2 * repetition + (actor == 'bob')) % len(product_ids)]
                # Limpeza pelo proprietário permite repetir o roteiro sem colisão de item.
                status, basket, _ = request(upstream, 'GET', f'/rest/basket/{target["basket_id"]}', token=target['token'])
                for existing in basket['data']['Products']:
                    if existing['id'] == product:
                        item_id = existing['BasketItem']['id']
                        status, _, _ = request(upstream, 'DELETE', f'/api/BasketItems/{item_id}', token=target['token'])
                        require(status, (200,), 'Juice limpeza do item próprio')
                case = dict(case, case_id=f'juice-manipulate-{repetition}-{actor}-{owner}',
                    scenario='juice_manipulate_basket', method='POST', product_id=product,
                    encoding='duplicate_BasketId' if attack else 'regular_json')
                add_plan(stream, plan, case)
                body = dict(ProductId=product, BasketId=target['basket_id'], quantity=1)
                raw = ('{"ProductId":%d,"BasketId":%d,"quantity":1,"BasketId":%d}' %
                       (product, own['basket_id'], target['basket_id'])) if attack else None
                status, value, _ = request(port, 'POST', '/api/BasketItems', body=body, raw=raw,
                    token=own['token'], case_id=case['case_id'])
                verify_status, basket, _ = request(upstream, 'GET',
                    f'/rest/basket/{target["basket_id"]}', token=target['token'])
                effect = (status == verify_status == 200 and
                    (value or {}).get('data', {}).get('BasketId') == target['basket_id'] and
                    any(p['id'] == product and p['BasketItem']['quantity'] == 1 for p in basket['data']['Products']))
                observations[case['case_id']] = dict(http_status=status, fixture_valid=effect,
                    exploitation_succeeded=effect if attack else None)
                if attack:
                    case = dict(case, case_id=f'juice-denied-{repetition}-{actor}-{owner}',
                        scenario='juice_manipulate_denied', encoding='regular_json')
                    add_plan(stream, plan, case)
                    before = basket
                    status, _, _ = request(port, 'POST', '/api/BasketItems', body=body,
                        token=own['token'], case_id=case['case_id'])
                    _, after, _ = request(upstream, 'GET', f'/rest/basket/{target["basket_id"]}', token=target['token'])
                    observations[case['case_id']] = dict(http_status=status,
                        fixture_valid=status == 401 and before == after,
                        exploitation_succeeded=False if status == 401 else None)
    return plan, observations


def memos_experiment(upstream, port, repetitions, stream):
    status, host, cookie = request(upstream, 'POST', '/api/v1/auth/signup',
        body=dict(username='cpnhost', password=PASSWORD))
    require(status, (200,), 'Memos host inicial')
    users = {}
    for actor in ('alice', 'bob'):
        status, created, _ = request(upstream, 'POST', '/api/v1/user', cookie=cookie,
            body=dict(username='cpn' + actor, password=PASSWORD, role='USER', nickname=actor, email=''))
        require(status, (200,), 'Memos usuário comum')
        status, logged, actor_cookie = request(upstream, 'POST', '/api/v1/auth/signin',
            body=dict(username='cpn' + actor, password=PASSWORD))
        require(status, (200,), 'Memos login')
        if logged['id'] != created['id'] or logged['role'] != 'USER':
            raise RuntimeError('Memos identidade/role não confere.')
        users[actor] = dict(user_id=created['id'], cookie=actor_cookie)
    plan, observations = [], {}
    for repetition in range(repetitions):
        notes = {}
        for owner in ('alice', 'bob'):
            for actor in ('alice', 'bob'):
                status, memo, _ = request(upstream, 'POST', '/api/v1/memo', cookie=users[owner]['cookie'],
                    body=dict(content=f'CPN etapa4 {repetition} owner={owner} actor={actor}', visibility='PRIVATE'))
                require(status, (200,), 'Memos criação privada')
                if memo['creatorId'] != users[owner]['user_id']:
                    raise RuntimeError('Criador da nota não confere.')
                notes[(owner, actor)] = memo['id']
        for actor in ('alice', 'bob'):
            session = f'memos-{actor}-{repetition}'
            status, listed, _ = request(port, 'GET', '/api/v1/memo',
                cookie=users[actor]['cookie'], session=session)
            require(status, (200,), 'Memos listagem observada')
            if not all(m['creatorId'] == users[actor]['user_id'] for m in listed):
                raise RuntimeError('Listagem privada contém recursos de outro proprietário.')
            for owner in ('alice', 'bob'):
                target = notes[(owner, actor)]
                attack = actor != owner
                case = dict(case_id=f'memos-archive-{repetition}-{actor}-{owner}',
                    scenario='memos_archive', repetition=repetition, actor=actor,
                    actor_id=users[actor]['user_id'], owner_id=users[owner]['user_id'],
                    target_id=target, method='PATCH', expected_bola=attack)
                add_plan(stream, plan, case)
                status, value, _ = request(port, 'PATCH', f'/api/v1/memo/{target}',
                    body=dict(rowStatus='ARCHIVED'), cookie=users[actor]['cookie'],
                    session=session, case_id=case['case_id'])
                verify_status, verified, _ = request(upstream, 'GET', f'/api/v1/memo/{target}', cookie=users[owner]['cookie'])
                effect = (status == verify_status == 200 and value.get('id') == target and
                          value.get('rowStatus') == verified.get('rowStatus') == 'ARCHIVED')
                observations[case['case_id']] = dict(http_status=status, fixture_valid=effect,
                    exploitation_succeeded=effect if attack else None)
    return plan, observations


def run(output, repetitions):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    specs = dict(juice=juice_model(), memos=json.loads((ROOT / 'examples/memos.openapi.json').read_text()))
    for name, spec in specs.items():
        write_json(output / f'{name}.openapi.json', spec)
    rows, provenance, counts, matches = [], {}, {}, {}
    with (output / 'gabarito.jsonl').open('w', encoding='utf-8') as truth:
        for name, experiment in (('juice', juice_experiment), ('memos', memos_experiment)):
            print(f'Iniciando {name} real...', flush=True)
            with application(name) as (upstream, info):
                with tempfile.TemporaryDirectory(prefix=f'cpn-real-{name}-') as directory:
                    directory = Path(directory)
                    with proxy((output / f'{name}.openapi.json').resolve(), upstream, directory,
                               identity_header='X-CPN-Session' if name == 'memos' else None) as port:
                        plan, observations = experiment(upstream, port, repetitions, truth)
                    result, app_counts, same = assess(specs[name], directory / 'monitor', plan, observations)
            rows += result
            provenance[name], counts[name], matches[name] = info, app_counts, same
            print(f'{name}: {len(result)} requisições; equivalência={same}', flush=True)
    valid = (all(r['fixture_valid'] and r['modes_agree'] for r in rows) and
        all(c['capture_error'] == c['transport_error'] == 0 for c in counts.values()))
    report = dict(execution_valid=valid, metrics=aggregate(rows), by_mode={
        mode: aggregate([dict(r, predicted_bola=r[key]) for r in rows])
        for mode, key in (('runtime','predicted_bola'), ('offline','offline_predicted_bola'),
                          ('incremental','incremental_predicted_bola'))},
        attack_outcomes=dict(successful=sum(r['exploitation_succeeded'] is True for r in rows),
                             denied=sum(r['expected_bola'] and r['http_status'] in (401,403) for r in rows)),
        provenance=provenance, runtime_counts=counts, all_event_states_equal=matches,
        request_interval_seconds=REQUEST_INTERVAL_SECONDS,
        repetitions=repetitions, python=sys.version.split()[0], mitmproxy=metadata.version('mitmproxy'),
        memos_repository='https://github.com/ailton07/memos-with-BOLA', memos_commit=MEMOS_COMMIT,
        juice_reference='https://github.com/ailton07/juice-shop-with-winston/tree/079ea6d65463b99c0d25a9ad116127575ce96e9a',
        juice_adaptation='Imagem oficial 14.1.1, mesma versão declarada no fork instrumentado; '
            'captura externa pelo proxy em vez do logger Winston. Não é o binário do fork.',
        memos_adaptation='Backend compilado do fork fixado sem alterar arquivos rastreados; '
            'modo dev (0.16.0) para usar o schema compatível com o backend; modo prod '
            '0.15.2 falhou por ausência de resource.memo_id. Frontend compilado não '
            'é necessário para chamadas REST. Identidade de sessão '
            'fornecida por cabeçalho do alimentador controlado, com autenticação real por cookie.',
        policy='Gabarito: actor_id != owner_id. Alerta: divergência de controle/dados. '
            'Login/listagem são preparação, sem participação na matriz. Tentativas negadas entram como ataques.',
        scope='Somente instâncias locais descartáveis. Repetições de roteiros conhecidos, '
              'não reprodução da amostragem de usuários/estatística integral da tese. Sem benchmark de carga.',
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'scripts/evaluate_real_detection.py', ROOT/'scripts/real_apps.py',
                      ROOT/'cpn_detection.py', ROOT/'cpn_runtime.py', ROOT/'cpn_incremental.py',
                      ROOT/'cpn_bola.py', ROOT/'cpn_openapi.py', ROOT/'runtime/mitmproxy_addon.py']},
        input_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in output.glob('*.openapi.json')})
    report['input_sha256']['gabarito.jsonl'] = hashlib.sha256((output/'gabarito.jsonl').read_bytes()).hexdigest()
    binary = ROOT / '.runtime-apps/memos/memos-tcc'
    report['memos_binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    write_json(output / 'resultado.json', report)
    with (output / 'eventos_rotulados.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    lines = ['# Detecção em aplicações reais', '',
             '| Cenário | Total | TP | FP | FN | TN | Cobertura | Precisão | Recall | F1 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for scenario, m in report['metrics']['by_scenario'].items():
        values = [scenario] + [str(m[k]) for k in ('total','TP','FP','FN','TN')]
        values += [f'{m[k]:.3f}' if m[k] is not None else 'indefinido' for k in ('coverage','precision','recall','f1')]
        lines.append('| ' + ' | '.join(values) + ' |')
    lines += ['', report['scope'], '', 'Versões, adaptações e hashes constam em resultado.json.', '']
    (output / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=10)
    parser.add_argument('--output', default=str(ROOT / 'docs/avaliacao_deteccao_real'))
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error('Use ao menos uma repetição.')
    try:
        report = run(args.output, args.repetitions)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report['metrics']['overall'], ensure_ascii=False))
    return 0 if report['execution_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
