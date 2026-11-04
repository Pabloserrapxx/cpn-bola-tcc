"""Reexecuta logs públicos fixados por commit nos modelos reconstruídos.

Baixa somente dados; não executa código do repositório de referência.
Os arquivos brutos permanecem em um diretório temporário.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpn_bola import DivergenceType, verificar_conformidade
from cpn_export import write_json
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn

COMMIT = 'd6092c3d85adaf96a0e83a90a3bd2d287cd51325'
BASE = f'https://raw.githubusercontent.com/ailton07/openapi-links-to-CPNs/{COMMIT}/'
CASES = [('logs/memos_login_create_archieve.json', True),
         ('logs/memos_login_and_attack_log.json', False)]


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='cpn-reference-') as directory:
        for name, expected in CASES:
            url = BASE + name
            with urlopen(url, timeout=30) as response:
                data = response.read()
            local = Path(directory) / Path(name).name
            local.write_bytes(data)
            net = build_openapi_cpn(ROOT / 'examples/memos.openapi.json')
            traces = load_traces(local, identity_field='ip')
            summary = verificar_conformidade(net, net.initial_places, traces, unknown_policy='skip')
            if len(summary.results) != 1 or summary.results[0].accepted != expected:
                raise AssertionError(f'Resultado inesperado em {name}')
            result = summary.results[0]
            if not expected and result.divergences[0].kind != DivergenceType.DATA_FLOW:
                raise AssertionError('Esperada divergência de dados no cenário malicioso.')
            row = dict(source=url, sha256=hashlib.sha256(data).hexdigest(),
                       model='examples/memos.openapi.json', accepted=result.accepted,
                       expected_accepted=expected, backend=result.backend, total_events=len(traces[0].events),
                       evaluated_events=result.evaluated_events, skipped_events=result.skipped_events,
                       divergences=[dict(kind=d.kind.value, event_index=d.event_index,
                                         source_record=traces[0].events[d.event_index].source_index,
                                         activity=d.activity) for d in result.divergences])
            results.append(row)
            print(Path(name).name, 'ACEITO' if result.accepted else result.divergences[0].kind.value)
    write_json(ROOT / 'docs/verificacao_logs_publicos.json', dict(
        reference_commit=COMMIT, identity_field='ip', unknown_policy='skip', results=results,
        scope='Reexecução de dois logs de Memos em especificação reconstruída. '
              'Não é reprodução da matriz de confusão da tese nem comparação diferencial com SNAKES.'))


if __name__ == '__main__':
    main()
