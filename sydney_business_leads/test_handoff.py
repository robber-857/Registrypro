import io
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch
import registration as r
import browser_registry as b
from renewal_export import xlsx
from local_leads import csv_bytes

class HandoffTests(unittest.TestCase):
    def test_date_boundaries_and_exports_preserve_registered(self):
        row={'name':'Synthetic','business_name':'Synthetic','business_name_renewal_date':'2020-01-01','asic_status':'Registered','match_status':'商号及续期日已确认'}
        self.assertIn('日期已过',r.renewal_notice(row,date(2026,11,18)))
        self.assertIn('今天',r.renewal_notice(row,date(2020,1,1)))
        self.assertIn('未来',r.renewal_notice(row,date(2019,12,31)))
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'PATH',Path(tmp)/'manual.json'):
            exported=csv_bytes([row]).decode('utf-8-sig')
            self.assertIn('日期已过',exported)
            self.assertIn('Registered',exported)
            with zipfile.ZipFile(io.BytesIO(xlsx([row]))) as z:
                sheet=z.read('xl/worksheets/sheet1.xml').decode()
                self.assertIn('日期已过',sheet)
                self.assertIn('Registered',sheet)
        self.assertNotIn('renewal_date_notice',r.EDITABLE)

    def test_ocr_count_survives_checkpoint_and_includes_failures_and_fallback(self):
        row={'name':'Synthetic','address':''}
        task={'id':'synthetic','row':row,'key':r.key(row),'kind':'business','state':'running'}
        payload={'id':'synthetic','url':'https://connectonline.asic.gov.au/RegistrySearch/','screenshot':'fixture'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'PATH',Path(tmp)/'manual.json'),patch.dict(b.STATE,{'tasks':[task],'results':[],'rows':[]},clear=True):
            with patch('ocr_fallback.extract',return_value={'text':'fixture'}):b.inspect_screenshot(payload)
            with patch('ocr_fallback.extract',side_effect=ValueError('fixture failure')):
                with self.assertRaises(ValueError):b.inspect_screenshot(payload)
            saved=json.loads((Path(tmp)/'browser_queue.json').read_text())
            b.STATE['tasks']=saved['tasks']
            with patch('ocr_fallback.extract',side_effect=ValueError('fixture fallback failure')):b.result({**payload,'metrics':{'ocr_attempts':1}})
            metrics=b.STATE['results'][0]['metrics']
            self.assertEqual(metrics['ocr_attempts'],3)
            self.assertEqual(metrics['reader_ocr_attempts'],1)
