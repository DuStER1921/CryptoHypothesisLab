"""Safety gate for the offline, no-Drive Colab 07 research notebook."""
import json
import unittest
from pathlib import Path
from scripts.audit_public_tree import inspect_file

ROOT = Path(__file__).resolve().parents[1]
NB = ROOT/'colab'/'07_premove_v31_offline.ipynb'


class NotebookV31Tests(unittest.TestCase):
    def test_colab07_is_public_code_only_offline(self):
        content = NB.read_bytes()
        self.assertFalse(inspect_file('colab/07_premove_v31_offline.ipynb',content))
        nb = json.loads(content)
        parts=[]
        for i,cell in enumerate(nb['cells']):
            self.assertEqual(cell.get('outputs',[]),[])
            self.assertIsNone(cell.get('execution_count'))
            if cell['cell_type']=='code':
                source=''.join(cell['source'])
                compile(source,f'colab07:{i}','exec')
                parts.append(source)
        source='\n'.join(parts)
        self.assertNotIn('drive.mount(',source)
        self.assertNotIn('authenticate_user(',source)
        self.assertNotIn('source_generations=',source)
        self.assertNotIn('prepare_one_micro_month(',source)
        self.assertIn('files.upload()',source)
        self.assertIn('actual!=expected',source)
        self.assertIn('owner_token!=plan_fields',source)
        plan_ix=next(i for i,c in enumerate(parts) if 'files.download(str(plan_path))' in c)
        run_ix=next(i for i,c in enumerate(parts) if 'report=run_v31(packet,plan)' in c)
        self.assertLess(plan_ix,run_ix)
        self.assertIn('files.download(str(report_path))',source)


if __name__=='__main__':unittest.main()
