"""Adaptação HTTP -> ReplaySession, independente do proxy usado na captura."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
from time import monotonic, perf_counter_ns

from cpn_export import jsonable, write_json
from cpn_incremental import ReplaySession
from cpn_logs import event_from_record
from cpn_openapi import build_openapi_cpn


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class RuntimeMonitor:
    def __init__(self, spec, output, *, compatible=False, identity_header=None,
                 ttl=1800, max_sessions=1000, max_body_bytes=1048576,
                 capture_events=False, continue_after_divergence=True):
        if ttl <= 0 or max_sessions <= 0 or max_body_bytes <= 0:
            raise ValueError('TTL, capacidade e limite de corpo devem ser positivos.')
        self.model = build_openapi_cpn(spec, links2cpn_compatible=compatible)
        self.compatible, self.identity_header = compatible, identity_header
        self.ttl, self.max_sessions, self.max_body_bytes = ttl, max_sessions, max_body_bytes
        self.continue_after_divergence = continue_after_divergence
        self.sessions = {}
        self.generation = 0
        self.lock = RLock()
        self.counts = dict(events=0, fired=0, divergent=0, outside_model=0,
                           not_processed=0, capture_error=0, transport_error=0,
                           expired_sessions=0)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.log = (self.output / 'monitor.jsonl').open('x', encoding='utf-8')
        self.capture = None
        try:
            if capture_events:
                self.capture = (self.output / 'eventos_capturados.jsonl').open('x', encoding='utf-8')
        except Exception:
            self.log.close()
            raise
        self.closed = False

    def _write(self, row):
        self.log.write(json.dumps(jsonable(row), ensure_ascii=False) + '\n')
        self.log.flush()

    def _expire(self, now):
        for key, (session, seen) in list(self.sessions.items()):
            if now - seen >= self.ttl:
                session.finish()
                del self.sessions[key]
                self.counts['expired_sessions'] += 1
                self._write(dict(status='session_expired', trace_id=session.trace_id,
                                 decision_at=utc_now()))

    def observe(self, *, method, path, status_code, request_headers,
                response_headers, request_body, response_body, flow_id,
                received_at=None, request_started_ns=None):
        """Recebe corpos descomprimidos em bytes; não altera tráfego HTTP.

        As chamadas são serializadas. A ordem é a de chegada ao monitor, no hook
        de resposta, e não uma ordenação por timestamp de requisição.
        """
        with self.lock:
            if self.closed:
                raise ValueError('Monitor encerrado.')
            self.counts['events'] += 1
            row = dict(event_id=self.counts['events'], flow_id=flow_id,
                       received_at=received_at, method=method, path=path.split('?', 1)[0])
            started = perf_counter_ns()
            measurement_id = next((v for k, v in request_headers.items()
                                   if k.lower() == 'x-cpn-measurement-id'), None)
            if measurement_id is not None:
                row['measurement_id'] = measurement_id
            try:
                if len(request_body) > self.max_body_bytes or len(response_body) > self.max_body_bytes:
                    raise ValueError('body_limit')
                headers = {k.lower(): v for k, v in request_headers.items()}
                record = dict(method=method, path=path, status_code=status_code,
                    request_headers=headers, response_headers=dict(response_headers),
                    request_body=request_body.decode('utf-8'),
                    response_body=response_body.decode('utf-8'))
                if self.identity_header:
                    identity = headers.get(self.identity_header.lower())
                    if not identity:
                        raise ValueError('identity_missing')
                    record['user_id'] = 'session:' + hashlib.sha256(identity.encode()).hexdigest()[:24]
                    identity_field = 'user_id'
                else:
                    record['headerAuthorization'] = headers.get('authorization')
                    identity_field = 'headerAuthorization'
                # Corpos não JSON fora da rede não devem impedir a exclusão.
                event = event_from_record(record, row['event_id'], identity_field,
                                          strict_bodies=False)
                now = monotonic()
                self._expire(now)
                if event.user_id not in self.sessions:
                    if len(self.sessions) >= self.max_sessions:
                        raise ValueError('session_capacity')
                    self.generation += 1
                    session = ReplaySession(self.model,
                        f'{event.user_id}:{self.generation}', event.user_id,
                        unknown_policy='skip',
                        continue_after_divergence=self.continue_after_divergence)
                    self.sessions[event.user_id] = (session, now)
                session, _ = self.sessions[event.user_id]
                self.sessions[event.user_id] = (session, now)
                result = session.process(event)
                self.counts[result.status] += 1
                row.update(trace_id=session.trace_id, status=result.status, result=result)
                if self.capture:
                    captured = asdict(event)
                    captured.update(trace_id=session.trace_id,
                                    runtime_marking=jsonable(session.snapshot()))
                    self.capture.write(json.dumps(captured, ensure_ascii=False) + '\n')
                    self.capture.flush()
            except Exception as exc:
                # Erros internos também ficam visíveis e não bloqueiam a resposta da API.
                self.counts['capture_error'] += 1
                row.update(status='capture_error', error_type=type(exc).__name__)
                if str(exc) in ('body_limit', 'identity_missing', 'session_capacity'):
                    row['reason'] = str(exc)
            if any(k.lower() == 'x-cpn-state-sample' and v == '1'
                   for k, v in request_headers.items()):
                row['state'] = dict(sessions=len(self.sessions),
                    tokens=sum(sum(p.marking.values()) for s, _ in self.sessions.values()
                               for p in s.cpn.places.values()),
                    variants=sum(len(s.executor.variants) for s, _ in self.sessions.values()
                                 if s.executor is not None))
            row['decision_at'] = utc_now()
            row['monitor_ns'] = perf_counter_ns() - started
            row['interaction_to_decision_ns'] = (
                perf_counter_ns() - request_started_ns if request_started_ns is not None else None)
            self._write(row)
            return row

    def capture_error(self, flow_id, error_type):
        """Falhas na extração HTTP (ex.: descompressão), anteriores ao Event."""
        with self.lock:
            self.counts['events'] += 1
            self.counts['capture_error'] += 1
            self._write(dict(event_id=self.counts['events'], flow_id=flow_id,
                status='capture_error', error_type=error_type, decision_at=utc_now()))

    def transport_error(self, flow_id):
        with self.lock:
            self.counts['transport_error'] += 1
            self._write(dict(flow_id=flow_id, status='transport_error', decision_at=utc_now()))

    def close(self):
        with self.lock:
            if self.closed:
                return
            results = [s.finish() for s, _ in self.sessions.values()]
            write_json(self.output / 'resultado.json', dict(counts=self.counts, results=results,
                profile='links2cpn' if self.compatible else 'strict',
                continue_after_divergence=self.continue_after_divergence,
                ordering='HTTP response completion / arrival at monitor',
                identity='explicit_header_hash' if self.identity_header else 'authorization_hash',
                session_ttl_seconds=self.ttl, max_sessions=self.max_sessions,
                note='Observação síncrona após resposta completa; não bloqueia BOLA. '
                     'Resultados finais incluem apenas sessões ainda ativas; '
                     'expirações estão registradas no monitor.jsonl.'))
            self.log.close()
            if self.capture:
                self.capture.close()
            self.closed = True
