from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn_bola import Event, Trace, replay_trace
from cpn_incremental import ReplaySession
from cpn_logs import load_traces
from cpn_openapi import build_openapi_cpn
from scripts.feed_events import equivalent, feed, iter_events

ROOT = Path(__file__).resolve().parents[1]


class IncrementalTests(unittest.TestCase):
    def test_every_prefix_of_examples_and_controls_matches_offline(self):
        specs = list((ROOT / 'examples').glob('*.openapi.json'))
        specs += list((ROOT / 'examples/comparison').glob('*.openapi.json'))
        for spec in specs:
            for compatible in (False, True):
                model = build_openapi_cpn(spec, legacy_expressions=True,
                                          links2cpn_compatible=compatible)
                logs = spec.with_name(spec.name.replace('.openapi.', '.logs.'))
                identity = 'ip' if spec.parent.name == 'comparison' else 'user_id'
                for trace in load_traces(logs, identity_field=identity,
                                         strict_bodies=not compatible):
                    for backend in ('python', 'snakes'):
                        with self.subTest(spec=spec.name, compatible=compatible,
                                          trace=trace.trace_id, backend=backend):
                            session = ReplaySession(model, trace.trace_id, trace.user_id,
                                backend=backend, unknown_policy='skip', record_steps=True,
                                continue_after_divergence=compatible)
                            executor = session.executor
                            for index, event in enumerate(trace.events):
                                session.process(event)
                                self.assertIs(executor, session.executor)
                                offline = replay_trace(model, model.initial_places,
                                    Trace(trace.trace_id, trace.user_id, trace.events[:index+1]),
                                    backend=backend, unknown_policy='skip', record_steps=True,
                                    continue_after_divergence=compatible)
                                self.assertEqual(offline.final_marking, session.snapshot())
                                self.assertEqual(offline.steps, session.steps)
                                # Comparação do resultado final também verifica contadores.
                            final = session.finish()
                            offline = replay_trace(model, model.initial_places, trace,
                                backend=backend, unknown_policy='skip', record_steps=True,
                                continue_after_divergence=compatible)
                            self.assertTrue(equivalent(offline, final))
                            self.assertEqual(final, session.finish())

    def test_interleaved_sessions_are_isolated_and_snapshot_is_detached(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[0]
        a = ReplaySession(model, 'a', trace.user_id)
        b = ReplaySession(model, 'b', 'bob')
        a.process(trace.events[0])
        snapshot = a.snapshot()
        for tokens in snapshot.values():
            tokens.clear()
        self.assertTrue(any(a.snapshot().values()))
        result = b.process(replace(trace.events[1], user_id='bob'))
        self.assertEqual('divergent', result.status)
        self.assertEqual('fired', a.process(trace.events[1]).status)
        self.assertTrue(all(not p.marking for p in model.places.values()))

    def test_stop_identity_empty_and_close(self):
        model = build_openapi_cpn(ROOT / 'examples/contas.openapi.json')
        trace = load_traces(ROOT / 'examples/contas.logs.json')[0]
        session = ReplaySession(model, 's', trace.user_id)
        before = session.snapshot()
        self.assertEqual('divergent', session.process(replace(trace.events[0], user_id='other')).status)
        self.assertEqual('not_processed', session.process(trace.events[0]).status)
        self.assertEqual(before, session.snapshot())
        session.finish()
        with self.assertRaises(ValueError):
            session.process(trace.events[0])
        for events in ((), (Event('GET', '/unknown', 200, trace.user_id),)):
            session = ReplaySession(model, 's', trace.user_id, unknown_policy='skip')
            for event in events:
                session.process(event)
            reference = replay_trace(model, model.initial_places,
                                     Trace('s', trace.user_id, events), unknown_policy='skip')
            self.assertTrue(equivalent(reference, session.finish()))

    def test_comparator_detects_wrong_event_status_and_handles_empty_trace(self):
        spec = ROOT / 'examples/contas.openapi.json'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.json'
            path.write_text(json.dumps([dict(trace_id='empty', user_id='alice', events=[])]))
            report = feed(spec, path, Path(directory) / 'out', compare=True)
            self.assertTrue(report['comparison']['equivalent'])
            self.assertEqual(1, len(report['comparison']['final_traces']))
            path.write_text(json.dumps([dict(trace_id='x', user_id='alice', events=[
                dict(method='POST', uri='/login', statusCode=200,
                     responseBody={'authentication': {'id': 6}})])]))
            original = ReplaySession.process
            def wrong_status(session, event):
                return replace(original(session, event), status='outside_model')
            with patch.object(ReplaySession, 'process', wrong_status):
                report = feed(spec, path, Path(directory) / 'out', compare=True)
            self.assertFalse(report['comparison']['equivalent'])
            self.assertEqual(1, report['comparison']['mismatches'])

    def test_jsonl_streaming_interleaving_and_report(self):
        records = [dict(trace_id='a', user_id='alice', method='POST', uri='/login',
                        statusCode=200, responseBody={'authentication': {'id': 6}}),
                   dict(trace_id='b', user_id='bob', method='POST', uri='/login',
                        statusCode=200, responseBody={'authentication': {'id': 7}}),
                   dict(trace_id='a', user_id='alice', method='GET', uri='/accounts/6',
                        statusCode=200, responseBody={'id': 6}),
                   dict(trace_id='b', user_id='bob', method='GET', uri='/accounts/7',
                        statusCode=200, responseBody={'id': 7})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in records))
            report = feed(ROOT / 'examples/contas.openapi.json', path,
                          Path(directory) / 'out', compare=True)
            self.assertTrue(report['comparison']['equivalent'])
            self.assertEqual(4, report['comparison']['compared_prefixes'])
            self.assertEqual(2, report['state']['sessions'])
            self.assertTrue(all(r.accepted for r in report['results']))
            self.assertEqual(4, len((Path(directory) / 'out/eventos.jsonl').read_text().splitlines()))
            # O gerador entrega o primeiro evento antes de tentar ler a próxima linha.
            path.write_text(json.dumps(records[0]) + '\ninvalid')
            events = iter_events(path)
            self.assertEqual('a', next(events)[0])
            with self.assertRaises(ValueError):
                next(events)


if __name__ == '__main__':
    unittest.main()
