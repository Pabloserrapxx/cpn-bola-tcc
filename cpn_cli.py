from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from cpn_bola import InvalidEvent, Token, verificar_conformidade
from cpn_export import export_index, export_net, write_json
from cpn_logs import load_traces
from cpn_openapi import ModelError, build_openapi_cpn

ROOT = Path(__file__).resolve().parent


def run(spec, logs, output, identity_field='user_id', unknown='error', legacy=False,
        backend='snakes', links2cpn_compatible=False):
    if links2cpn_compatible:
        legacy, unknown = True, 'skip'
    net = build_openapi_cpn(spec, legacy_expressions=legacy,
                            links2cpn_compatible=links2cpn_compatible)
    output = Path(output)
    export_net(net, output)
    if logs is None:
        print(f'{net.name}: {len(net.places)} lugares, {len(net.transitions)} transições → {output}')
        return None
    traces = load_traces(logs, identity_field=identity_field,
                         strict_bodies=not links2cpn_compatible)
    summary = verificar_conformidade(net, net.initial_places, traces,
                                     unknown_policy=unknown, record_steps=True, backend=backend,
                                     continue_after_divergence=links2cpn_compatible)
    for i, result in enumerate(summary.results):
        # Não usar IDs fornecidos por logs como caminhos de arquivos.
        export_net(net, output, marking=result.final_marking, stem=f'trace-{i:03d}',
                   title=f'{net.name} — trace {i}: ' + ('aceito' if result.accepted else 'divergente'))
        print(f'  {result.trace_id}: ' + ('ACEITO' if result.accepted else ', '.join(d.kind.value for d in result.divergences)))
    total = sum(len(t.events) for t in traces)
    report = dict(summary=summary, total_events=total,
                  evaluated_events=sum(r.evaluated_events for r in summary.results),
                  skipped_events=sum(r.skipped_events for r in summary.results),
                  unprocessed_events=sum(r.unprocessed_events for r in summary.results),
                  identity_field=identity_field, unknown_policy=unknown, backend=backend,
                  profile='links2cpn' if links2cpn_compatible else 'strict',
                  sources={t.trace_id: [e.source_index for e in t.events] for t in traces},
                  note='Fitness = proporção de traces aceitos; não é acurácia de detecção. '
                       'Divergência exige análise; atividade fora do modelo não comprova ataque.')
    write_json(output / 'resultado.json', report)
    print(f'{net.name}: fitness={summary.fitness}; artefatos em {output}')
    return summary


def run_analysis(spec, catalog, output, *, trace_id=None, identity_field='user_id',
                 legacy=False, target_path=None, **limits):
    from cpn_analysis import analyze_native
    from cpn_snakes import compile_event_domain

    traces = load_traces(catalog, identity_field=identity_field)
    if trace_id is not None:
        traces = [trace for trace in traces if trace.trace_id == trace_id]
    if len(traces) != 1:
        raise ValueError('Selecione exatamente um trace como catálogo com --trace-id.')
    trace = traces[0]
    net = build_openapi_cpn(spec, legacy_expressions=legacy)
    domain = compile_event_domain(net, trace.user_id, trace.events)
    if not domain.executor.net.transition():
        raise ValueError('Catálogo sem bindings válidos; não há comportamento de API para analisar.')
    target = None
    if target_path:
        raw = json.loads(Path(target_path).read_text(encoding='utf-8'))
        raw = raw.get('marking', raw)
        def tuple_value(value):
            return tuple(map(tuple_value, value)) if isinstance(value, list) else value
        target = {place: [Token(token['color'], tuple_value(token['value'])) for token in tokens]
                  for place, tokens in raw.items()}
    report = analyze_native(domain.executor.net, labels=domain.executor.labels, target=target, **limits)
    report['domain'] = dict(trace_id=trace.trace_id, user_id=trace.user_id,
                          event_count=len(trace.events), variants=domain.event_variants,
                          rejected_events=domain.rejected_events,
                          activities_without_bindings=sorted(
                              {t.activity for t in net.transitions.values()} -
                              {label['activity'] for label in domain.executor.labels.values()}),
                          rule='Qualquer ordem e repetição dos bindings válidos deste catálogo; sem novos valores.')
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    write_json(path / 'analise_estados.json', report)
    print(f'Análise SNAKES: {report["state_count"]} estados; grafo completo={report["complete_graph"]}; '
          f'limitada={report["bounded"]}; viva por binding={report["live"]}; '
          f'viva por atividade={report["all_activities_live"]}.')
    print(f'Relatório: {path / "analise_estados.json"}')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='OpenAPI → redes de Petri coloridas → replay de logs (TCC).')
    sub = parser.add_subparsers(dest='command')
    demo = sub.add_parser('demo', help='Executar os quatro cenários sintéticos e exportar as redes.')
    demo.add_argument('--output', default=str(ROOT / 'redes'))
    demo.add_argument('--analyze', action='store_true', help='Analisar o domínio do trace legítimo de cada cenário.')
    model = sub.add_parser('model', help='Gerar uma rede a partir de OpenAPI.')
    replay = sub.add_parser('replay', help='Verificar conformidade de logs JSON/JSONL.')
    analyze = sub.add_parser('analyze', help='Explorar ordens/repetições de um catálogo finito usando SNAKES.')
    for command in (model, replay, analyze):
        command.add_argument('openapi')
        command.add_argument('--output', default='redes/custom')
        command.add_argument('--legacy-expressions', action='store_true',
                             help='Interpretar /authentication.id como /authentication/id (convenção da tese).')
    for command in (model, replay):
        command.add_argument('--links2cpn-compatible', action='store_true',
                             help='Aplicar as convenções de execução observadas no Links2CPN dos autores.')
    replay.add_argument('logs')
    replay.add_argument('--identity-field', default='user_id', help='Campo estável do usuário/sessão; ip é opção explícita.')
    replay.add_argument('--unknown', choices=['error', 'skip'], default='error', help='Política para atividades fora do modelo.')
    replay.add_argument('--fail-on-divergence', action='store_true')
    replay.add_argument('--backend', choices=['snakes', 'python'], default='snakes',
                        help='SNAKES por padrão; Python é referência para comparação diferencial.')
    analyze.add_argument('catalog', help='JSON/JSONL com eventos que delimitam o domínio de bindings.')
    analyze.add_argument('--trace-id')
    analyze.add_argument('--identity-field', default='user_id')
    analyze.add_argument('--target-marking', help='Marcação JSON exata, aceita também trace-NNN.json.')
    analyze.add_argument('--max-states', type=int, default=200)
    analyze.add_argument('--max-depth', type=int, default=20)
    analyze.add_argument('--max-tokens', type=int, default=1000)
    analyze.add_argument('--max-edges', type=int, default=10000)
    analyze.add_argument('--stop-on-unbounded', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command is None or args.command == 'demo':
            output = Path(getattr(args, 'output', ROOT / 'redes'))
            scenarios = ('contas', 'juice_view', 'juice_manipulate', 'memos')
            for name in scenarios:
                run(ROOT / 'examples' / f'{name}.openapi.json', ROOT / 'examples' / f'{name}.logs.json', output / name)
                if getattr(args, 'analyze', False):
                    run_analysis(ROOT / 'examples' / f'{name}.openapi.json',
                                 ROOT / 'examples' / f'{name}.logs.json', output / name, trace_id='legitimo')
            export_index(output, scenarios, include_analysis=getattr(args, 'analyze', False))
            return 0
        if args.command == 'analyze':
            run_analysis(args.openapi, args.catalog, args.output, trace_id=args.trace_id,
                         identity_field=args.identity_field, legacy=args.legacy_expressions,
                         target_path=args.target_marking, max_states=args.max_states,
                         max_depth=args.max_depth, max_tokens=args.max_tokens,
                         max_edges=args.max_edges, stop_on_unbounded=args.stop_on_unbounded)
            return 0
        summary = run(args.openapi, getattr(args, 'logs', None), args.output,
                      getattr(args, 'identity_field', 'user_id'), getattr(args, 'unknown', 'error'),
                      args.legacy_expressions, getattr(args, 'backend', 'snakes'),
                      getattr(args, 'links2cpn_compatible', False))
        return 1 if summary and summary.error_trace_ids and getattr(args, 'fail_on_divergence', False) else 0
    except (ValueError, OSError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
