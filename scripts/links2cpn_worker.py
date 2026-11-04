"""Observador do Replay ORIGINAL; executar no Python/ambiente da referência.

Única substituição: DrawUtils.draw salva a marcação em vez de gerar PNG.
Não altera o parser, bindings, transições, retry ou tratamento de erros.
"""
import argparse
from contextlib import redirect_stdout
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('checkout')
    parser.add_argument('spec')
    parser.add_argument('logs')
    parser.add_argument('output')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.checkout) / 'src/openapiLinks2CPNs'))
    output = {'engine': 'Links2CPN', 'python': sys.version.split()[0], 'snapshots': [],
              'intervention': 'DrawUtils.draw replaced by read-only marking capture; original Replay called.'}
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            from replay.replay import Replay
            from utils.draw_utils import DrawUtils
            from snakes.data import MultiSet
            from utils.log_utils import LogUtils
            records = LogUtils.load_logs(args.logs)
            output['record_count'] = len(records)
            last_net = [None]

            def token_data(token):
                if isinstance(token, MultiSet):
                    return {'nested_multiset': [token_data(t) for t in token]}
                if hasattr(token, 'get_dict'):
                    value = token.get_dict()
                    if 'user_id' in value:
                        value['user_id'] = hashlib.sha256(str(value['user_id']).encode()).hexdigest()[:16]
                    return {'value': value}
                return {'unsupported_type': type(token).__name__}

            def marking(net):
                return {p.name: [token_data(t) for t in p.tokens] for p in net.place()}

            def capture(draws, name, net):
                match = re.match(r'line-(\d+)-fire-line', name)
                index = int(match[1]) if match else 0
                output['snapshots'].append({'record': index, 'marking': marking(net)})
                draws[name] = None
                last_net[0] = net
                output['places'] = [p.name for p in net.place()]
                output['transitions'] = [t.name for t in net.transition()]

            DrawUtils.draw = staticmethod(capture)
            Replay.replay_execution_on_log(args.spec, args.logs)
            if last_net[0] is not None:
                output['final_marking'] = marking(last_net[0])
        output['completed'] = True
    except Exception as exc:
        output['completed'] = False
        output['exception_type'] = type(exc).__name__
        # Evitar copiar corpos de log ou credenciais na mensagem da exceção.
    text = stdout.getvalue()
    output['failed_records'] = [int(n) for n in re.findall(r'Fire error, Line (\d+):', text)]
    output['skipped_records'] = [int(n) for n in re.findall(r'Line (\d+) is not present in the model:', text)]
    output['successful_records'] = [s['record'] for s in output['snapshots'] if s['record']]
    output['dependencies'] = {d.metadata['Name']: d.version for d in metadata.distributions()}
    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
