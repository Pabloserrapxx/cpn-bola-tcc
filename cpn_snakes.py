"""Execução SNAKES e instanciação finita dos bindings da API.

Cada binding concreto gera uma transição SNAKES com arcos Value/MultiArc.
Não executamos expressões fornecidas pelo OpenAPI como código SNAKES.
O catálogo de eventos delimita as cores consideradas na análise; não delimita
ordem nem número de repetições. O modelo é uma expansão finita dos bindings,
não a enumeração de todos os valores possíveis da API.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from snakes.nets import (Expression, Marking, MultiArc, PetriNet, Place,
                         Substitution, Transition, Value)
from snakes.typing import Instance

from cpn_bola import (InvalidEvent, InvalidResource, Token, popula_lugares_iniciais,
                      Trace)


def multiset(arcs):
    return Counter((a.place, a.token) for a in arcs)


class SnakesExecutor:
    def __init__(self, model):
        self.model = model
        self.net = PetriNet(model.name)
        self.variants = {}
        self.labels = {}
        for name, place in model.places.items():
            self.net.add_place(Place(name, place.tokens(), Instance(Token)))

    def register(self, transition, binding, event):
        """Congela apenas expressões de um binding; não consulta a marcação."""
        if not transition.guard(binding, event):
            raise InvalidEvent('Binding viola a guarda do modelo.')
        inputs = transition.inputs(binding, event)
        outputs = transition.outputs(binding, event)
        signature = (transition.name, frozenset(multiset(inputs).items()),
                     frozenset(multiset(outputs).items()))
        if signature in self.variants:
            return self.variants[signature]
        # Validação precede qualquer modificação da topologia SNAKES.
        for arc in inputs + outputs:
            if arc.place not in self.model.places:
                raise ValueError(f'Lugar inexistente: {arc.place}')
            hash(arc.token)
        name = f'b{len(self.variants):04d}'
        native = Transition(name, Expression('True'))
        self.net.add_transition(native)
        for arcs, add_arc in ((inputs, self.net.add_input), (outputs, self.net.add_output)):
            grouped = defaultdict(list)
            for arc in arcs:
                grouped[arc.place].append(Value(arc.token))
            for place, annotations in grouped.items():
                annotation = annotations[0] if len(annotations) == 1 else MultiArc(annotations)
                add_arc(place, name, annotation)
        self.variants[signature] = name
        self.labels[name] = dict(activity=transition.activity, user_id=event.user_id)
        return name

    def fire(self, transition, binding, event):
        # A camada Python é uma projeção do estado SNAKES durante o replay.
        expected = {name: Counter(place.tokens()) for name, place in self.model.places.items()}
        actual = {p.name: Counter(p.tokens) for p in self.net.place()}
        if expected != actual:
            raise RuntimeError('Marcação Python/SNAKES dessincronizada.')
        name = self.register(transition, binding, event)
        native = self.net.transition(name)
        mode = Substitution()
        if not native.enabled(mode):
            raise RuntimeError(f'SNAKES: binding não habilitado: {name}')
        before = self.net.get_marking()
        try:
            native.fire(mode)
        except Exception:
            self.net.set_marking(before)
            raise
        for name, place in self.model.places.items():
            place.marking = Counter(self.net.place(name).tokens)


@dataclass
class FiniteDomain:
    executor: SnakesExecutor
    rejected_events: list
    event_variants: list
    user_id: str


def compile_event_domain(model, user_id, events):
    """Compila todas as alternativas fornecidas, sem fazer replay do catálogo.

    Use eventos de uma única identidade. Somente USER é inicializado, nunca
    recursos oriundos de respostas futuras. Bindings iguais podem compartilhar
    uma transição nativa; isso não deduplica tokens nem eventos do replay.
    """
    from cpn_openapi import normalize_event, select_event_status

    if not user_id:
        raise ValueError('Informe uma identidade para a análise.')
    if not model.metadata.get('event_only_arcs'):
        raise ValueError('Compilação de domínio exige modelo OpenAPI com expressões independentes da marcação.')
    net = model.clone()
    net.reset()
    popula_lugares_iniciais(net, net.initial_places, Trace('domain', user_id, ()))
    executor = SnakesExecutor(net)
    rejected, variants = [], []
    for index, raw_event in enumerate(events):
        if raw_event.user_id != user_id:
            raise ValueError('O domínio deve conter eventos de uma única identidade.')
        try:
            event = select_event_status(net, normalize_event(net, raw_event))
            transition = net.select_transition(event.activity)
            if transition is None:
                raise InvalidEvent('Atividade fora do modelo.')
            binding = transition.bind(event, net)
            if not transition.postcondition(binding, event, net):
                raise InvalidResource('Resposta do catálogo viola uma pós-condição do modelo.')
            variant = executor.register(transition, binding, event)
            variants.append(dict(event_index=index, variant=variant))
        except (InvalidEvent, InvalidResource) as exc:
            rejected.append(dict(event_index=index, reason=str(exc)))
    return FiniteDomain(executor, rejected, variants, user_id)


def marking_key(net):
    return tuple((p.name, frozenset(Counter(p.tokens).items()))
                 for p in sorted(net.place(), key=lambda p: p.name))


def marking_tokens(marking):
    return {name: list(tokens) for name, tokens in marking.items()}


def ground_incidence(net):
    """Só aceita arcos constantes e guardas literais: prova sem efeitos externos."""
    def values(annotation):
        if type(annotation) is Value:
            return [annotation.value]
        if type(annotation) is MultiArc:
            return [token for part in annotation for token in values(part)]
        raise ValueError('Prova estrutural exige arcos Value/MultiArc.')

    incidence = {}
    for transition in net.transition():
        if str(transition.guard) not in ('True', 'False'):
            return None
        try:
            pre = Counter((p.name, token) for p, arc in transition.input() for token in values(arc))
            post = Counter((p.name, token) for p, arc in transition.output() for token in values(arc))
        except ValueError:
            return None
        types_valid = True
        try:
            for place, annotation in transition.input() + transition.output():
                place.check(values(annotation))
        except ValueError:
            types_valid = False
        incidence[transition.name] = (pre, post, str(transition.guard) == 'True' and types_valid)
    return incidence
