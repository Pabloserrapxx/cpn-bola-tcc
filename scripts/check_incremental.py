"""Registra a equivalência offline/incremental nos exemplos e 13 controles locais."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cpn_export import write_json
from scripts.feed_events import feed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'docs/avaliacao_incremental.json'))
    args = parser.parse_args(argv)
    cases = []
    specs = sorted((ROOT / 'examples').glob('*.openapi.json'))
    specs += sorted((ROOT / 'examples/comparison').glob('*.openapi.json'))
    with tempfile.TemporaryDirectory(prefix='cpn-incremental-') as directory:
        for spec in specs:
            logs = spec.with_name(spec.name.replace('.openapi.', '.logs.'))
            for compatible in (False, True):
                report = feed(spec, logs, Path(directory),
                    identity_field='ip' if spec.parent.name == 'comparison' else 'user_id',
                    legacy=True, compatible=compatible, compare=True)
                cases.append(dict(spec=str(spec.relative_to(ROOT)),
                    spec_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),
                    logs_sha256=hashlib.sha256(logs.read_bytes()).hexdigest(),
                    profile=report['profile'], comparison=report['comparison'],
                    processing_ms=report['processing_ms'], state=report['state']))
    passed = all(c['comparison']['equivalent'] for c in cases)
    report = dict(equivalent=passed, cases=cases, backend='snakes',
        python=sys.version.split()[0],
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'cpn_bola.py', ROOT/'cpn_incremental.py', ROOT/'cpn_snakes.py',
                      ROOT/'cpn_openapi.py', ROOT/'cpn_logs.py',
                      ROOT/'scripts/feed_events.py', ROOT/'scripts/check_incremental.py']},
        scope='Quatro exemplos reconstruídos e treze controles, em dois perfis. '
              'Não reexecuta o Links2CPN original. Compara o replay offline local '
              'independente com sessões incrementais após cada prefixo e no encerramento. '
              'Tempos coletados com comparação habilitada não são benchmark de capacidade.')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    print(f'{len(cases)} combinações; equivalência={passed}; relatório em {output}')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
