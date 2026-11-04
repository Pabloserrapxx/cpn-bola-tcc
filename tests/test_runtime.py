import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn_runtime import RuntimeMonitor

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def monitor(self, directory, **options):
        return RuntimeMonitor(ROOT / 'examples/contas.openapi.json', directory, **options)

    def observe(self, monitor, *, path='/login', body=None, token=None, request_headers=None):
        headers = dict(request_headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return monitor.observe(method='POST' if path == '/login' else 'GET', path=path,
            status_code=200, request_headers=headers, response_headers={},
            request_body=b'{}', response_body=json.dumps(body or {}).encode(), flow_id='flow')

    def login(self, monitor, name='alice', id=6):
        return self.observe(monitor, body={'authentication': {'id': id, 'token': 'lab-' + name}})

    def test_token_correlation_isolation_and_observation_after_attack(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory)
            self.assertEqual('fired', self.login(monitor)['status'])
            self.login(monitor, 'bob', 9)
            attack = self.observe(monitor, path='/accounts/9', body={'id': 9}, token='lab-alice')
            self.assertEqual('divergent', attack['status'])
            self.assertEqual('DIVERGENCIA_FLUXO_DADOS', attack['result'].divergence.kind.value)
            self.assertEqual('fired', self.observe(monitor, path='/accounts/6',
                body={'id': 6}, token='lab-alice')['status'])
            self.assertEqual('fired', self.observe(monitor, path='/accounts/9',
                body={'id': 9}, token='lab-bob')['status'])
            monitor.close()
            monitor.close()
            saved = (Path(directory) / 'monitor.jsonl').read_text()
            self.assertNotIn('lab-alice', saved)
            self.assertFalse((Path(directory) / 'eventos_capturados.jsonl').exists())

    def test_capacity_ttl_and_identity_errors_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory, ttl=10, max_sessions=1)
            with patch('cpn_runtime.monotonic', return_value=0):
                first = self.login(monitor)
            with patch('cpn_runtime.monotonic', return_value=1):
                self.assertEqual('session_capacity', self.login(monitor, 'bob', 9)['reason'])
            with patch('cpn_runtime.monotonic', return_value=11):
                second = self.login(monitor, 'bob', 9)
                self.assertEqual('fired', second['status'])
                self.assertNotEqual(first['trace_id'], second['trace_id'])
            self.assertEqual(1, monitor.counts['expired_sessions'])
            self.assertEqual('capture_error', self.observe(monitor, path='/accounts/6')['status'])
            monitor.transport_error('bad-flow')
            self.assertEqual(1, monitor.counts['transport_error'])
            monitor.close()

    def test_body_limit_header_identity_and_unknown_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory, identity_header='X-Session', max_body_bytes=100)
            self.assertEqual('identity_missing', self.login(monitor)['reason'])
            row = self.observe(monitor, body={'authentication': {'id': 6}},
                               request_headers={'x-session': 'trusted-session'})
            self.assertEqual('fired', row['status'])
            self.assertEqual('outside_model', self.observe(monitor, path='/health',
                request_headers={'X-Session': 'trusted-session'})['status'])
            self.assertEqual('body_limit', self.observe(monitor, body={'large': 'x' * 101})['reason'])
            monitor.close()

    def test_stop_policy_and_capture_can_replay(self):
        from cpn_logs import load_traces
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.monitor(directory, capture_events=True, continue_after_divergence=False)
            self.login(monitor)
            self.observe(monitor, path='/accounts/9', body={'id': 9}, token='lab-alice')
            self.assertEqual('not_processed', self.observe(monitor, path='/accounts/6',
                body={'id': 6}, token='lab-alice')['status'])
            monitor.close()
            traces = load_traces(Path(directory) / 'eventos_capturados.jsonl')
            self.assertEqual(1, len(traces))
            self.assertEqual(3, len(traces[0].events))
            with self.assertRaises(FileExistsError):
                self.monitor(directory)


if __name__ == '__main__':
    unittest.main()
