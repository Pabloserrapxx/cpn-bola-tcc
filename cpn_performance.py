"""Estatística e gerador de carga HTTP com agendamento aberto e fila observável."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPException
from math import ceil
from statistics import mean, median, stdev
from threading import local
from time import perf_counter_ns, sleep


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0, mean=None, p50=None, p95=None, p99=None, min=None, max=None)
    def percentile(q):
        return values[max(0, ceil(q * len(values)) - 1)]
    return dict(count=len(values), mean=mean(values), p50=median(values),
                p95=percentile(.95), p99=percentile(.99), min=values[0], max=values[-1])


def delta(baseline, measured):
    if baseline is None or measured is None:
        return dict(absolute=None, percent=None)
    return dict(absolute=measured-baseline,
                percent=100*(measured-baseline)/baseline if baseline else None)


def repeated(values):
    values = [v for v in values if v is not None]
    return dict(n=len(values), mean=mean(values) if values else None,
                stdev=stdev(values) if len(values) > 1 else None,
                min=min(values) if values else None, max=max(values) if values else None)


def summarize(rows, duration_seconds):
    elapsed = max(duration_seconds, max((r['completed_ns'] for r in rows), default=0) / 1e9)
    successes = [r for r in rows if r['status'] == 200 and r['body_valid']]
    return dict(requests=len(rows), successes=len(successes),
        http_statuses=dict(Counter(str(r['status']) for r in rows)),
        transport_errors=sum(r['error_type'] is not None for r in rows),
        invalid_bodies=sum(r['status'] == 200 and not r['body_valid'] for r in rows),
        elapsed_seconds=elapsed, completed_rps=len(rows)/elapsed if elapsed else None,
        successful_rps=len(successes)/elapsed if elapsed else None,
        success_fraction=len(successes)/len(rows) if rows else None,
        latency_ms=distribution([r['latency_ms'] for r in rows]),
        successful_latency_ms=distribution([r['latency_ms'] for r in successes]),
        service_ms=distribution([r['service_ms'] for r in rows]),
        client_queue_ms=distribution([r['queue_ms'] for r in rows]))


def load(port, method, path, headers, body, *, rate, duration, workers=16,
         prefix='measure', validate=lambda payload: True, state_samples=()):
    """Carga aberta: latência inclui atraso do gerador e espera na fila local.

    Uma conexão persistente por trabalhador; todas as respostas são consumidas.
    Erros não são repetidos para não alterar o volume ou esconder falhas.
    """
    count = int(rate * duration)
    if rate <= 0 or duration <= 0 or workers < 1 or count < 1:
        raise ValueError('Carga deve gerar ao menos uma requisição; limites positivos.')
    storage, connections = local(), []
    origin = perf_counter_ns()
    def execute(index):
        scheduled_ns = int(index / rate * 1e9)
        start = perf_counter_ns()
        conn = getattr(storage, 'connection', None)
        if conn is None:
            conn = HTTPConnection('127.0.0.1', port, timeout=10)
            storage.connection = conn
            connections.append(conn)
        status, valid, error = None, False, None
        try:
            conn.request(method, path, body=body,
                         headers={**headers, 'X-CPN-Measurement-Id': f'{prefix}-{index}',
                                  **({'X-CPN-State-Sample': '1'} if index in state_samples else {})})
            response = conn.getresponse()
            status = response.status
            payload = response.read()
            if status == 200:
                try:
                    valid = bool(validate(payload))
                except (ValueError, KeyError, TypeError):
                    valid = False
        except (OSError, HTTPException, ValueError) as exc:
            error = type(exc).__name__
            conn.close()
            storage.connection = None
        finish = perf_counter_ns()
        return dict(index=index, measurement_id=f'{prefix}-{index}', status=status,
            body_valid=valid, error_type=error,
            latency_ms=(finish-origin-scheduled_ns)/1e6, service_ms=(finish-start)/1e6,
            queue_ms=max(0, start-origin-scheduled_ns)/1e6, completed_ns=finish-origin,
            finished_monotonic_ns=finish)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for index in range(count):
                delay = origin / 1e9 + index / rate - perf_counter_ns() / 1e9
                if delay > 0:
                    sleep(delay)
                futures.append(pool.submit(execute, index))
            rows = [future.result() for future in futures]
            remaining = duration - (perf_counter_ns()-origin)/1e9
            if remaining > 0:
                sleep(remaining)
    finally:
        for connection in connections:
            connection.close()
    return rows, summarize(rows, duration)
