"""Testes das medições; a execução dos autores é feita pelo script de integração."""
from pathlib import Path
import tempfile
import unittest

from cpn_bola import user_token
from cpn_openapi import build_openapi_cpn, resource_token
from scripts.comparison_fixtures import make
from scripts.compare_links2cpn import identity, own_projection, ref_projection, run_ours


class ComparisonTests(unittest.TestCase):
    def test_nested_multiset_is_not_equated_with_flat_token(self):
        owner = identity('alice')
        token = {'value': {'id':'4','user_id':owner}}
        flat = {'id patch-/memos/{id}':[token]}
        nested = {'id patch-/memos/{id}':[{'nested_multiset':[token]}]}
        self.assertNotEqual(ref_projection(flat,owner),ref_projection(nested,owner))

    def test_owner_and_multiplicity_are_preserved(self):
        owner = identity('alice')
        a = {'value':{'id':'6','user_id':owner}}
        b = {'value':{'id':'6','user_id':identity('bob')}}
        counts = ref_projection({'id get-/accounts/{id}':[a,a,b]},owner)
        self.assertEqual(2,sum(counts.values()))

    def test_auxiliary_user_place_is_explicitly_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            case=make(directory)[0]
            net=build_openapi_cpn(case['spec'],legacy_expressions=True)
            place=next(p for p in net.places if p!='p_user')
            mark={'p_user':[user_token('alice')],place:[resource_token('alice','6')]}
            owner=identity('alice')
            reference={'id get-/accounts/{id}':[{'value':{'id':'6','user_id':owner}}]}
            self.assertEqual(ref_projection(reference,owner),own_projection(net,mark,owner))

    def test_first_failure_does_not_count_tail_as_analyzed(self):
        with tempfile.TemporaryDirectory() as directory:
            case=next(c for c in make(directory) if c['name']=='apos-erro')
            report,_,_=run_ours(case)
            self.assertEqual([2],report['failed_records'])
            self.assertEqual([3,4],report['unprocessed_records'])
            self.assertEqual([1],report['successful_records'])

    def test_fixtures_are_deterministic(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first,second=make(a),make(b)
            self.assertEqual(13,len(first))
            for x,y in zip(first,second):
                self.assertEqual(Path(x['spec']).read_bytes(),Path(y['spec']).read_bytes())
                self.assertEqual(Path(x['logs']).read_bytes(),Path(y['logs']).read_bytes())


if __name__=='__main__':
    unittest.main()
