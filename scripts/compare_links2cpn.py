"""Comparação diferencial com o Replay dos autores, em processo/ambiente separado.

Usa os arquivos originais sem alterações e controles autorais com entradas iguais.
Permite medir tanto o perfil rigoroso quanto o perfil compatível do produto.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cpn_bola import verificar_conformidade
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn
from cpn_export import write_json
from scripts.comparison_fixtures import make

COMMIT = 'd6092c3d85adaf96a0e83a90a3bd2d287cd51325'
REPO = 'https://github.com/ailton07/openapi-links-to-CPNs'
ORIGINALS = [
    ('original-contas','BOLA_Example_login_and_accounts_id.yaml','combined_bola_example.json'),
    ('original-view-legitimo','JuiceShop.yaml','combined_login.json'),
    ('original-view-multiusuario','JuiceShop.yaml','combined_login_multiuser.json'),
    ('original-view-ataque','JuiceShop.yaml','real_logs_juice_shop_signup_view_basket.log'),
    ('original-manipulate','OWASP-Juice-Shop-manipulate-basket.yaml','real_log_Juice_Shop_manipulate_basket.log'),
    ('original-experimento','OWASP-Juice-Shop-experiment.yaml','real_logs_experiment.log'),
    ('original-memos-legitimo','memos-API-3.0.yml','memos_login_create_archieve.json'),
    ('original-memos-ataque','memos-API-3.0.yml','memos_login_and_attack_log.json'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(user):
    return hashlib.sha256(str(user).encode()).hexdigest()[:16]


def ref_projection(marking, owner):
    """Mantém multiplicidade e aninhamento; projeta identidades de recursos escalares."""
    values = Counter()
    def visit(place, token, depth=0):
        if 'nested_multiset' in token:
            for inner in token['nested_multiset']:
                visit(place, inner, depth+1)
        elif 'value' in token:
            fields = token['value']
            if fields.get('user_id') != owner:
                return
            resource = {k:v for k,v in fields.items() if k != 'user_id'}
            values[(place.lower(), json.dumps(resource, sort_keys=True, ensure_ascii=False), depth)] += 1
    for place, tokens in marking.items():
        for token in tokens:
            visit(place, token)
    return values


def own_projection(net, marking, owner):
    values = Counter()
    for place, tokens in marking.items():
        if place == 'p_user':
            continue
        field = net.metadata['fields'][place]
        operation = next(v for v in net.metadata['transitions'].values() if v['operation'] == field['operation'])
        name = field['name'].lstrip('/')
        comparable_place = f'{name} {operation["method"]}-{operation["path"]}'.lower()
        for token in tokens:
            user, frozen = token.value
            if identity(user) != owner:
                continue
            tag, value = frozen
            if tag not in ('str', 'int', 'float', 'bool', 'null'):
                value = repr(frozen)
            values[(comparable_place, json.dumps({name:str(value)}, sort_keys=True, ensure_ascii=False), 0)] += 1
    return values


def project_json(counts):
    return [dict(place=p, resource=json.loads(value), nesting=depth, count=count)
            for (p,value,depth), count in sorted(counts.items())]


def diagnostic(divergence):
    text = divergence.message
    for prefix, code in [('Resposta fora do schema', 'response_schema'),
                         ('Recurso de ', 'link_target_schema'),
                         ('Não foi possível obter', 'link_expression'),
                         ('O recurso observado', 'postcondition')]:
        if text.startswith(prefix):
            return code
    return divergence.kind.value


def run_ours(case, profile='strict'):
    compatible = profile == 'links2cpn'
    output = dict(engine='TCC', python=sys.version.split()[0], backend='snakes',
                  legacy_expressions=True, identity_field='ip', unknown_policy='skip',
                  profile=profile,
                  failed_records=[], successful_records=[], skipped_records=[],
                  unprocessed_records=[], snapshots={})
    stage = 'model'
    try:
        net = build_openapi_cpn(case['spec'], legacy_expressions=True,
                                links2cpn_compatible=compatible)
        output['places'] = list(net.places)
        output['transitions'] = list(net.transitions)
        stage = 'logs'
        traces = load_traces(case['logs'], identity_field='ip', strict_bodies=not compatible)
        stage = 'replay'
        summary = verificar_conformidade(
            net, net.initial_places, traces, unknown_policy='skip', record_steps=True,
            continue_after_divergence=compatible)
        output['divergences'] = []
        output['record_count'] = sum(len(t.events) for t in traces)
        owners = {}
        projections = {}
        for trace, result in zip(traces, summary.results):
            failed = {d.event_index: d for d in result.divergences if d.event_index >= 0}
            fired = {step['event_index']: step for step in result.steps}
            for index, event in enumerate(trace.events):
                record = event.source_index
                owners[record] = identity(event.user_id)
                first_failed = min(failed, default=None)
                if not compatible and first_failed is not None and index > first_failed:
                    status = 'unprocessed_records'
                elif index in failed:
                    status = 'failed_records'
                    div = failed[index]
                    output['divergences'].append(dict(record=record, kind=div.kind.value, diagnostic=diagnostic(div)))
                elif index in fired:
                    status = 'successful_records'
                else:
                    status = 'skipped_records'
                output[status].append(record)
                if index in fired:
                    projections[record] = own_projection(net, fired[index]['marking'], owners[record])
        output['completed'] = True
        return output, owners, projections
    except Exception as exc:
        output.update(completed=False, exception_type=type(exc).__name__, failure_stage=stage)
        record = re.search(r'Registro (\d+):', str(exc))
        if record:
            output['failure_record'] = int(record[1])
        if 'não contém JSON válido' in str(exc):
            output['failure_reason'] = 'invalid_json_body'
        if stage == 'model':
            output['model_diagnostic'] = str(exc)[:300]
        return output, {}, {}


def compare_case(case, python_ref, checkout, output_dir, profile='strict'):
    input_hashes = {key:sha(case[key]) for key in ('spec','logs')}
    with tempfile.TemporaryDirectory(prefix='cpn-compare-worker-') as directory:
        output_path = Path(directory) / 'reference.json'
        env = {**os.environ, 'PYTHONHASHSEED':'0'}
        env.pop('PYTHONPATH', None)
        command = [str(python_ref), str(ROOT/'scripts/links2cpn_worker.py'), str(checkout),
                   case['spec'], case['logs'], str(output_path)]
        process = subprocess.run(command, cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
        if process.returncode or not output_path.exists():
            raise RuntimeError(f'Worker falhou em {case["name"]}; saída={process.returncode}')
        reference = json.loads(output_path.read_text())
    ours, owners, projections = run_ours(case, profile)
    if any(sha(case[key]) != value for key,value in input_hashes.items()):
        raise RuntimeError(f'Entradas modificadas durante a comparação: {case["name"]}')
    ref_snapshots = {s['record']: s['marking'] for s in reference.pop('snapshots') if s['record']}
    reference.pop('final_marking', None)
    ours.pop('snapshots', None)
    # Não incorporar tokens, corpos de log ou credenciais ao relatório persistente.
    records, common, matching = [], 0, 0
    mismatches = []
    if reference['completed'] and ours['completed']:
        total = max(reference['record_count'], ours['record_count'])
        def status(result, record):
            for key, label in [('successful_records','fired'), ('failed_records','failed'),
                               ('skipped_records','outside_model'), ('unprocessed_records','not_processed')]:
                if record in result.get(key, []):
                    return label
            return 'not_processed'
        for record in range(1,total+1):
            a, b = status(reference,record), status(ours,record)
            row = dict(record=record, reference=a, ours=b)
            if 'not_processed' not in (a,b):
                common += 1
                matching += int(a==b)
            if a == b == 'fired':
                expected = ref_projection(ref_snapshots[record], owners[record])
                actual = projections[record]
                row['resource_marking_equal'] = expected == actual
                if expected != actual:
                    mismatches.append(dict(record=record, reference=project_json(expected), ours=project_json(actual)))
            records.append(row)
    first_ref = min(reference['failed_records'], default=None)
    first_ours = min(ours['failed_records'], default=None)
    result = dict(name=case['name'], dataset=case['dataset'], purpose=case.get('purpose'),
                  inputs=dict(spec_sha256=input_hashes['spec'], logs_sha256=input_hashes['logs'],
                              specification=Path(case['spec']).name, log=Path(case['logs']).name,
                              same_files_for_both_engines=True), reference=reference, ours=ours,
                  comparable_event_count=common, matching_event_count=matching,
                  same_event_outcomes=(all(e['reference']==e['ours'] for e in records)
                                       if reference['completed'] and ours['completed'] else None),
                  comparable_marking_count=sum('resource_marking_equal' in e for e in records),
                  first_failure_agreement=(first_ref==first_ours if reference['completed'] and ours['completed'] else None),
                  resource_marking_mismatches=mismatches, events=records)
    write_json(output_dir / (case['name']+'.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--reference-python', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/comparacao_links2cpn')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--ours-profile', choices=['strict', 'links2cpn'], default='strict')
    args = parser.parse_args()
    # Não resolver o symlink do executável: isso descartaria o virtualenv.
    checkout, python_ref = args.checkout.resolve(), args.reference_python.absolute()
    ref_version = subprocess.check_output([str(python_ref), '-c',
        'import sys; print(".".join(map(str,sys.version_info[:3])))'],text=True).strip()
    if not ref_version.startswith('3.9.'):
        raise ValueError('Esta comparação exige Python 3.9 para a referência.')
    head = subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    if head != COMMIT:
        raise ValueError(f'Checkout deve estar no commit {COMMIT}.')
    changed = subprocess.check_output(['git','-C',str(checkout),'diff','HEAD','--'],text=True)
    if changed:
        raise ValueError('Checkout da referência contém alterações nos algoritmos/entradas.')
    args.output.mkdir(parents=True,exist_ok=True)
    cases = [dict(name=name, dataset='original', spec=str(checkout/'examples'/spec), logs=str(checkout/'logs'/logs))
             for name,spec,logs in ORIGINALS]
    cases += make(ROOT/'examples/comparison')
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        results = list(executor.map(
            lambda c: compare_case(c,python_ref,checkout,args.output,args.ours_profile), cases))
    normalize = lambda name: re.sub('[-_.]+','-',name).lower()
    versions = {normalize(name):version for name,version in results[0]['reference']['dependencies'].items()}
    requirements_check = []
    for line in (checkout/'requirements.txt').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            name, required = line.split('==')
            observed = versions.get(normalize(name))
            requirements_check.append(dict(package=name,required=required,observed=observed,match=required==observed))
    if not all(check['match'] for check in requirements_check):
        raise RuntimeError('As dependências da referência não correspondem às 51 versões fixadas; execute o setup.')
    manifest = dict(reference_repository=REPO, reference_commit=COMMIT,
                    ours_profile=args.ours_profile,
                    reference_python=ref_version, ours_python=sys.version.split()[0],
                    observer='scripts/links2cpn_worker.py; substitui somente geração de PNG por captura de marcação.',
                    source_sha256={str(p.relative_to(checkout)):sha(p) for p in sorted((checkout/'src').rglob('*.py'))},
                    ours_source_sha256={str(p.relative_to(ROOT)):sha(p) for p in
                                        sorted(ROOT.glob('cpn_*.py')) + [ROOT/'scripts/compare_links2cpn.py',
                                        ROOT/'scripts/links2cpn_worker.py',ROOT/'scripts/comparison_fixtures.py']},
                    reference_requirements_sha256=sha(checkout/'requirements.txt'),
                    reference_requirements_check=requirements_check,
                    ours_dependencies={d.metadata['Name']:d.version for d in metadata.distributions()},
                    projection='Comparação de recursos escalares por usuário: omite p_user auxiliar, '
                               'alinha nomes de lugares e converte IDs para string; preserva multiplicidades '
                               'e aninhamento de MultiSet. Não é equivalência de tipos nem equivalência formal.',
                    scope='Oito pares originais e treze controles. Resultados descritivos, não acurácia BOLA.',
                    cases=[])
    lines = ['# Comparação executada com Links2CPN', '',
             f'Perfil da implementação do TCC: `{args.ours_profile}`.', '',
             f'Referência: [{COMMIT[:12]}]({REPO}/tree/{COMMIT}). Código original sem alterações; '
             'somente a geração de PNG foi substituída por observação das marcações.', '',
             '| Caso | Original completou | TCC completou | Falhas original | Falhas TCC | Primeiro erro igual | Desfechos iguais no arquivo inteiro | Marcações comparadas / diferentes* |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for result in results:
        ref, ours = result['reference'],result['ours']
        row = dict(name=result['name'],dataset=result['dataset'],reference_completed=ref['completed'],
                   ours_completed=ours['completed'],reference_failed_records=ref['failed_records'],
                   ours_failed_records=ours['failed_records'],first_failure_agreement=result['first_failure_agreement'],
                   same_event_outcomes=result['same_event_outcomes'],comparable_marking_count=result['comparable_marking_count'],
                   resource_marking_mismatch_records=[x['record'] for x in result['resource_marking_mismatches']])
        manifest['cases'].append(row)
        def yes(value):
            return '—' if value is None else ('sim' if value else 'não')
        def errors(record):
            values=record['failed_records']
            return str(values) if len(values)<=12 else f'{len(values)} falhas (ver JSON)'
        lines.append(f'| [{result["name"]}]({result["name"]}.json) | {yes(ref["completed"])} | {yes(ours["completed"])} | '
                     f'{errors(ref)} | {errors(ours)} | {yes(row["first_failure_agreement"])} | '
                     f'{yes(row["same_event_outcomes"])} | {row["comparable_marking_count"]} / '
                     f'{row["resource_marking_mismatch_records"]} |')
        print(result['name'], 'ref=',ref['completed'], 'tcc=',ours['completed'],
              'first=',row['first_failure_agreement'], 'marking_diffs=',len(result['resource_marking_mismatches']))
    lines += ['', '*Somente eventos disparados por ambos e a projeção de recursos descrita no manifest. '
              'Não há comparação de marcação após eventos que um dos programas não processou.', '',
              '“Completou” significa que o programa terminou sem exceção externa; pode ter encontrado divergências. '
              'Uma lista vazia de falhas não significa êxito quando “completou” é não.', '',
              'Veja [a interpretação metodológica](../comparacao_links2cpn.md) e [o manifest](manifest.json).']
    write_json(args.output/'manifest.json',manifest)
    (args.output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
