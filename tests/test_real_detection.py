from io import StringIO
import json
import unittest

from cpn_bola import Event
from cpn_incremental import ReplaySession
from cpn_openapi import build_openapi_cpn
from scripts.evaluate_real_detection import add_plan, juice_model


class RealDetectionTests(unittest.TestCase):
    def test_duplicate_key_extraction_matches_effective_target(self):
        model = build_openapi_cpn(juice_model())
        session = ReplaySession(model, 'actor', 'actor', continue_after_divergence=True)
        session.process(Event('POST', '/rest/user/login', 200, 'actor',
            response_body={'authentication': {'bid': 6}}))
        duplicate = '{"ProductId":1,"BasketId":6,"quantity":1,"BasketId":9}'
        body = json.loads(duplicate)
        self.assertEqual(9, body['BasketId'])
        attack = Event('POST', '/api/BasketItems', 200, 'actor',
            request_body=body, response_body={'data': {'BasketId': 9}})
        result = session.process(attack)
        self.assertEqual('DIVERGENCIA_FLUXO_DADOS', result.divergence.kind.value)
        own = Event('POST', '/api/BasketItems', 200, 'actor',
            request_body={'ProductId': 1, 'BasketId': 6, 'quantity': 1},
            response_body={'data': {'BasketId': 6}})
        self.assertEqual('fired', session.process(own).status)

    def test_denied_attempt_is_modeled_despite_non_json_response(self):
        model = build_openapi_cpn(juice_model())
        session = ReplaySession(model, 'actor', 'actor')
        session.process(Event('POST', '/rest/user/login', 200, 'actor',
            response_body={'authentication': {'bid': 6}}))
        result = session.process(Event('POST', '/api/BasketItems', 401, 'actor',
            request_body={'ProductId': 1, 'BasketId': 9, 'quantity': 1},
            response_body="{'error': 'Invalid BasketId'}"))
        self.assertEqual('divergent', result.status)
        self.assertEqual('DIVERGENCIA_FLUXO_DADOS', result.divergence.kind.value)

    def test_ground_truth_is_saved_before_execution(self):
        stream, plan = StringIO(), []
        case = dict(case_id='known-attack', actor_id=21, owner_id=22, expected_bola=True)
        add_plan(stream, plan, case)
        self.assertEqual(case, json.loads(stream.getvalue()))
        self.assertEqual([case], plan)
        with self.assertRaises(ValueError):
            add_plan(stream, plan, dict(case, expected_bola=False))
        self.assertEqual([case], plan)


if __name__ == '__main__':
    unittest.main()
