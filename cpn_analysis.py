"""Alcançabilidade, vivacidade e limitabilidade em redes SNAKES.

O grafo é explorado com limites explícitos. A ausência de um estado em um grafo
truncado nunca é interpretada como prova de inalcançabilidade. Provas adicionais
só são aplicadas a subclasses cuja estrutura pode ser verificada no código.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import ceil
from snakes.nets import Substitution

from cpn_snakes import ground_incidence, marking_key, marking_tokens


def enabled_modes(transition):
    # SNAKES 0.9.33 retorna [] em modes() para transições sem entradas,
    # embora enabled(Substitution()) possa ser verdadeiro para fontes constantes.
    try:
        candidates = transition.modes() if transition.input() else [Substitution()]
        return [mode for mode in candidates if transition.enabled(mode)]
    except NameError as exc:
        raise ValueError('Análise requer variáveis ligadas pelas entradas ou arcos constantes.') from exc


def _counts(marking):
    return Counter({(place, token): count for place, tokens in marking.items()
                    for token, count in Counter(tokens).items()})


def _covers(larger, smaller):
    return all(larger[key] >= count for key, count in smaller.items())


def _persistent_proof(net, incidence):
    """Prova para redes constantes em que toda entrada é devolvida.

    Nesse caso M' >= M. A habilitação persiste; uma transição é viva sse pode
    tornar-se habilitada. Basta saturar as demandas finitas de cada cor/lugar.
    Uma transição alcançável com produção líquida positiva prova não limitação.
    """
    if incidence is None or any(not _covers(post, pre) for pre, post, _ in incidence.values()):
        return None
    initial = _counts(net.get_marking())
    current = initial.copy()
    caps = initial.copy()
    for pre, post, _ in incidence.values():
        for key, count in pre.items():
            caps[key] = max(caps[key], count)
        for key in post:
            caps[key] = max(caps[key], 1)
    witness, reachable = [], set()
    changed = True
    while changed:
        changed = False
        for name, (pre, post, guard) in incidence.items():
            if not guard or not _covers(current, pre):
                continue
            reachable.add(name)
            growth = post - pre
            repetitions = max((ceil((caps[key] - current[key]) / count)
                               for key, count in growth.items() if current[key] < caps[key]), default=0)
            if repetitions:
                current.update({key: count * repetitions for key, count in growth.items()})
                witness.append(dict(transition=name, repetitions=repetitions))
                changed = True
    # Última passagem inclui transições habilitadas pela última produção.
    reachable = {name for name, (pre, _, guard) in incidence.items()
                 if guard and _covers(current, pre)}
    growing = set()
    growth_witnesses = []
    for name in sorted(reachable):
        pre, post, _ = incidence[name]
        growth = post - pre
        if growth:
            growing.update(place for place, token in growth)
            growth_witnesses.append(dict(transition=name,
                increments=[dict(place=p, token=t, amount=n) for (p, t), n in growth.items()]))
    bounds = {p.name: (None if p.name in growing else len(p.tokens)) for p in net.place()}
    initially_enabled = [name for name, (pre, _, guard) in incidence.items()
                         if guard and _covers(initial, pre)]
    return dict(basis='constant_persistent_arcs',
                transition_liveness={name: name in reachable for name in incidence},
                bounded=not growing, place_bounds=bounds, unbounded_places=sorted(growing),
                deadlock_free=bool(initially_enabled),
                enabling_sequence=witness, growth_witnesses=growth_witnesses,
                explanation='Entradas são repostas: a habilitação persiste. Saturação das demandas '
                            'estabelece quais bindings podem habilitar. Produção líquida positiva '
                            'de um binding habilitável pode ser repetida indefinidamente.')


def analyze_native(net, *, max_states=200, max_depth=20, max_tokens=1000,
                   max_edges=10000, target=None, labels=None, stop_on_unbounded=False):
    """Explora todos os modes() habilitados; restaura a marcação de entrada.

    target é uma marcação exata; lugares omitidos são considerados vazios.
    Todas as conclusões se referem exclusivamente à rede nativa fornecida.
    """
    for name, value in [('max_states', max_states), ('max_depth', max_depth),
                        ('max_tokens', max_tokens), ('max_edges', max_edges)]:
        minimum = 0 if name == 'max_depth' else 1
        if type(value) is not int or value < minimum:
            raise ValueError(f'{name} deve ser inteiro >= {minimum}')
    labels = labels or {}
    initial = net.get_marking()
    initial_counts = _counts(initial)
    if sum(initial_counts.values()) > max_tokens:
        raise ValueError('Marcação inicial excede max_tokens.')
    if target is not None:
        unknown_places = set(target) - {p.name for p in net.place()}
        if unknown_places:
            raise ValueError(f'Lugares desconhecidos na marcação-alvo: {sorted(unknown_places)}')
    target_counts = None if target is None else _counts(target)
    incidence = ground_incidence(net)
    structural = _persistent_proof(net, incidence)
    markings, parents, depths = [initial], [None], [0]
    seen = {marking_key(net): 0}
    enabled_at = defaultdict(set)
    edges, deadlocks, cutoffs = [], [], set()
    frontier = deque([0])
    unbounded_witness = None
    target_path = [] if target_counts == initial_counts else None

    def path_to(state):
        sequence = []
        while parents[state] is not None:
            parent, step = parents[state]
            sequence.append(step)
            state = parent
        return list(reversed(sequence))

    try:
        terminate = False
        while frontier and not terminate:
            state = frontier.popleft()
            net.set_marking(markings[state])
            modes = [(t, mode) for t in sorted(net.transition(), key=lambda t: t.name) for mode in enabled_modes(t)]
            enabled_at[state].update(t.name for t, mode in modes)
            if not modes:
                deadlocks.append(state)
            for transition, mode in modes:
                if len(edges) >= max_edges:
                    cutoffs.add('max_edges')
                    terminate = True
                    break
                net.set_marking(markings[state])
                transition.fire(mode)
                marking = net.get_marking()
                counts = _counts(marking)
                step = dict(transition=transition.name, binding=repr(mode))
                if target_counts is not None and counts == target_counts and target_path is None:
                    target_path = path_to(state) + [step]
                if incidence is not None and unbounded_witness is None:
                    ancestor = state
                    while ancestor is not None:
                        earlier = _counts(markings[ancestor])
                        if counts != earlier and _covers(counts, earlier):
                            unbounded_witness = dict(
                                ancestor_state=ancestor, ancestor_marking=marking_tokens(markings[ancestor]),
                                descendant_marking=marking_tokens(marking),
                                prefix=path_to(ancestor),
                                repeatable_sequence=(path_to(state) + [step])[depths[ancestor]:],
                                basis='strict_self_covering_path_with_constant_arcs')
                            break
                        ancestor = parents[ancestor][0] if parents[ancestor] is not None else None
                key = marking_key(net)
                successor = seen.get(key)
                if successor is None:
                    if sum(counts.values()) > max_tokens:
                        cutoffs.add('max_tokens')
                    elif depths[state] >= max_depth:
                        cutoffs.add('max_depth')
                    elif len(markings) >= max_states:
                        cutoffs.add('max_states')
                    else:
                        successor = len(markings)
                        seen[key] = successor
                        markings.append(marking)
                        parents.append((state, step))
                        depths.append(depths[state] + 1)
                        frontier.append(successor)
                edges.append(dict(source=state, target=successor, **step))
                if stop_on_unbounded and unbounded_witness is not None:
                    cutoffs.add('unbounded_witness_stop')
                    terminate = True
                    break
    finally:
        net.set_marking(initial)

    complete = not cutoffs
    names = sorted(t.name for t in net.transition())
    live = {name: None for name in names}
    activities = defaultdict(set)
    for name in names:
        activities[labels.get(name, {}).get('activity', name)].add(name)
    activity_live = {name: None for name in activities}
    if complete:
        reverse = defaultdict(set)
        for edge in edges:
            reverse[edge['target']].add(edge['source'])

        def is_live(variants):
            can_reach = {state for state, enabled in enabled_at.items() if enabled & variants}
            queue = list(can_reach)
            while queue:
                for parent in reverse[queue.pop()]:
                    if parent not in can_reach:
                        can_reach.add(parent)
                        queue.append(parent)
            return len(can_reach) == len(markings)

        live = {name: is_live({name}) for name in names}
        activity_live = {name: is_live(variants) for name, variants in activities.items()}
    elif deadlocks:
        live = dict.fromkeys(names, False)
        activity_live = dict.fromkeys(activities, False)

    maximum = {p.name: max(len(m.get(p.name, [])) for m in markings) for p in net.place()}
    bounded = False if unbounded_witness else (True if complete else None)
    bounded_basis = 'self_covering_path' if unbounded_witness else ('complete_graph' if complete else 'inconclusive')
    live_basis = 'complete_graph' if complete else ('reachable_deadlock' if deadlocks else 'inconclusive')
    place_bounds = maximum if complete else dict.fromkeys(maximum)
    deadlock_free = False if deadlocks else (True if complete else None)
    if structural is not None:
        live = structural['transition_liveness']
        activity_live = {name: any(live[v] for v in variants) for name, variants in activities.items()}
        bounded, place_bounds = structural['bounded'], structural['place_bounds']
        bounded_basis = live_basis = structural['basis']
        deadlock_free = structural['deadlock_free']
    overall_live = all(live.values()) if all(value is not None for value in live.values()) else None
    activity_overall = all(activity_live.values()) if all(v is not None for v in activity_live.values()) else None
    reachable = None if target_counts is None else (True if target_path is not None else (False if complete else None))
    return dict(
        scope='Conclusões sobre a rede SNAKES fornecida; em modelos de API, somente o catálogo finito de bindings.',
        complete_graph=complete, cutoff_reasons=sorted(cutoffs),
        limits=dict(max_states=max_states, max_depth=max_depth, max_tokens=max_tokens, max_edges=max_edges),
        state_count=len(markings), edge_count=len(edges),
        bounded=bounded, bounded_basis=bounded_basis, place_bounds=place_bounds,
        observed_max_tokens=maximum, unbounded_witness=unbounded_witness,
        live=overall_live, transition_liveness=live, activity_liveness=activity_live,
        all_activities_live=activity_overall, liveness_basis=live_basis,
        deadlock_free=deadlock_free, deadlock_states=deadlocks,
        target=None if target_counts is None else dict(reachable=reachable, witness=target_path,
                                                     exact_marking=marking_tokens(target)),
        structural_proof=structural, labels=labels,
        states=[dict(id=i, depth=depths[i], marking=marking_tokens(m), enabled=sorted(enabled_at[i]))
                for i, m in enumerate(markings)], edges=edges)
