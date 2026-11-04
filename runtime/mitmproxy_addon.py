"""Addon de observação: não modifica request, response ou headers."""
import json
import os
from pathlib import Path
import sys
from time import perf_counter_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpn_runtime import RuntimeMonitor, utc_now


class CPNObserver:
    def running(self):
        config = json.loads(os.environ['CPN_RUNTIME_CONFIG'])
        try:
            self.monitor = RuntimeMonitor(**config)
        except Exception:
            from mitmproxy import ctx
            ctx.master.shutdown()
            raise

    def requestheaders(self, flow):
        flow.metadata['cpn_received_at'] = utc_now()
        flow.metadata['cpn_started_ns'] = perf_counter_ns()

    def response(self, flow):
        try:
            request_body = flow.request.content
            response_body = flow.response.content
            if request_body is None or response_body is None:
                raise ValueError('Corpo indisponível: streaming não suportado pelo coletor.')
        except Exception as exc:
            self.monitor.capture_error(flow.id, type(exc).__name__)
            return
        self.monitor.observe(method=flow.request.method, path=flow.request.path,
            status_code=flow.response.status_code,
            request_headers=dict(flow.request.headers),
            response_headers=dict(flow.response.headers),
            request_body=request_body, response_body=response_body,
            flow_id=flow.id, received_at=flow.metadata.get('cpn_received_at'),
            request_started_ns=flow.metadata.get('cpn_started_ns'))

    def error(self, flow):
        self.monitor.transport_error(flow.id)

    def done(self):
        if hasattr(self, 'monitor'):
            self.monitor.close()


addons = [CPNObserver()]
