from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cpn_bola import (ArcBinding, ColoredPetriNet, DivergenceType as D, Event,
                      InvalidEvent, Token, Trace, Transition, fire, replay_trace,
                      verificar_conformidade)
from cpn_logs import load_traces
from cpn_openapi import (ModelError, build_openapi_cpn, expression, freeze,
                         load_openapi, normalize_event, pointer)

ROOT = Path(__file__).resolve().parents[1]


def spec(name='contas'):
    return load_openapi(ROOT / 'examples' / f'{name}.openapi.json')


def login(user='alice', ident=6):
    return Event('POST', '/login', 200, user, response_body={'authentication': {'id': ident}})


def get(ident=6, user='alice', observed=None):
    return Event('GET', f'/accounts/{ident}', 200, user,
                 response_body={'id': ident if observed is None else observed})


def replay(events, document=None, **options):
    net = build_openapi_cpn(document or spec())
    return replay_trace(net, net.initial_places, Trace('t', 'alice', tuple(events)), **options)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.net = ColoredPetriNet('test')
        self.net.add_place('p')
        self.token = Token('USER', 'alice')
        self.net.places['p'].add(self.token)
        self.event = login()

    def transition(self, inputs, outputs, guard=lambda b, e: True):
        return Transition('t', 'a', lambda e, n: {}, lambda b, e: inputs,
                          outputs, guard)

    def test_multiset_requires_two_tokens(self):
        arc = ArcBinding('p', self.token)
        t = self.transition([arc, arc], lambda b, e: [])
        before = self.net.snapshot()
        with self.assertRaises(RuntimeError):
            fire(self.net, t, {}, self.event)
        self.assertEqual(before, self.net.snapshot())

    def test_multiset_consumes_two_when_available(self):
        self.net.places['p'].add(self.token)
        arc = ArcBinding('p', self.token)
        fire(self.net, self.transition([arc, arc], lambda b, e: []), {}, self.event)
        self.assertEqual([], self.net.places['p'].tokens())

    def test_output_exception_does_not_consume_inputs(self):
        def fail(b, e):
            raise ValueError('falha de expressão')
        t = self.transition([ArcBinding('p', self.token)], fail)
        with self.assertRaises(ValueError):
            fire(self.net, t, {}, self.event)
        self.assertTrue(self.net.places['p'].has(self.token))

    def test_bad_output_place_does_not_consume_inputs(self):
        t = self.transition([ArcBinding('p', self.token)], lambda b, e: [ArcBinding('missing', self.token)])
        with self.assertRaises(ValueError):
            fire(self.net, t, {}, self.event)
        self.assertTrue(self.net.places['p'].has(self.token))

    def test_direct_fire_checks_guard(self):
        t = self.transition([], lambda b, e: [], lambda b, e: False)
        with self.assertRaises(ValueError):
            fire(self.net, t, {}, self.event)

    def test_duplicate_activity_rejected(self):
        self.net.add_transition(self.transition([], lambda b, e: []))
        with self.assertRaises(ValueError):
            self.net.add_transition(Transition('other', 'a', lambda e, n: {}, lambda b, e: [], lambda b, e: []))

    def test_invalid_multiplicity(self):
        for amount in (-1, 0, True):
            with self.assertRaises(ValueError):
                self.net.places['p'].add(self.token, amount)


class ReplayTests(unittest.TestCase):
    def test_legal_repeated_access_preserves_multiset(self):
        result = replay([login(), login(), get(), get()])
        self.assertTrue(result.accepted)
        self.assertEqual(1, len(result.final_marking['p_user']))
        resources = [v for k, v in result.final_marking.items() if k != 'p_user']
        self.assertEqual([2], list(map(len, resources)))

    def test_data_flow(self):
        result = replay([login(), get(9)])
        self.assertEqual(D.DATA_FLOW, result.divergences[0].kind)

    def test_control_flow(self):
        self.assertEqual(D.CONTROL_FLOW, replay([get()]).divergences[0].kind)

    def test_corrupted_response(self):
        self.assertEqual(D.CORRUPTED_RESOURCE, replay([login(), get(observed=9)]).divergences[0].kind)

    def test_missing_response_id_is_not_accepted(self):
        result = replay([login(), replace(get(), response_body={})])
        self.assertEqual(D.CORRUPTED_RESOURCE, result.divergences[0].kind)

    def test_bad_producer_response_adds_no_resource(self):
        result = replay([replace(login(), response_body={})])
        self.assertEqual(D.CORRUPTED_RESOURCE, result.divergences[0].kind)
        self.assertEqual(1, sum(map(len, result.final_marking.values())))

    def test_missing_request_field(self):
        event = replace(get(), path='/accounts/{id}', path_params={})
        self.assertEqual(D.INVALID_EVENT, replay([login(), event]).divergences[0].kind)

    def test_users_cannot_share_tokens(self):
        net = build_openapi_cpn(spec())
        traces = [Trace('a', 'alice', (login(),)), Trace('b', 'bob', (get(user='bob'),))]
        summary = verificar_conformidade(net, iter(net.initial_places), traces)
        self.assertTrue(summary.results[0].accepted)
        self.assertEqual(D.CONTROL_FLOW, summary.results[1].divergences[0].kind)
        self.assertTrue(all(not p.tokens() for p in net.places.values()))

    def test_user_change_in_trace_is_invalid(self):
        self.assertEqual(D.INVALID_EVENT, replay([login(), get(user='bob')]).divergences[0].kind)

    def test_break_first_error(self):
        result = replay([login(), get(9), get()])
        self.assertEqual(1, len(result.divergences))
        self.assertEqual(1, result.unprocessed_events)

    def test_compatibility_continues_after_error(self):
        result = replay([login(), get(9), get()], continue_after_divergence=True,
                        record_steps=True)
        self.assertEqual([1], [d.event_index for d in result.divergences])
        self.assertEqual([0, 2], [step['event_index'] for step in result.steps])
        self.assertEqual(0, result.unprocessed_events)

    def test_unknown_policies_and_empty_coverage(self):
        unknown = Event('GET', '/outside', 200, 'alice')
        self.assertEqual(D.UNKNOWN_ACTIVITY, replay([unknown]).divergences[0].kind)
        result = replay([unknown, login(), get()], unknown_policy='skip')
        self.assertTrue(result.accepted)
        self.assertEqual(1, result.skipped_events)
        self.assertEqual(D.NO_MODELED_EVENTS, replay([unknown], unknown_policy='skip').divergences[0].kind)
        self.assertFalse(replay([]).accepted)

    def test_empty_log_has_no_fitness(self):
        net = build_openapi_cpn(spec())
        self.assertIsNone(verificar_conformidade(net, net.initial_places, []).fitness)

    def test_fitness_is_per_trace(self):
        net = build_openapi_cpn(spec())
        traces = [Trace('ok', 'alice', (login(), get())), Trace('bad', 'alice', (get(),))]
        self.assertEqual(.5, verificar_conformidade(net, net.initial_places, traces).fitness)

    def test_all_synthetic_scenarios(self):
        expected = {'legitimo': None, 'lista-vazia': None, 'bola-id-alheio': D.DATA_FLOW,
                    'fora-de-ordem': D.CONTROL_FLOW, 'resposta-corrompida': D.CORRUPTED_RESOURCE,
                    'regra-quantidade': D.RULE_VIOLATION, 'regra-estado': D.RULE_VIOLATION}
        for name in ('contas', 'juice_view', 'juice_manipulate', 'memos'):
            net = build_openapi_cpn(spec(name))
            for trace in load_traces(ROOT / 'examples' / f'{name}.logs.json'):
                with self.subTest(model=name, trace=trace.trace_id):
                    result = replay_trace(net, net.initial_places, trace)
                    kind = result.divergences[0].kind if result.divergences else None
                    self.assertEqual(expected[trace.trace_id], kind)


class TransformationTests(unittest.TestCase):
    def test_json_pointer_and_types(self):
        self.assertEqual(7, pointer({'a/b': {'~': [7]}}, '/a~1b/~0/0'))
        self.assertNotEqual(freeze(True), freeze(1))
        self.assertNotEqual(freeze([1]), freeze({'0': 1}))
        self.assertEqual(freeze({'a': 1, 'b': 2}), freeze({'b': 2, 'a': 1}))

    def test_local_schema_and_link_refs(self):
        document = spec()
        link = document['paths']['/login']['post']['responses']['200']['links']['account']
        document['components'] = {'links': {'account': link}, 'schemas': {'Id': {'type': 'integer'}}}
        document['paths']['/login']['post']['responses']['200']['links']['account'] = {'$ref': '#/components/links/account'}
        document['paths']['/accounts/{id}']['get']['parameters'][0]['schema'] = {'$ref': '#/components/schemas/Id'}
        self.assertTrue(replay([login(), get()], document).accepted)

    def test_numeric_integer_canonicalization_and_boolean_rejection(self):
        self.assertTrue(replay([login(ident=6.0), get()]).accepted)
        self.assertEqual(D.CORRUPTED_RESOURCE, replay([login(ident=True)]).divergences[0].kind)

    def test_links2cpn_profile_uses_text_ids_and_skips_extra_validations(self):
        document = spec()
        document['paths']['/accounts/{id}']['get']['responses']['200']['x-cpn-equals'] = {
            '/id': '$request.path.id'
        }
        net = build_openapi_cpn(document, links2cpn_compatible=True)
        events = (login(ident='6'), get(observed=9))
        result = replay_trace(net, net.initial_places, Trace('t', 'alice', events))
        self.assertTrue(result.accepted)

    def test_links2cpn_profile_expands_array_without_extension(self):
        document = load_openapi(ROOT / 'examples/comparison/lista-dois.openapi.json')
        link = document['paths']['/memos']['get']['responses']['200']['links']['patch']
        link.pop('x-cpn-many')
        net = build_openapi_cpn(document, links2cpn_compatible=True)
        events = (Event('GET', '/memos', 200, 'alice', response_body=[{'id': '4'}, {'id': '6'}]),
                  Event('PATCH', '/memos/4', 200, 'alice'))
        self.assertTrue(replay_trace(net, net.initial_places, Trace('t', 'alice', events)).accepted)

    def test_same_path_different_method_is_distinct(self):
        document = spec()
        document['paths']['/login']['put'] = {'operationId': 'otherLogin',
                                             'responses': {'200': {'description': 'sem fluxo'}}}
        result = replay([replace(login(), method='PUT')], document)
        self.assertEqual(D.UNKNOWN_ACTIVITY, result.divergences[0].kind)

    def test_operation_ref(self):
        document = spec()
        link = document['paths']['/login']['post']['responses']['200']['links']['account']
        del link['operationId']
        link['operationRef'] = '#/paths/~1accounts~1{id}/get'
        self.assertTrue(replay([login(), get()], document).accepted)

    def test_external_and_cyclic_refs_rejected(self):
        for ref in ('https://example.com/schema.json', '#/components/schemas/Loop'):
            document = spec()
            document['components'] = {'schemas': {'Loop': {'$ref': ref}}}
            document['paths']['/accounts/{id}']['get']['parameters'][0]['schema'] = {'$ref': '#/components/schemas/Loop'}
            with self.assertRaises(ModelError):
                build_openapi_cpn(document)

    def test_invalid_link_target_and_expression_rejected(self):
        for key, value in [('operationId', 'missing'), ('parameters', {'id': '$response.body#bad'})]:
            document = spec()
            document['paths']['/login']['post']['responses']['200']['links']['account'][key] = value
            with self.assertRaises(ModelError):
                build_openapi_cpn(document)

    def test_unlinked_operations_removed(self):
        document = spec()
        document['paths']['/health'] = {'get': {'responses': {'200': {'description': 'ok'}}}}
        net = build_openapi_cpn(document)
        self.assertEqual(2, len(net.transitions))
        self.assertIn('get-/health-200', net.metadata['omitted_activities'])

    def test_multiple_status_codes(self):
        document = spec()
        document['paths']['/login']['post']['responses']['201'] = deepcopy(document['paths']['/login']['post']['responses']['200'])
        net = build_openapi_cpn(document)
        self.assertEqual(3, len(net.transitions))
        self.assertTrue(replay([replace(login(), status_code=201), get()], document).accepted)

    def test_status_range_and_default(self):
        for code in ('2XX', 'default'):
            document = spec()
            responses = document['paths']['/login']['post']['responses']
            responses[code] = responses.pop('200')
            self.assertTrue(replay([replace(login(), status_code=201), get()], document).accepted)

    def test_omitted_exact_status_does_not_use_default(self):
        document = spec()
        responses = document['paths']['/login']['post']['responses']
        responses['default'] = responses.pop('200')
        responses['401'] = {'description': 'Denied'}
        self.assertEqual(D.UNKNOWN_ACTIVITY, replay([replace(login(), status_code=401)], document).divergences[0].kind)

    def test_path_parameters_at_path_item_level(self):
        document = spec()
        path = document['paths']['/accounts/{id}']
        path['parameters'] = path['get'].pop('parameters')
        self.assertTrue(replay([login(), get()], document).accepted)

    def test_runtime_request_query_header_and_array(self):
        event = replace(login(), query_params={'id': '6'}, request_headers={'x-id': 'A'}, response_body=[{'id': 3}])
        self.assertEqual('6', expression('$request.query.id', event))
        self.assertEqual('A', expression('$request.header.X-ID', event))
        self.assertEqual(3, expression('$response.body#/0/id', event))

    def test_legacy_dotted_pointer_is_opt_in(self):
        event = login()
        self.assertEqual(6, expression('$response.body#/authentication.id', event, legacy=True))
        with self.assertRaises(KeyError):
            expression('$response.body#/authentication.id', event)

    def test_url_case_and_explicit_path_conflict(self):
        net = build_openapi_cpn(spec())
        event = normalize_event(net, replace(get(), path='https://api.example/accounts/6/?q=A'))
        self.assertEqual(6, event.path_params['id'])
        self.assertEqual('A', event.query_params['q'])
        with self.assertRaises(InvalidEvent):
            normalize_event(net, replace(get(), path_params={'id': 9}))

    def test_no_links_rejected(self):
        document = spec()
        del document['paths']['/login']['post']['responses']['200']['links']
        with self.assertRaises(ModelError):
            build_openapi_cpn(document)


class LogTests(unittest.TestCase):
    def read(self, text, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'log.json'
            path.write_text(text)
            return load_traces(path, **options)

    def record(self, **extra):
        return dict(method='GET', uri='/x', statusCode=200, user_id='alice', **extra)

    def test_jsonl_and_string_bodies(self):
        record = self.record(requestBody='{}', responseBody='[{"id": 1}]')
        traces = self.read('\n'.join([json.dumps(record)] * 2))
        self.assertEqual([{'id': 1}], traces[0].events[0].response_body)
        self.assertEqual(2, len(traces[0].events))

    def test_missing_identity_and_explicit_ip(self):
        record = {'method': 'GET', 'uri': '/', 'statusCode': 200, 'ip': '127.0.0.1'}
        with self.assertRaises(InvalidEvent):
            self.read(json.dumps(record))
        self.assertEqual('127.0.0.1', self.read(json.dumps(record), identity_field='ip')[0].user_id)

    def test_mixed_identity_in_trace_rejected(self):
        records = [self.record(trace_id='x'), {**self.record(trace_id='x'), 'user_id': 'bob'}]
        with self.assertRaises(InvalidEvent):
            self.read(json.dumps(records))

    def test_invalid_json_and_body_are_not_silently_ignored(self):
        for text in ('{broken}', json.dumps(self.record(requestBody='{broken}'))):
            with self.assertRaises(InvalidEvent):
                self.read(text)

    def test_compatibility_can_defer_invalid_body_parsing(self):
        traces = self.read(json.dumps(self.record(requestBody='{broken}')), strict_bodies=False)
        self.assertEqual('{broken}', traces[0].events[0].request_body)

    def test_authorization_hash_correlates_login_and_request(self):
        records = [self.record(responseBody={'authentication': {'token': 'SECRET'}}),
                   self.record(headerAuthorization='Bearer SECRET')]
        traces = self.read(json.dumps(records), identity_field='headerAuthorization')
        self.assertEqual(1, len(traces))
        self.assertNotIn('SECRET', traces[0].user_id)


if __name__ == '__main__':
    unittest.main()
