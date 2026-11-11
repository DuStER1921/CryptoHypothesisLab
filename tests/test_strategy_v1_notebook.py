"""Offline notebook static safety and preregistration sequencing QA."""
import json
import unittest
from pathlib import Path
from scripts.audit_public_tree import inspect_file

ROOT=Path(__file__).resolve().parents[1]
NAME='08_trade_candidate_offline.ipynb'

class TradeCandidateNotebookSafety(unittest.TestCase):
    def test_private_offline_clean_compilable(self):
        raw=(ROOT/'colab'/NAME).read_bytes()
        self.assertFalse(inspect_file('colab/'+NAME,raw))
        nb=json.loads(raw)
        code=[]
        for i,cell in enumerate(nb['cells']):
            self.assertEqual(cell.get('outputs',[]),[])
            self.assertIsNone(cell.get('execution_count'))
            if cell['cell_type']=='code':
                src=''.join(cell['source']);compile(src,f'{NAME}:{i}','exec');code.append(src)
        entire='\n'.join(code)
        self.assertIn('files.upload()',entire)
        self.assertNotIn('drive.mount(',entire)
        self.assertNotIn('authenticate_user(',entire)
        self.assertNotIn('GoogleDrivePinnedSource(',entire)
        self.assertIn('if hashlib.sha256(raw).hexdigest()!=expected',entire)
        self.assertLess(next(i for i,x in enumerate(code) if 'plan=make_strategy_plan(packet)' in x),
                        next(i for i,x in enumerate(code) if 'report=run_strategy_v1(packet,plan)' in x))
        self.assertIn("plan_path.open('xb')",entire)
        self.assertIn("report_path.open('x'",entire)
        self.assertIn('owner_token!=fields[\'freeze_token_sha256\']',entire)
        self.assertIn('files.download(str(report_path))',entire)

if __name__=='__main__':unittest.main()
