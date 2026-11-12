"""Offline Colab 10 safety and chronology checks (static)."""
import json
import unittest
from pathlib import Path
from scripts.audit_public_tree import inspect_file

ROOT=Path(__file__).resolve().parents[1]

class DiscoveryNotebookSafety(unittest.TestCase):
    def test_public_offline_source_compiles(self):
        path=ROOT/'colab'/'10_systematic_discovery_offline.ipynb'
        blob=path.read_bytes()
        self.assertEqual(inspect_file('colab/10_systematic_discovery_offline.ipynb',blob),())
        nb=json.loads(blob)
        self.assertEqual(nb['nbformat'],4)
        cells=[''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code']
        for i,s in enumerate(cells):
            compile(s,f'colab/10:{i}','exec')
        src='\n'.join(cells)
        self.assertNotIn('drive.mount(',src)
        self.assertNotIn('authenticate_user(',src)
        self.assertNotIn('GoogleDrivePinnedSource(',src)
        self.assertNotIn('2025-01',src)
        self.assertNotIn('2026-01',src)
        self.assertIn('files.upload()',src)
        self.assertIn('run_july_discovery(packet,plan)',src)
        self.assertIn('run_august_replay(packet,plan,manifest,july_result_bytes)',src)
        self.assertLess(src.index('files.download(str(plan_file))'),src.index('run_july_discovery(packet,plan)'))
        self.assertLess(src.index('make_replay_manifest(july_report,plan)!=manifest'),src.index("chosen=json.loads(manifest)"))
        self.assertLess(src.index('if not chosen:raise RuntimeError'),src.index('packet=private/'))

if __name__=='__main__':unittest.main()
