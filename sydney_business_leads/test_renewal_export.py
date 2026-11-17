import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from renewal_export import xlsx
from ocr_fallback import structure_local

class ExportTests(unittest.TestCase):
    def test_only_confirmed_registered_pair_enters_date_sheet(self):
        good=dict(name='A',business_name='A',business_name_renewal_date='2029-01-01',asic_status='Registered',match_status='商号及续期日已确认')
        rows=[good,{**good,'name':'Cancelled row','asic_status':'Cancelled'},{**good,'name':'Candidate row','match_status':'高匹配候选'},{**good,'name':'No date','business_name_renewal_date':''}]
        with zipfile.ZipFile(io.BytesIO(xlsx(rows))) as z:
            for name in z.namelist():ET.fromstring(z.read(name))
            self.assertNotIn('Cancelled row',z.read('xl/worksheets/sheet1.xml').decode())
            self.assertIn('Cancelled row',z.read('xl/worksheets/sheet2.xml').decode())
            self.assertIn('Candidate row',z.read('xl/worksheets/sheet2.xml').decode())

    def test_local_screenshot_cancelled_rows_keep_missing_numbers_empty(self):
        data=structure_local({'text':'2 results found for "young accountants"\nYoung Accountants Not Available Cancelled Homebush NSW 2140\nYOUNGACCOUNTANTS Not Available Cancelled Homebush NSW 2140'})
        self.assertEqual(len(data['rows']),2)
        self.assertTrue(all(r['status']=='Cancelled' and not r['abn'] and not r['acn'] for r in data['rows']))
