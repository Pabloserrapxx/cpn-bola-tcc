from dataclasses import replace
import unittest

from cpn_bola import Event
from cpn_detection import metrics, prediction
from cpn_incremental import ReplaySession
from cpn_openapi import build_openapi_cpn
from scripts.detection_lab import authorized, cases, specification


class DetectionMetricsTests(unittest.TestCase):
    def test_hand_calculated_matrix_and_coverage(self):
        rows = [dict(expected_bola=t, predicted_bola=p) for t, p in
                [(True, True), (True, True), (True, False), (False, True),
                 (False, False), (True, None), (False, None)]]
        result = metrics(rows)
        self.assertEqual((2, 1, 1, 1), tuple(result[k] for k in ('TP', 'FP', 'FN', 'TN')))
        self.assertEqual(2, result['unclassified'])
        self.assertAlmostEqual(5/7, result['coverage'])
        self.assertAlmostEqual(2/3, result['precision'])
        self.assertAlmostEqual(2/3, result['recall'])
        self.assertAlmostEqual(2/3, result['f1'])
        self.assertAlmostEqual(3/5, result['accuracy'])
        self.assertAlmostEqual(2/4, result['end_to_end_attack_detection'])

    def test_undefined_metrics_and_invalid_labels(self):
        result = metrics([])
        for key in ('coverage', 'precision', 'recall', 'f1', 'accuracy'):
            self.assertIsNone(result[key])
        result = metrics([dict(expected_bola=False, predicted_bola=False)])
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['recall'])
        with self.assertRaises(ValueError):
            metrics([dict(expected_bola='attack', predicted_bola=True)])

    def test_alert_policy_does_not_turn_capture_or_schema_errors_into_attacks(self):
        for status in ('capture_error', 'transport_error', 'outside_model', 'not_processed'):
            self.assertIsNone(prediction(dict(status=status)))
        self.assertFalse(prediction(dict(status='fired')))
        for kind in ('DIVERGENCIA_FLUXO_CONTROLE', 'DIVERGENCIA_FLUXO_DADOS'):
            self.assertTrue(prediction(dict(status='divergent', result=dict(divergence=dict(kind=kind)))))
        for kind in ('EVENTO_INVALIDO', 'VIOLACAO_REGRA', 'RECURSO_CORROMPIDO'):
            self.assertIsNone(prediction(dict(status='divergent', result=dict(divergence=dict(kind=kind)))))

    def test_labels_follow_acl_without_using_detector(self):
        plan = cases(2)
        self.assertEqual(36, len(plan))
        self.assertEqual(36, len({r['case_id'] for r in plan}))
        for row in plan:
            self.assertEqual(not authorized(row['user'], row['method'], row['target']), row['expected_bola'])
            if row['expected_http'] == 403:
                self.assertTrue(row['expected_bola'])
        self.assertTrue(authorized('alice', 'GET', 12))
        self.assertFalse(authorized('alice', 'PATCH', 12))
        with self.assertRaises(ValueError):
            cases(0)

    def test_model_detects_read_write_denied_and_exposes_context_limitations(self):
        model = build_openapi_cpn(specification())
        login = Event('POST', '/login', 200, 'alice',
                      response_body={'authentication': {'id': 6}})
        for method in ('GET', 'PATCH'):
            session = ReplaySession(model, method, 'alice', continue_after_divergence=True)
            session.process(login)
            good = Event(method, '/accounts/6', 200, 'alice',
                         request_body={'note': 'ok'}, response_body={'id': 6})
            self.assertEqual('fired', session.process(good).status)
            denied = replace(good, path='/enforced/accounts/9', status_code=403,
                             response_body={'error': 'forbidden'})
            self.assertEqual('divergent', session.process(denied).status)
        session = ReplaySession(model, 'sharing', 'alice')
        session.process(login)
        legitimate_shared = Event('GET', '/accounts/12', 200, 'alice', response_body={'id': 12})
        self.assertTrue(authorized('alice', 'GET', 12))
        self.assertEqual('divergent', session.process(legitimate_shared).status)
        cold = ReplaySession(model, 'cold', 'alice')
        self.assertEqual('divergent', cold.process(Event('GET', '/accounts/6', 200,
                          'alice', response_body={'id': 6})).status)


if __name__ == '__main__':
    unittest.main()
