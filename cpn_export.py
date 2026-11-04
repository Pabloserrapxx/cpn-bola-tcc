"""Artefatos SVG/DOT/JSON independentes de CPN Tools e Graphviz."""
from dataclasses import asdict, is_dataclass
from enum import Enum
from html import escape
import json
from pathlib import Path
import textwrap


def jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export_net(net, directory, *, marking=None, stem='rede', title=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marking = marking if marking is not None else net.snapshot()
    nodes = list(net.places) + list(net.transitions)
    ids = {name: f'n{i}' for i, name in enumerate(nodes)}
    q = lambda x: json.dumps(x, ensure_ascii=False)
    dot = ['digraph CPN {', 'rankdir=LR;', 'node [fontname="sans-serif"];']
    for node in nodes:
        is_place = node in net.places
        label = node + (f'\n{len(marking.get(node, []))} token(s)' if is_place else '')
        dot.append(f'{ids[node]} [shape={"ellipse" if is_place else "box"}, label={q(label)}];')
    for arc in net.metadata.get('arcs', []):
        dot.append(f'{ids[arc["source"]]} -> {ids[arc["target"]]} [label={q(arc["label"])}];')
    dot.append('}')
    (directory / f'{stem}.dot').write_text('\n'.join(dot) + '\n', encoding='utf-8')
    write_json(directory / f'{stem}.json', dict(name=net.name, initial_places=net.initial_places,
                                               places=list(net.places), marking=marking, **net.metadata))
    _svg(net, marking, directory / f'{stem}.svg', title or net.name)


def _svg(net, marking, path, title):
    info = net.metadata.get('transitions', {})
    targeted = {field['operation'] for field in net.metadata.get('fields', {}).values()}
    left = [t for t, data in info.items() if data['operation'] not in targeted]
    right = [t for t in info if t not in left]
    resources = [p for p in net.places if p != 'p_user']
    rows = max(len(left), len(right), len(resources), 1)
    graph_height = 210 + rows * 210
    positions = {'p_user': (640, 125)}
    for names, x in [(left, 255), (resources, 640), (right, 1025)]:
        for i, name in enumerate(names):
            positions[name] = (x, 300 + 210 * i)
    token_lines = []
    for place in net.places:
        tokens = marking.get(place, [])
        label = 'p_user' if place == 'p_user' else _place_label(net, place)
        token_lines.append(label + ': ' + (', '.join(map(str, tokens)) if tokens else '∅'))
    token_lines = [line for text in token_lines for line in textwrap.wrap(text, 135)]
    height = graph_height + 105 + 24 * len(token_lines)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="{height}" viewBox="0 0 1280 {height}">',
           '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#52657a"/></marker></defs>',
           '<rect width="100%" height="100%" fill="#f8fafc"/>',
           f'<text x="35" y="42" font-family="sans-serif" font-size="24" font-weight="bold">{escape(title)}</text>',
           '<text x="35" y="68" font-family="sans-serif" font-size="14" fill="#475569">Círculos: lugares · Retângulos: transições · Retorno: preserva recurso · Tokens: (usuário, valor tipado)</text>']
    for arc in net.metadata.get('arcs', []):
        source, target = arc['source'], arc['target']
        x1, y1 = positions[source]
        x2, y2 = positions[target]
        user_arc = 'p_user' in (source, target)
        dx, dy = x2-x1, y2-y1
        length = max((dx*dx+dy*dy)**.5, 1)
        rectangle_radius = min(162 / max(abs(dx/length), 1e-9),
                               48 / max(abs(dy/length), 1e-9))
        radius1 = 55 if source in net.places else rectangle_radius + 2
        radius2 = 55 if target in net.places else rectangle_radius + 2
        start = (x1+dx/length*radius1, y1+dy/length*radius1)
        end = (x2-dx/length*radius2, y2-dy/length*radius2)
        bend = 32 if not user_arc else 15
        cx, cy = (start[0]+end[0])/2-dy/length*bend, (start[1]+end[1])/2+dx/length*bend
        svg.append(f'<path d="M{start[0]},{start[1]} Q{cx},{cy} {end[0]},{end[1]}" stroke="{("#94a3b8" if user_arc else "#52657a")}" fill="none" stroke-width="1.5" marker-end="url(#arrow)"><title>{escape(arc["label"])}</title></path>')
        if not user_arc:
            label = arc['label'].replace('$response.body#', 'resp#').replace('$request.body#', 'req#')
            svg.append(f'<text x="{cx}" y="{cy-6}" text-anchor="middle" font-family="monospace" font-size="11" fill="#334155">{escape(label)}</text>')
    for name, (x, y) in positions.items():
        if name in net.places:
            svg.append(f'<circle cx="{x}" cy="{y}" r="53" fill="#dbeafe" stroke="#2563eb" stroke-width="2"/>')
            label = 'USER' if name == 'p_user' else 'RESOURCE'
            lines = [label, f'{len(marking.get(name, []))} token(s)']
            extra = 'p_user' if name == 'p_user' else _place_label(net, name)
            svg.append(f'<text x="{x}" y="{y+78}" text-anchor="middle" font-family="sans-serif" font-size="13">{escape(extra)}</text>')
        else:
            svg.append(f'<rect x="{x-162}" y="{y-48}" width="324" height="96" rx="5" fill="#dcfce7" stroke="#15803d" stroke-width="2"/>')
            data = info[name]
            lines = [data['method'].upper() + ' ' + data['status'], data['path']]
        for i, line in enumerate(lines):
            svg.append(f'<text x="{x}" y="{y-6+i*22}" text-anchor="middle" font-family="monospace" font-size="15">{escape(line)}</text>')
    svg.append(f'<text x="35" y="{graph_height+45}" font-family="sans-serif" font-size="18" font-weight="bold">Marcação</text>')
    for i, line in enumerate(token_lines):
        svg.append(f'<text x="35" y="{graph_height+77+24*i}" font-family="monospace" font-size="13">{escape(line)}</text>')
    svg.append('</svg>')
    path.write_text('\n'.join(svg), encoding='utf-8')


def _place_label(net, name):
    field = net.metadata['fields'][name]
    return f'{field["operation"]} / {field["location"]}:{field["name"] or "/"}'


def export_index(directory, scenarios, *, include_analysis=False):
    directory = Path(directory)
    data = []
    for name in scenarios:
        model = json.loads((directory / name / 'rede.json').read_text(encoding='utf-8'))
        report = json.loads((directory / name / 'resultado.json').read_text(encoding='utf-8'))
        analysis_path = directory / name / 'analise_estados.json'
        analysis = None
        if include_analysis and analysis_path.exists():
            raw = json.loads(analysis_path.read_text(encoding='utf-8'))
            analysis = {key: raw[key] for key in ('bounded', 'live', 'complete_graph', 'state_count')}
            analysis['trace_id'] = raw['domain']['trace_id']
        data.append(dict(folder=name, name=model['name'], report=report, analysis=analysis,
                         places=len(model['places']), transitions=len(model['transitions'])))
    embedded = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    html = r'''<!doctype html>
<html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redes de Petri — TCC</title>
<style>
body{margin:0;background:#f1f5f9;color:#172033;font:16px system-ui,sans-serif}
main{max-width:1280px;margin:auto;padding:28px}h1{font-size:28px;margin:0 0 12px}
p{line-height:1.6}label{display:inline-block;margin:10px 24px 18px 0}select{display:block;font:inherit;padding:10px;margin-top:6px;max-width:100%}
img{width:100%;background:white;border:1px solid #cbd5e1;border-radius:8px}a{color:#1d4ed8}
#status{padding:16px;background:white;border-left:4px solid #2563eb;margin:8px 0 20px;white-space:pre-wrap}
small{color:#475569}nav a{margin-right:22px}
</style>
<main><h1>Redes de Petri coloridas para APIs</h1>
<p>Quatro cenários reconstruídos a partir da tese de Ailton Santos Filho. Selecione uma rede e uma execução para visualizar os tokens disponíveis ao final do trace.</p>
<label>Rede<select id="model"></select></label><label>Marcação<select id="trace"></select></label>
<div id="status" role="status"></div><nav><a id="svg">Abrir SVG</a><a id="report">Relatório JSON</a><a id="dot">Modelo DOT</a><a id="analysis" hidden>Análise de estados</a></nav>
<p><img id="diagram" alt="Diagrama da rede de Petri selecionada"></p>
<small>Dados da demonstração são sintéticos. Fitness é a fração de traces aceitos, não acurácia de detecção. A estrutura inicial tem marcação vazia; p_user é populado no começo de cada trace.</small>
</main><script>
const models = __DATA__;
const modelSelect = document.getElementById('model');
const traceSelect = document.getElementById('trace');
for (const [i, model] of models.entries()) modelSelect.add(new Option(model.name, i));
function selectModel(){
 const model=models[modelSelect.value];
 traceSelect.replaceChildren(new Option('Estrutura inicial', '-1'));
 model.report.summary.results.forEach((r,i)=>traceSelect.add(new Option(r.trace_id+' — '+(r.accepted?'aceito':'divergente'),i)));
 selectTrace();
}
function selectTrace(){
 const model=models[modelSelect.value], index=Number(traceSelect.value);
 const stem=index<0?'rede':'trace-'+String(index).padStart(3,'0');
 document.getElementById('diagram').src=model.folder+'/'+stem+'.svg';
 document.getElementById('svg').href=model.folder+'/'+stem+'.svg';
 document.getElementById('dot').href=model.folder+'/rede.dot';
 document.getElementById('report').href=model.folder+'/resultado.json';
 document.getElementById('analysis').hidden=!model.analysis;
 document.getElementById('analysis').href=model.folder+'/analise_estados.json';
 let text=model.places+' lugares · '+model.transitions+' transições · Fitness dos exemplos: '+model.report.summary.fitness.toFixed(3);
 if(model.analysis){
  const a=model.analysis, answer=v=>v===null?'inconclusivo':v?'sim':'não';
  text+='\nDomínio analisado: '+a.trace_id+' · '+a.state_count+' estados · Grafo completo: '+answer(a.complete_graph)+'.';
  text+='\nLimitada: '+answer(a.bounded)+' · Viva por binding: '+answer(a.live)+' (somente nesse catálogo).';
 }
 if(index>=0){
  const r=model.report.summary.results[index];
  text+='\n'+(r.accepted?'Trace aceito.':r.divergences.map(d=>d.kind+': '+d.message).join('\n'));
  text+='\nEventos avaliados: '+r.evaluated_events+' · Fora do modelo: '+r.skipped_events+' · Não processados após a parada: '+r.unprocessed_events;
 }
 document.getElementById('status').textContent=text;
}
modelSelect.addEventListener('change',selectModel);traceSelect.addEventListener('change',selectTrace);selectModel();
</script></html>'''
    (directory / 'index.html').write_text(html.replace('__DATA__', embedded), encoding='utf-8')
