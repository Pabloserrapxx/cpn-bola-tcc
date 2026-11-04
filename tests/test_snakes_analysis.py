from collections import Counter
from pathlib import Path
import unittest
from unittest.mock import patch

from snakes.nets import (MultiArc, PetriNet, Place, Substitution, Transition, Value,
                         Variable, Expression)
from snakes.typing import tInteger

from cpn_analysis import analyze_native, enabled_modes
from cpn_bola import Event, Trace, replay_trace
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn, resource_token
from cpn_snakes import SnakesExecutor, compile_event_domain

ROOT = Path(__file__).resolve().parents[1]


def fixture(places, arcs):
    net = PetriNet('fixture')
    for name, tokens in places.items():
        net.add_place(Place(name, tokens))
    for name, (inputs, outputs) in arcs.items():
        net.add_transition(Transition(name))
        for side, connect in ((inputs, net.add_input), (outputs, net.add_output)):
            for place, values in side.items():
                annotations = [Value(value) for value in values]
                connect(place, name, annotations[0] if len(annotations) == 1 else MultiArc(annotations))
    return net


def cycle():
    return fixture({'p': [1], 'q': []}, {'forward': ({'p': [1]}, {'q': [1]}),
                                          'back': ({'q': [1]}, {'p': [1]})})


class SnakesBackendTests(unittest.TestCase):
    def test_differential_replay_all_examples(self):
        for scenario in ('contas', 'juice_view', 'juice_manipulate', 'memos'):
            net = build_openapi_cpn(ROOT / 'examples' / f'{scenario}.openapi.json')
            for trace in load_traces(ROOT / 'examples' / f'{scenario}.logs.json'):
                with self.subTest(scenario=scenario, trace=trace.trace_id):
                    a = replay_trace(net, net.initial_places, trace, backend='python', record_steps=True)
                    b = replay_trace(net, net.initial_places, trace, backend='snakes', record_steps=True)
                    self.assertEqual('snakes', b.backend)
                    self.assertEqual(a.accepted, b.accepted)
                    self.assertEqual(a.divergences, b.divergences)
                    self.assertEqual(a.steps, b.steps)
                    self.assertEqual(a.final_marking, b.final_marking)

    def test_actual_native_fire_is_called(self):
        net = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[0]
        calls = []
        original = Transition.fire
        def record(native, mode):
            calls.append(native.name)
            return original(native, mode)
        with patch.object(Transition, 'fire', record):
            result = replay_trace(net, net.initial_places, trace)
        self.assertTrue(result.accepted)
        self.assertEqual(3, len(calls))

    def test_native_net_types_and_modes(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[0]
        domain = compile_event_domain(model, trace.user_id, trace.events)
        native = domain.executor.net
        self.assertIsInstance(native, PetriNet)
        self.assertEqual(2, len(native.transition()))
        enabled = [t.name for t in native.transition() if t.modes()]
        self.assertEqual(1, len(enabled))
        native.transition(enabled[0]).fire(Substitution())
        self.assertEqual(2, len([t for t in native.transition() if t.modes()]))

    def test_repeated_production_and_reads_preserve_multiplicity(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[0]
        trace = Trace('repeat', trace.user_id, (trace.events[0],) * 3 + (trace.events[1],) * 4)
        result = replay_trace(model, model.initial_places, trace)
        self.assertTrue(result.accepted)
        resource_place = next(p for p in result.final_marking if p != 'p_user')
        self.assertEqual(3, len(result.final_marking[resource_place]))

    def test_array_duplicates_are_not_silently_removed(self):
        model = build_openapi_cpn(ROOT / 'examples/memos.openapi.json')
        event = Event('GET', '/api/v1/memo', 200, 'u', response_body=[{'id': 4}, {'id': 4}])
        result = replay_trace(model, model.initial_places, Trace('dup', 'u', (event,)))
        place = next(p for p in result.final_marking if p != 'p_user')
        self.assertEqual(2, len(result.final_marking[place]))

    def test_domain_does_not_seed_future_resource_ids(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[1]
        domain = compile_event_domain(model, trace.user_id, trace.events)
        resources = [p for p in domain.executor.net.place() if p.name != 'p_user']
        self.assertTrue(all(len(p.tokens) == 0 for p in resources))
        report = analyze_native(domain.executor.net, labels=domain.executor.labels, max_states=8)
        self.assertFalse(report['all_activities_live'])
        self.assertFalse(report['activity_liveness']['get-/accounts/{id}-200'])

    def test_catalog_rejects_guard_and_postcondition_violations(self):
        for scenario, index in [('juice_manipulate', 3), ('contas', 3)]:
            model = build_openapi_cpn(ROOT / 'examples' / f'{scenario}.openapi.json')
            trace = load_traces(ROOT / 'examples' / f'{scenario}.logs.json')[index]
            domain = compile_event_domain(model, trace.user_id, trace.events)
            self.assertEqual(1, len(domain.rejected_events))

    def test_catalog_rejects_mixed_identities(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        with self.assertRaises(ValueError):
            compile_event_domain(model, 'alice', [Event('POST', '/login', 200, 'bob')])


class StateAnalysisTests(unittest.TestCase):
    def test_bounded_live_cycle_complete_graph(self):
        report = analyze_native(cycle())
        self.assertTrue(report['complete_graph'])
        self.assertEqual(2, report['state_count'])
        self.assertTrue(report['live'])
        self.assertTrue(report['bounded'])
        self.assertTrue(report['deadlock_free'])
        self.assertEqual({'p': 1, 'q': 1}, report['place_bounds'])

    def test_terminal_deadlock_disproves_liveness(self):
        net = fixture({'p': [1], 'q': []}, {'finish': ({'p': [1]}, {'q': [1]})})
        report = analyze_native(net)
        self.assertEqual([1], report['deadlock_states'])
        self.assertFalse(report['live'])
        self.assertFalse(report['deadlock_free'])

    def test_reachable_target_has_witness(self):
        report = analyze_native(cycle(), target={'q': [1]})
        self.assertTrue(report['target']['reachable'])
        self.assertEqual('forward', report['target']['witness'][0]['transition'])

    def test_initial_target_has_empty_witness(self):
        report = analyze_native(cycle(), target={'p': [1]})
        self.assertEqual([], report['target']['witness'])

    def test_unreachable_requires_complete_graph(self):
        report = analyze_native(cycle(), target={'p': [1, 1]})
        self.assertFalse(report['target']['reachable'])
        truncated = analyze_native(cycle(), max_states=1, target={'p': [1, 1]})
        self.assertFalse(truncated['complete_graph'])
        self.assertIsNone(truncated['target']['reachable'])
        self.assertIsNone(truncated['bounded'])
        self.assertIsNone(truncated['live'])

    def test_depth_cutoff_does_not_hide_positive_witness(self):
        report = analyze_native(cycle(), max_depth=0, target={'q': [1]})
        self.assertIn('max_depth', report['cutoff_reasons'])
        self.assertTrue(report['target']['reachable'])

    def test_multiset_not_set_state_identity(self):
        net = fixture({'p': [1]}, {'grow': ({'p': [1]}, {'p': [1, 1]})})
        report = analyze_native(net, max_states=4)
        self.assertEqual(4, report['state_count'])
        self.assertFalse(report['bounded'])
        self.assertTrue(report['live'])
        self.assertIsNotNone(report['unbounded_witness'])

    def test_token_limit_is_inconclusive_without_proof(self):
        net = PetriNet('symbolic')
        net.add_place(Place('p', [0]))
        net.add_transition(Transition('increment'))
        net.add_input('p', 'increment', Variable('x'))
        net.add_output('p', 'increment', Expression('x+1'))
        report = analyze_native(net, max_states=3)
        self.assertIsNone(report['bounded'])
        self.assertIsNone(report['live'])
        self.assertFalse(report['complete_graph'])
        self.assertIsNone(report['unbounded_witness'])

    def test_token_budget_and_edge_budget(self):
        net = fixture({'p': [1]}, {'grow': ({'p': [1]}, {'p': [1, 1]})})
        self.assertIn('max_tokens', analyze_native(net, max_tokens=1)['cutoff_reasons'])
        self.assertIn('max_edges', analyze_native(cycle(), max_edges=1)['cutoff_reasons'])

    def test_analysis_restores_initial_marking(self):
        net = cycle()
        before = net.get_marking()
        analyze_native(net)
        self.assertEqual(before, net.get_marking())

    def test_no_transitions_is_deadlock_but_vacuously_live(self):
        report = analyze_native(fixture({'p': []}, {}))
        self.assertTrue(report['complete_graph'])
        self.assertTrue(report['bounded'])
        self.assertTrue(report['live'])
        self.assertFalse(report['deadlock_free'])

    def test_dead_transition_is_distinct_from_deadlock(self):
        net = fixture({'p': [1], 'q': [], 'z': []}, {
            'forward': ({'p': [1]}, {'q': [1]}),
            'back': ({'q': [1]}, {'p': [1]}),
            'dead': ({'z': [1]}, {'z': [1]})})
        report = analyze_native(net)
        self.assertTrue(report['transition_liveness']['forward'])
        self.assertFalse(report['transition_liveness']['dead'])
        self.assertTrue(report['deadlock_free'])

    def test_persistent_proof_handles_multiplicity_thresholds(self):
        net = fixture({'p': [], 'q': []}, {
            'source': ({}, {'p': [1]}),
            'needs_two': ({'p': [1, 1]}, {'p': [1, 1], 'q': [2]})})
        report = analyze_native(net, max_states=1)
        self.assertFalse(report['complete_graph'])
        self.assertTrue(report['live'])
        self.assertFalse(report['bounded'])
        proof = report['structural_proof']
        for step in proof['enabling_sequence']:
            for _ in range(step['repetitions']):
                transition = net.transition(step['transition'])
                self.assertTrue(transition.enabled(Substitution()))
                transition.fire(Substitution())
        self.assertTrue(all(enabled_modes(t) for t in net.transition()))
        self.assertEqual(['p', 'q'], proof['unbounded_places'])

    def test_unreachable_growing_transition_does_not_prove_unbounded(self):
        net = fixture({'p': []}, {'grow': ({'p': [1]}, {'p': [1, 1]})})
        report = analyze_native(net)
        self.assertTrue(report['bounded'])
        self.assertFalse(report['live'])
        self.assertEqual({'p': 0}, report['place_bounds'])

    def test_source_without_inputs_is_not_mistaken_for_deadlock(self):
        net = fixture({'p': []}, {'source': ({}, {'p': [1]})})
        report = analyze_native(net, max_states=3)
        self.assertEqual(3, report['state_count'])
        self.assertFalse(report['bounded'])
        self.assertTrue(report['deadlock_free'])

    def test_type_invalid_output_cannot_prove_liveness_or_growth(self):
        net = PetriNet('typed')
        net.add_place(Place('p', [], tInteger))
        net.add_transition(Transition('bad_output'))
        net.add_output('p', 'bad_output', Value('not-an-integer'))
        report = analyze_native(net)
        self.assertTrue(report['bounded'])
        self.assertFalse(report['live'])

    def test_activity_liveness_aggregates_alternative_bindings(self):
        net = fixture({'p': [1], 'z': []}, {'good': ({'p': [1]}, {'p': [1]}),
                                           'bad': ({'z': [1]}, {'z': [1]})})
        report = analyze_native(net, labels={'good': {'activity': 'get'}, 'bad': {'activity': 'get'}})
        self.assertFalse(report['live'])
        self.assertTrue(report['all_activities_live'])

    def test_invalid_limits_and_target_are_explicit(self):
        for options in ({'max_states': 0}, {'max_depth': -1}, {'max_tokens': 0},
                        {'target': {'missing': [1]}}):
            with self.assertRaises(ValueError):
                analyze_native(cycle(), **options)


if __name__ == '__main__':
    unittest.main()
