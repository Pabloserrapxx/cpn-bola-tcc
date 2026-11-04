from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import unittest

from cpn_performance import delta, distribution, load, repeated, summarize
from scripts.benchmark_runtime import consolidate, process_usage


class PerformanceTests(unittest.TestCase):
    def test_statistics_denominators_and_percentiles(self):
        d = distribution([4, 1, 3, 2])
        self.assertEqual(2.5, d['mean'])
        self.assertEqual(2.5, d['p50'])
        self.assertEqual(4, d['p95'])
        self.assertEqual(4, d['p99'])
        self.assertIsNone(distribution([])['mean'])
        self.assertEqual(dict(absolute=5, percent=25), delta(20, 25))
        self.assertIsNone(delta(0, 1)['percent'])
        self.assertEqual(1, repeated([1, 2, 3])['stdev'])

    def test_failures_are_not_successful_throughput(self):
        rows = [dict(completed_ns=int(1e9), latency_ms=10, service_ms=9, queue_ms=1,
                     status=s, body_valid=valid, error_type=error)
                for s,valid,error in [(200,True,None),(429,False,None),(200,False,None),
                                      (None,False,'TimeoutError')]]
        summary = summarize(rows, 2)
        self.assertEqual(.5, summary['successful_rps'])
        self.assertEqual(2, summary['completed_rps'])
        self.assertEqual(1, summary['transport_errors'])
        self.assertEqual(1, summary['invalid_bodies'])
        self.assertEqual(.25, summary['success_fraction'])
        self.assertEqual(1, summary['successful_latency_ms']['count'])

    def test_real_http_load_keeps_offered_count_and_validates_bodies(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_GET(self):
                body = b'{"id": 6}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rows, result = load(server.server_port, 'GET', '/', {}, None,
                rate=20, duration=.2, workers=2, validate=lambda raw:json.loads(raw)['id']==6)
            self.assertEqual(4, len(rows))
            self.assertEqual(4, result['successes'])
            self.assertEqual(20, result['successful_rps'])
            self.assertTrue(all(r['latency_ms'] >= r['service_ms'] for r in rows))
            _, bad = load(server.server_port, 'GET', '/', {}, None,
                rate=10, duration=.1, validate=lambda raw:json.loads(raw)['missing']==6)
            self.assertEqual(1, bad['invalid_bodies'])
            self.assertEqual(0, bad['transport_errors'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_overhead_pairs_same_repetition_not_execution_order(self):
        trials=[]
        for repetition in range(2):
            for mode, average in [('monitor', 30+repetition),('direct', 10+repetition),('proxy',20+repetition)]:
                trials.append(dict(app='api', target_rps=10, mode=mode, repetition=repetition,
                    summary=dict(requests=10, successes=10, http_statuses={'200':10},
                        latency_ms=dict(mean=average,p50=average,p95=average,p99=average),
                        successful_rps=10, client_queue_ms={'p95':0}, success_fraction=1)))
        group = consolidate(trials)[0]
        self.assertEqual(20, group['overhead']['total_vs_direct']['mean_latency_ms']['mean'])
        self.assertEqual(10, group['overhead']['monitor_vs_proxy']['mean_latency_ms']['mean'])


if __name__ == '__main__':
    unittest.main()
