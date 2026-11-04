"""Etapa 5: carga aberta, três configurações, alvos reais e estado reinicializado."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import csv
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import random
import signal
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
from time import monotonic, perf_counter_ns, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cpn_export import write_json
from cpn_performance import delta, distribution, load, repeated
from scripts.evaluate_real_detection import request, require, PASSWORD
from scripts.real_apps import application

MODES = ('direct', 'proxy', 'monitor')


def process_usage(pid):
    """CPU de processo em segundos e RSS atual, sem dependência de psutil."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        cpu = (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
        status = Path(f'/proc/{pid}/status').read_text().splitlines()
        rss = next(int(line.split()[1]) * 1024 for line in status if line.startswith('VmRSS:'))
        return dict(cpu_seconds=cpu, rss_bytes=rss)
    except (OSError, ValueError, StopIteration):
        return None


class Sampler:
    def __init__(self, pids):
        self.pids = {name: pid for name, pid in pids.items() if pid is not None}
        self.samples, self.stop = [], Event()

    def sample(self):
        self.samples.append(dict(at_ns=perf_counter_ns(), processes={
            name: process_usage(pid) for name, pid in self.pids.items()}))

    def start(self):
        self.sample()
        self.thread = Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stop.wait(.1):
            self.sample()

    def finish(self):
        self.stop.set()
        self.thread.join()
        self.sample()
        elapsed = (self.samples[-1]['at_ns'] - self.samples[0]['at_ns']) / 1e9
        result = {}
        for name in self.pids:
            values = [s['processes'][name] for s in self.samples if s['processes'][name] is not None]
            if len(values) < 2:
                result[name] = None
                continue
            cpu_seconds = values[-1]['cpu_seconds'] - values[0]['cpu_seconds']
            result[name] = dict(cpu_seconds=cpu_seconds,
                cpu_percent_one_core=100*cpu_seconds/elapsed if elapsed else None,
                rss_start_bytes=values[0]['rss_bytes'], rss_end_bytes=values[-1]['rss_bytes'],
                sampled_peak_rss_bytes=max(v['rss_bytes'] for v in values), samples=len(values))
        return result


@contextmanager
def frontend(mode, upstream, spec, directory):
    if mode == 'direct':
        yield upstream, None
        return
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = [sys.executable, str(ROOT/'scripts/run_proxy.py'), str(spec),
        '--upstream', f'http://127.0.0.1:{upstream}', '--port', str(port),
        '--output', str(directory/'monitor'), '--identity-header', 'X-CPN-Session']
    if mode == 'proxy':
        args.append('--no-monitor')
    with (directory/'proxy.log').open('w+') as console:
        child = subprocess.Popen(args, stdout=console, stderr=console)
        try:
            deadline = monotonic()+20
            while True:
                if child.poll() is not None:
                    console.seek(0)
                    raise RuntimeError('Proxy encerrou: '+console.read())
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    if monotonic() >= deadline:
                        raise RuntimeError('Timeout do proxy.')
                    sleep(.05)
            yield port, child.pid
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def workload(app, upstream, port):
    """Preparação idêntica nos três modos, antes de aquecimento e medidas."""
    headers = {'Content-Type': 'application/json', 'X-CPN-Session': 'benchmark-owner'}
    if app == 'juice':
        email = 'cpn-benchmark@tcc.invalid'
        status, user, _ = request(upstream, 'POST', '/api/Users',
            body=dict(email=email, password=PASSWORD, passwordRepeat=PASSWORD))
        require(status, (201,), 'Registro benchmark Juice')
        status, logged, _ = request(port, 'POST', '/rest/user/login',
            body=dict(email=email, password=PASSWORD), session='benchmark-owner')
        require(status, (200,), 'Login benchmark Juice')
        target = logged['authentication']['bid']
        headers['Authorization'] = 'Bearer '+logged['authentication']['token']
        return dict(method='GET', path=f'/rest/basket/{target}', body=None, headers=headers,
                    target=target, validate=lambda raw: json.loads(raw)['data']['id'] == target,
                    growth_body=json.dumps(dict(email=email, password=PASSWORD)))
    status, _, host_cookie = request(upstream, 'POST', '/api/v1/auth/signup',
        body=dict(username='cpnhost', password=PASSWORD))
    require(status, (200,), 'Host benchmark Memos')
    status, _, _ = request(upstream, 'POST', '/api/v1/user', cookie=host_cookie,
        body=dict(username='cpnowner', password=PASSWORD, role='USER', nickname='Owner', email=''))
    require(status, (200,), 'Usuário benchmark Memos')
    status, _, cookie = request(port, 'POST', '/api/v1/auth/signin',
        body=dict(username='cpnowner', password=PASSWORD), session='benchmark-owner')
    require(status, (200,), 'Login benchmark Memos')
    status, memo, _ = request(port, 'POST', '/api/v1/memo', cookie=cookie,
        session='benchmark-owner', body=dict(content='CPN benchmark', visibility='PRIVATE'))
    require(status, (200,), 'Nota benchmark Memos')
    target = memo['id']
    status, _, _ = request(upstream, 'PATCH', f'/api/v1/memo/{target}',
        cookie=cookie, body=dict(rowStatus='ARCHIVED'))
    require(status, (200,), 'Estado inicial arquivado')
    headers['Cookie'] = cookie
    return dict(method='PATCH', path=f'/api/v1/memo/{target}',
        body=json.dumps(dict(rowStatus='ARCHIVED')), headers=headers, target=target,
        validate=lambda raw: json.loads(raw)['id'] == target and json.loads(raw)['rowStatus'] == 'ARCHIVED')


def trial(app, mode, rate, duration, warmup, workers, directory, spec):
    directory.mkdir()
    with application(app) as (upstream, info):
        with frontend(mode, upstream, spec, directory) as (port, proxy_pid):
            task = workload(app, upstream, port)
            _, warm = load(port, task['method'], task['path'], task['headers'], task['body'],
                rate=10, duration=warmup, workers=workers, prefix='warmup', validate=task['validate'])
            sampler = Sampler(dict(api=info['host_pid'], proxy=proxy_pid))
            sampler.start()
            try:
                rows, summary = load(port, task['method'], task['path'], task['headers'], task['body'],
                    rate=rate, duration=duration, workers=workers, validate=task['validate'])
            finally:
                resources = sampler.finish()
    internal, state = None, None
    if mode == 'monitor':
        records = [json.loads(s) for s in (directory/'monitor/monitor.jsonl').read_text().splitlines()]
        records = [r for r in records if str(r.get('measurement_id', '')).startswith('measure-')]
        summary_runtime = json.loads((directory/'monitor/resultado.json').read_text())
        expected_ids = {r['measurement_id'] for r in rows if r['status'] is not None}
        observed_ids = [r['measurement_id'] for r in records]
        internal = dict(observed=len(records), expected_http_responses=len(expected_ids),
            complete=set(observed_ids) == expected_ids and len(observed_ids) == len(set(observed_ids)),
            classifications=dict(Counter(r['status'] for r in records)),
            replay_ms=distribution([r['result']['elapsed_ns']/1e6 for r in records if 'result' in r]),
            fired_replay_ms=distribution([r['result']['elapsed_ns']/1e6 for r in records if r['status']=='fired']),
            monitor_ms=distribution([r['monitor_ns']/1e6 for r in records if 'monitor_ns' in r]))
        state = dict(sessions=len(summary_runtime['results']),
            tokens=sum(len(tokens) for r in summary_runtime['results'] for tokens in r['final_marking'].values()),
            counts=summary_runtime['counts'])
    # Não persistir cabeçalhos/tokens ou logs da aplicação.
    with (directory/'samples.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row)+'\n')
    write_json(directory/'resources.json', dict(summary=resources, samples=sampler.samples))
    report = dict(app=app, mode=mode, target_rps=rate, duration_seconds=duration,
        workers=workers, summary=summary, warmup=warm, resources=resources,
        internal=internal, final_state=state, application=info)
    write_json(directory/'result.json', report)
    return report


def consolidate(trials):
    groups = []
    for app in sorted({t['app'] for t in trials}):
        for rate in sorted({t['target_rps'] for t in trials if t['app']==app}):
            modes = {}
            for mode in MODES:
                selection = [t for t in trials if (t['app'],t['target_rps'],t['mode'])==(app,rate,mode)]
                modes[mode] = dict(repetitions=len(selection),
                    requests=sum(t['summary']['requests'] for t in selection),
                    successes=sum(t['summary']['successes'] for t in selection),
                    http_statuses=dict(sum((Counter(t['summary']['http_statuses'])
                                           for t in selection), Counter())),
                    latency_ms={key:repeated([t['summary']['latency_ms'][key] for t in selection])
                                for key in ('mean','p50','p95','p99')},
                    successful_rps=repeated([t['summary']['successful_rps'] for t in selection]),
                    client_queue_ms=repeated([t['summary']['client_queue_ms']['p95'] for t in selection]),
                    success_fraction=repeated([t['summary']['success_fraction'] for t in selection]),
                    successful_latency_ms={key:repeated([t['summary'].get('successful_latency_ms', {}).get(key)
                        for t in selection]) for key in ('mean','p50','p95','p99')},
                    resources={process:{key:repeated([(t.get('resources', {}).get(process) or {}).get(key)
                        for t in selection]) for key in ('cpu_percent_one_core','sampled_peak_rss_bytes')}
                        for process in ('api','proxy')},
                    replay_ms=repeated([(t.get('internal') or {}).get('fired_replay_ms', {}).get('mean')
                                        for t in selection]))
            overhead = {}
            for label, base, measured in (('proxy_vs_direct','direct','proxy'),
                                          ('monitor_vs_proxy','proxy','monitor'),
                                          ('total_vs_direct','direct','monitor')):
                # Diferenças pareadas pela repetição, sem fabricar percentis conjuntos.
                a = sorted([t for t in trials if (t['app'],t['target_rps'],t['mode'])==(app,rate,base)], key=lambda t:t['repetition'])
                b = sorted([t for t in trials if (t['app'],t['target_rps'],t['mode'])==(app,rate,measured)], key=lambda t:t['repetition'])
                paired = [dict(repetition=x['repetition'],
                    **{key:delta(x['summary']['latency_ms'][key],y['summary']['latency_ms'][key])
                       for key in ('mean','p50','p95','p99')},
                    successful_rps=delta(x['summary']['successful_rps'],y['summary']['successful_rps']))
                          for x,y in zip(a,b)]
                overhead[label] = dict(paired=paired,
                    mean_latency_ms=repeated([p['mean']['absolute'] for p in paired]),
                    mean_latency_percent=repeated([p['mean']['percent'] for p in paired]),
                    successful_rps_percent=repeated([p['successful_rps']['percent'] for p in paired]))
            groups.append(dict(app=app, target_rps=rate, modes=modes, overhead=overhead))
    return groups


def growth(directory, spec, counts):
    """Produtor repetido em uma sessão: sem deduplicar tokens para limitar memória."""
    directory.mkdir()
    with application('juice') as (upstream, info):
        with frontend('monitor', upstream, spec, directory) as (port, pid):
            task = workload('juice', upstream, port)
            sampler = Sampler(dict(api=info['host_pid'], proxy=pid))
            sampler.start()
            try:
                rows, summary = load(port, 'POST', '/rest/user/login', task['headers'], task['growth_body'],
                    rate=100, duration=max(counts)/100, workers=1, prefix='growth',
                    validate=lambda raw: json.loads(raw)['authentication']['bid'] == task['target'],
                    state_samples={n-1 for n in counts})
            finally:
                resources = sampler.finish()
    records = [json.loads(s) for s in (directory/'monitor/monitor.jsonl').read_text().splitlines()]
    samples = []
    for record in records:
        if 'state' not in record:
            continue
        index = int(record['measurement_id'].split('-')[1])
        nearest = min(sampler.samples, key=lambda s:abs(s['at_ns']-rows[index]['finished_monotonic_ns']))
        samples.append(dict(events=index+1, state=record['state'],
            replay_ms=record['result']['elapsed_ns']/1e6,
            nearest_resource_sample=nearest))
    report = dict(summary=summary, checkpoints=samples, resources=resources,
        note='Uma sessão e login produtor repetido; há um login inicial adicional. '
             'RSS é amostrado a cada 100ms e associado ao ponto mais próximo. Sem prova assintótica.')
    write_json(directory/'growth.json', report)
    return report


def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    specs = {}
    for app, example in (('juice','juice_view'),('memos','memos')):
        path = output/f'{app}.openapi.json'
        path.write_bytes((ROOT/f'examples/{example}.openapi.json').read_bytes())
        specs[app] = path.resolve()
    rng = random.Random(args.seed)
    trials = []
    trial_root = output/'trials'
    trial_root.mkdir()
    with tempfile.TemporaryDirectory(prefix='cpn-performance-') as temp:
        for app in args.apps:
            for rate in args.rates:
                # Rotação balanceada a cada três repetições, com ordem inicial sorteada.
                initial = list(MODES)
                rng.shuffle(initial)
                for repetition in range(args.repetitions):
                    order = initial[repetition%3:]+initial[:repetition%3]
                    for order_index, mode in enumerate(order):
                        name = f'{app}-r{rate}-n{repetition}-{mode}'
                        print(f'Ensaio {name}', flush=True)
                        temporary = Path(temp)/name
                        result = trial(app, mode, rate, args.duration, args.warmup,
                            args.workers, temporary, specs[app])
                        result.update(repetition=repetition, order_index=order_index)
                        dest = trial_root/name
                        dest.mkdir()
                        for file in ('samples.jsonl','resources.json'):
                            (dest/file).write_bytes((temporary/file).read_bytes())
                        write_json(dest/'result.json', result)
                        trials.append(result)
                        # Checkpoint: dados de ensaios anteriores sobrevivem a falhas posteriores.
                        write_json(output/'progress.json', dict(completed=len(trials), trials=trials))
        growth_report = None
        if args.growth_counts:
            print('Experimento de crescimento de estado...', flush=True)
            growth_report = growth(Path(temp)/'growth', specs['juice'], args.growth_counts)
    report = dict(configuration=vars(args), trials=trials, groups=consolidate(trials), growth=growth_report,
        environment=dict(platform=platform.platform(), python=sys.version.split()[0],
            mitmproxy=metadata.version('mitmproxy'), logical_cpus=os.cpu_count(),
            cpu_model=next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines()
                            if s.startswith('model name')),None),
            mem_total=next(s for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemTotal:')),
            loadavg=os.getloadavg()),
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'cpn_performance.py', ROOT/'cpn_runtime.py', ROOT/'cpn_snakes.py',
                      ROOT/'cpn_incremental.py', ROOT/'scripts/benchmark_runtime.py',
                      ROOT/'scripts/run_proxy.py', ROOT/'scripts/real_apps.py',
                      ROOT/'runtime/mitmproxy_addon.py']},
        input_sha256={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in specs.values()},
        scope='Carga aberta em localhost, gerador/API/proxy na mesma máquina; leitura de carrinho '
              'e arquivamento idempotente de nota própria. Três configurações síncronas, '
              'sem captura bruta e com log normal do monitor habilitado. Não estima capacidade '
              'máxima nem define limiar de overhead aceitável. HTTP 429 permanece visível.')
    report['integrity_valid'] = all(
        t['summary']['requests'] == int(t['target_rps'] * t['duration_seconds'])
        and (t['internal'] is None or (t['internal']['complete'] and
            t['internal']['classifications'].get('fired', 0) == t['summary']['successes']))
        for t in trials)
    write_json(output/'resultado.json', report)
    table = ['# Desempenho e overhead — aplicações reais', '',
        'Médias entre repetições. Percentis são calculados por ensaio e depois agregados.', '',
        '| API | Carga req/s | Modo | Média ms | p50 ms | p95 ms | p99 ms | Sucessos/s | HTTP 200 / total |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for group in report['groups']:
        for mode, data in group['modes'].items():
            values = [group['app'],str(group['target_rps']),mode]
            values += [f"{data['latency_ms'][k]['mean']:.3f}" for k in ('mean','p50','p95','p99')]
            values += [f"{data['successful_rps']['mean']:.2f}",f"{data['successes']} / {data['requests']}"]
            table.append('| '+' | '.join(values)+' |')
    table += ['', 'Latências acima incluem todos os status. HTTP 429 é recusa por limite, não sucesso.', '',
        '| API | Carga req/s | Proxy − direta (ms) | Monitor − proxy (ms) | Total − direta (ms) | Total (%) |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for group in report['groups']:
        overhead = group['overhead']
        values = [group['app'], str(group['target_rps'])]
        values += [f"{overhead[k]['mean_latency_ms']['mean']:+.3f}" for k in
                   ('proxy_vs_direct','monitor_vs_proxy','total_vs_direct')]
        values += [f"{overhead['total_vs_direct']['mean_latency_percent']['mean']:+.1f}"]
        table.append('| '+' | '.join(values)+' |')
    table += ['', 'Consulte resultado.json para dispersão, overhead pareado, erros, CPU/RSS e estado.', '', report['scope'], '']
    (output/'README.md').write_text('\n'.join(table), encoding='utf-8')
    with (output/'resumo.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['api','rate','repetition','mode','latency_mean_ms','p50_ms','p95_ms','p99_ms',
                         'successful_rps','successes','requests','http_statuses'])
        for t in trials:
            s = t['summary']
            writer.writerow([t['app'],t['target_rps'],t['repetition'],t['mode']]+[
                s['latency_ms'][k] for k in ('mean','p50','p95','p99')]+[
                s['successful_rps'],s['successes'],s['requests'],json.dumps(s['http_statuses'])])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'docs/avaliacao_desempenho'))
    parser.add_argument('--apps', nargs='+', choices=['juice','memos'], default=['juice','memos'])
    parser.add_argument('--rates', nargs='+', type=int, default=[10,50,100,200])
    parser.add_argument('--duration', type=float, default=3)
    parser.add_argument('--warmup', type=float, default=1)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--growth-counts', nargs='*', type=int, default=[100,1000])
    args = parser.parse_args(argv)
    if (args.duration <= 0 or args.warmup < .1 or args.repetitions < 1 or args.workers < 1
            or any(r <= 0 or int(r*args.duration)<1 for r in args.rates)
            or any(n <= 0 for n in args.growth_counts)):
        parser.error('Parâmetros devem ser positivos e gerar ao menos uma requisição.')
    args.rates = sorted(set(args.rates))
    args.apps = list(dict.fromkeys(args.apps))
    args.growth_counts = sorted(set(args.growth_counts))
    try:
        report = run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 2
    print(f"{len(report['trials'])} ensaios concluídos; relatório em {args.output}")
    return 0 if report['integrity_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
