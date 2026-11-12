"""Code-only Colab09 preflight; no private ZIP or historical source reads."""
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NOTEBOOK=ROOT/'colab'/'09_failed_auction_v2_offline.ipynb'

class OfflineNotebookTests(unittest.TestCase):
    def test_code_compiles_and_notebook_has_no_saved_outputs(self):
        obj=json.loads(NOTEBOOK.read_text(encoding='utf8'))
        self.assertEqual(obj['nbformat'],4)
        for n,cell in enumerate(obj['cells']):
            if cell['cell_type']!='code':continue
            self.assertEqual(cell.get('outputs'),[])
            self.assertIsNone(cell.get('execution_count'))
            compile(''.join(cell['source']),str(NOTEBOOK)+':'+str(n),'exec')

    def test_no_source_read_and_exact_plan_precedes_trading_outcome(self):
        obj=json.loads(NOTEBOOK.read_text(encoding='utf8'))
        cells=[''.join(c['source']) for c in obj['cells'] if c['cell_type']=='code']
        code='\n'.join(cells)
        self.assertNotIn('drive.mount(',code)
        self.assertNotIn('authenticate_user(',code)
        self.assertNotIn('GoogleDrivePinnedSource(',code)
        self.assertIn('files.upload()',code)
        self.assertIn('expected!=actual',code)
        self.assertIn('july_token!=p[',code)
        plan=next(i for i,c in enumerate(cells) if 'plan=make_strategy_v2_plan(packet)' in c)
        frozen=next(i for i,c in enumerate(cells) if "files.download(str(plan_file))" in c)
        run=next(i for i,c in enumerate(cells) if 'report=run_strategy_v2(packet,plan)' in c)
        self.assertLess(plan,frozen)
        self.assertLess(frozen,run)
        self.assertIn('files.download(str(report_file))',code)

if __name__=='__main__':unittest.main()
