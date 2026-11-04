"""Inicia mitmproxy reverso e monitor CPN em modo de observação."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openapi')
    parser.add_argument('--upstream', required=True, help='URL fixa da API, por exemplo http://127.0.0.1:3000')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--output', default='redes/runtime')
    parser.add_argument('--identity-header', help='Cabeçalho de sessão de um coletor confiável; destinado ao laboratório.')
    parser.add_argument('--session-ttl', type=float, default=1800)
    parser.add_argument('--max-sessions', type=int, default=1000)
    parser.add_argument('--max-body-bytes', type=int, default=1048576)
    parser.add_argument('--links2cpn-compatible', action='store_true')
    parser.add_argument('--stop-on-divergence', action='store_true')
    parser.add_argument('--capture-events', action='store_true',
                        help='Gravar corpos e cabeçalhos HTTP para replay; pode conter credenciais.')
    parser.add_argument('--no-monitor', action='store_true', help='Proxy puro para medir custo de encaminhamento.')
    args = parser.parse_args(argv)
    url = urlsplit(args.upstream)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or
            url.password or url.path not in ('', '/') or url.query or url.fragment):
        parser.error('--upstream deve conter apenas esquema HTTP(S), host e porta.')
    if not 1 <= args.port <= 65535:
        parser.error('Porta inválida.')
    if args.session_ttl <= 0 or args.max_sessions <= 0 or args.max_body_bytes <= 0:
        parser.error('Limites devem ser positivos.')
    if importlib.util.find_spec('mitmproxy') is None:
        parser.error('Instale requirements.runtime.txt no ambiente usado para executar este comando.')
    if not args.no_monitor:
        sys.path.insert(0, str(ROOT))
        from cpn_openapi import build_openapi_cpn
        build_openapi_cpn(args.openapi, links2cpn_compatible=args.links2cpn_compatible)
        output = Path(args.output).resolve()
        if (output / 'monitor.jsonl').exists() or (args.capture_events and
                (output / 'eventos_capturados.jsonl').exists()):
            parser.error('Use um diretório de saída novo para preservar as execuções anteriores.')
        os.environ['CPN_RUNTIME_CONFIG'] = json.dumps(dict(
            spec=str(Path(args.openapi).resolve()), output=str(output),
            compatible=args.links2cpn_compatible, identity_header=args.identity_header,
            ttl=args.session_ttl, max_sessions=args.max_sessions,
            max_body_bytes=args.max_body_bytes, capture_events=args.capture_events,
            continue_after_divergence=not args.stop_on_divergence))
    from mitmproxy.tools.main import mitmdump
    command = ['--mode', f'reverse:{args.upstream.rstrip("/")}',
               '--listen-host', args.host, '--listen-port', str(args.port),
               '--set', 'flow_detail=0']
    if not args.no_monitor:
        command += ['-s', str(ROOT / 'runtime/mitmproxy_addon.py')]
    mitmdump(command)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
