"""Alimenta sessões incrementais; --compare-offline valida cada marcação.

JSONL/NDJSON é lido linha a linha; arrays JSON são carregados pelo parser atual.
O relatório de eventos é gravado progressivamente, sem guardar corpos HTTP.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from math import ceil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpn_bola import Trace, replay_trace
from cpn_export import jsonable, write_json
from cpn_incremental import ReplaySession
from cpn_logs import event_from_record, read_records
from cpn_openapi import build_openapi_cpn


def iter_events(path, *, identity_field='user_id', strict_bodies=True):
    path = Path(path)
    def records():
        if path.suffix.lower() in ('.jsonl', '.ndjson'):
            with path.open(encoding='utf-8') as stream:
                for number, line in enumerate(stream, 1):
                    if line.strip():
                        try:
                            yield number, json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValueError(f'JSON inválido na linha {number}: {exc.msg}') from exc
        else:
            yield from enumerate(read_records(path), 1)
    owners, explicit = {}, set()
    for number, record in records():
        if not isinstance(record, dict):
            raise ValueError(f'Registro {number}: esperado objeto.')
        if 'events' in record:
            trace_id = str(record.get('trace_id', f'trace-{number}'))
            user = record.get('user_id')
            if not isinstance(record['events'], list) or not user:
                raise ValueError(f'Trace {trace_id}: user_id/events inválidos.')
            if trace_id in owners:
                raise ValueError(f'trace_id duplicado: {trace_id}')
            owners[trace_id] = str(user)
            explicit.add(trace_id)
            # Uma sessão vazia também deve constar no relatório.
            yield trace_id, str(user), None
            for index, raw in enumerate(record['events'], 1):
                event = event_from_record(raw, index, identity_field, user,
                                          strict_bodies=strict_bodies)
                if event.user_id != str(user):
                    raise ValueError(f'Trace {trace_id} mistura identidades.')
                yield trace_id, str(user), event
        else:
            event = event_from_record(record, number, identity_field,
                                      strict_bodies=strict_bodies)
            trace_id = str(record.get('trace_id', event.user_id))
            if trace_id in explicit:
                raise ValueError(f'trace_id duplicado: {trace_id}')
            if trace_id in owners and owners[trace_id] != event.user_id:
                raise ValueError(f'Trace {trace_id} mistura identidades.')
            owners[trace_id] = event.user_id
            yield trace_id, event.user_id, event


def same_marking(a, b):
    return {p: Counter(tokens) for p, tokens in a.items()} == {
        p: Counter(tokens) for p, tokens in b.items()}


def equivalent(a, b):
    return (a.accepted == b.accepted and a.divergences == b.divergences
            and same_marking(a.final_marking, b.final_marking)
            and a.evaluated_events == b.evaluated_events
            and a.skipped_events == b.skipped_events
            and a.unprocessed_events == b.unprocessed_events)


def feed(spec, logs, output, *, identity_field='user_id', backend='snakes',
         compatible=False, legacy=False, unknown='error', compare=False):
    if compatible:
        legacy, unknown = True, 'skip'
    model = build_openapi_cpn(spec, legacy_expressions=legacy,
                             links2cpn_compatible=compatible)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    sessions, histories, timings = {}, {}, []
    comparisons = mismatches = 0
    with (output / 'eventos.jsonl').open('w', encoding='utf-8') as stream:
        for trace_id, user_id, event in iter_events(logs, identity_field=identity_field,
                                                   strict_bodies=not compatible):
            if trace_id not in sessions:
                sessions[trace_id] = ReplaySession(model, trace_id, user_id,
                    unknown_policy=unknown, backend=backend,
                    continue_after_divergence=compatible)
                if compare:
                    histories[trace_id] = []
            session = sessions[trace_id]
            if event is None:
                continue
            result = session.process(event)
            if result.status != 'not_processed':
                timings.append(result.elapsed_ns)
            row = dict(trace_id=trace_id, result=result)
            if compare:
                # Referência independente: replay original de cada prefixo.
                # Custo quadrático intencional, fora do tempo medido de process().
                histories[trace_id].append(event)
                offline = replay_trace(model, model.initial_places,
                    Trace(trace_id, user_id, tuple(histories[trace_id])),
                    unknown_policy=unknown, backend=backend, record_steps=True,
                    continue_after_divergence=compatible)
                incremental = session_result(session)
                expected_status = ('not_processed' if offline.unprocessed_events else
                    'divergent' if any(d.event_index == result.event_index
                                       for d in offline.divergences) else
                    'fired' if any(s['event_index'] == result.event_index
                                   for s in offline.steps) else 'outside_model')
                row['offline_status'] = expected_status
                equal = equivalent(offline, incremental) and result.status == expected_status
                if result.fired:
                    equal = equal and bool(offline.steps) and (
                        offline.steps[-1]['event_index'] == result.event_index)
                else:
                    equal = equal and not any(s['event_index'] == result.event_index
                                              for s in offline.steps)
                row['offline_equivalent'] = equal
                comparisons += 1
                mismatches += int(not equal)
            stream.write(json.dumps(jsonable(row), ensure_ascii=False) + '\n')
    results = [s.finish() for s in sessions.values()]
    final_comparisons = []
    if compare:
        for result in results:
            session = sessions[result.trace_id]
            offline = replay_trace(model, model.initial_places,
                Trace(session.trace_id, session.user_id, tuple(histories[result.trace_id])),
                unknown_policy=unknown, backend=backend,
                continue_after_divergence=compatible)
            equal = equivalent(offline, result)
            final_comparisons.append(dict(trace_id=result.trace_id, equivalent=equal))
            mismatches += int(not equal)
    ordered = sorted(timings)
    def percentile(q):
        return ordered[max(0, ceil(q * len(ordered)) - 1)] / 1e6 if ordered else None
    report = dict(profile='links2cpn' if compatible else 'strict', backend=backend,
        identity_field=identity_field, unknown_policy=unknown, results=results,
        comparison=dict(enabled=compare, compared_prefixes=comparisons,
                        mismatches=mismatches, final_traces=final_comparisons,
                        equivalent=mismatches == 0 if compare else None),
        processing_ms=dict(count=len(timings),
            mean=sum(timings) / len(timings) / 1e6 if timings else None,
            p50=percentile(.50), p95=percentile(.95), p99=percentile(.99)),
        state=dict(sessions=len(sessions), tokens=sum(
            sum(p.marking.values()) for s in sessions.values() for p in s.cpn.places.values()),
            native_variants=sum(len(s.executor.variants) if s.executor else 0 for s in sessions.values())),
        note='Tempo de process() inclui verificações e disparo; exclui inicialização, '
             'parsing, exportação e comparação. Não é latência de API nem benchmark isolado. '
             'Sessões ficam em memória até o fim; sem expiração ou persistência em disco.')
    write_json(output / 'resultado.json', report)
    return report


def session_result(session):
    """Resumo provisório sem fechar a sessão; sem-eventos só se aplica ao prefixo."""
    from cpn_bola import Divergence, DivergenceType, ReplayResult
    divergences = list(session.divergences)
    if not session.evaluated and not divergences:
        divergences.append(Divergence(DivergenceType.NO_MODELED_EVENTS,
            session.trace_id, -1, '', 'Trace sem eventos avaliáveis pelo modelo.'))
    return ReplayResult(session.trace_id, not divergences, divergences, session.snapshot(),
        session.evaluated, session.skipped, session.unprocessed, backend=session.backend)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openapi')
    parser.add_argument('logs')
    parser.add_argument('--output', default='redes/incremental')
    parser.add_argument('--identity-field', default='user_id')
    parser.add_argument('--backend', choices=['snakes', 'python'], default='snakes')
    parser.add_argument('--unknown', choices=['error', 'skip'], default='error')
    parser.add_argument('--legacy-expressions', action='store_true')
    parser.add_argument('--links2cpn-compatible', action='store_true')
    parser.add_argument('--compare-offline', action='store_true')
    parser.add_argument('--fail-on-divergence', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = feed(args.openapi, args.logs, args.output, identity_field=args.identity_field,
            backend=args.backend, compatible=args.links2cpn_compatible,
            legacy=args.legacy_expressions, unknown=args.unknown, compare=args.compare_offline)
        print(f"{len(report['results'])} sessões; comparação: {report['comparison']}")
        print(f'Relatórios em {args.output}')
        return 1 if report['comparison']['mismatches'] or (args.fail_on_divergence and
                    any(not r.accepted for r in report['results'])) else 0
    except (ValueError, OSError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
