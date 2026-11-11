"""Notebook QA: runnable code syntax, no committed outputs, offline-only research."""
import json
import unittest
from pathlib import Path
from scripts.audit_public_tree import inspect_file

ROOT=Path(__file__).resolve().parents[1]

class ColabNotebookTests(unittest.TestCase):
    def test_notebooks_are_public_safe_and_compile(self):
        for name in ('01_prepare_development.ipynb','02_discover_offline.ipynb','03_premove_offline.ipynb'):
            with self.subTest(name=name):
                p=ROOT/'colab'/name
                raw=p.read_bytes()
                self.assertFalse(inspect_file('colab/'+name,raw))
                nb=json.loads(raw)
                for i,cell in enumerate(nb['cells']):
                    self.assertEqual(cell.get('outputs',[]),[])
                    self.assertIsNone(cell.get('execution_count'))
                    if cell['cell_type']=='code':
                        compile(''.join(cell['source']),f'{name}:{i}','exec')
    def test_offline_notebook_never_requests_drive(self):
        for name in ('02_discover_offline.ipynb','03_premove_offline.ipynb'):
            nb=json.loads((ROOT/'colab'/name).read_text())
            code='\n'.join(''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code')
            self.assertNotIn('drive.mount(',code)
            self.assertNotIn('authenticate_user(',code)
            self.assertIn('files.upload()',code)
            self.assertNotIn('GoogleDrivePinnedSource(',code)
    def test_public_audit_rejects_saved_results(self):
        raw=(ROOT/'colab'/'02_discover_offline.ipynb').read_bytes()
        nb=json.loads(raw)
        nb['cells'][1]['outputs']=[{'output_type':'stream','text':'SECRET'}]
        self.assertIn('notebook outputs/attachments or unsupported cells',inspect_file('colab/02_discover_offline.ipynb',json.dumps(nb).encode()))

    def test_premove_03_registers_before_labels_and_downloads_private_report(self):
        nb=json.loads((ROOT/'colab'/'03_premove_offline.ipynb').read_text())
        code=[''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code']
        ix_plan=next(i for i,c in enumerate(code) if 'make_premove_plan(payload)' in c)
        ix_labels=next(i for i,c in enumerate(code) if 'run_premove_study(payload,plan)' in c)
        self.assertLess(ix_plan,ix_labels)
        self.assertIn('files.download(str(report_path))', '\n'.join(code))
        self.assertIn('actual_hash!=expected_hash', '\n'.join(code))

if __name__=='__main__':unittest.main()
