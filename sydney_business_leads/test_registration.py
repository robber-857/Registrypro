import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import registration as r
from local_leads import csv_bytes, annotated_csv


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(r,'PATH',Path(self.temp.name)/'annotations.json')
        self.patch.start()
        self.row={'name':'Synthetic cafe','address':'1 Test St NSW 2134'}

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def values(self):
        # The supplied identifiers exercise checksum validation only; this is not a real match.
        return {'business_name':'Synthetic cafe','legal_name':'Synthetic company',
                'abn':'99 115 089 033','acn':'115 089 033','identity_status':'人工核实',
                'identity_source':'https://abr.business.gov.au/ABN/View?id=99115089033',
                'identity_evidence':'Synthetic fixture only: website statement matches identifier'}

    def test_does_not_promote_directory_name_to_registered_name(self):
        row=r.apply([self.row])[0]
        self.assertEqual(row['business_name'],'')
        self.assertEqual(row['legal_name'],'')
        self.assertEqual(row['identity_status'],'待核实')

    def test_annotation_is_specific_to_name_and_address(self):
        r.save(r.key(self.row),self.values())
        same=r.apply([dict(self.row,website='https://changed.example')])[0]
        self.assertEqual(same['business_name'],'Synthetic cafe')
        other=r.apply([dict(self.row,address='2 Test St NSW 2134')])[0]
        self.assertEqual(other['business_name'],'')
        self.assertEqual(same['abn'],'99115089033')

    def test_verification_requires_match_evidence_official_source_and_identifier(self):
        for field in ('identity_evidence','identity_source','abn'):
            values=self.values();values[field]='';values['acn']=''
            with self.assertRaises(ValueError):r.checked(values)
        values=self.values();values['identity_source']='https://asic.gov.au.evil.example/'
        with self.assertRaises(ValueError):r.checked(values)

    def test_review_date_never_becomes_renewal_date(self):
        values=self.values();values.update(company_next_review_date='2027-07-01',review_source='https://connectonline.asic.gov.au/')
        data=r.checked(values)
        self.assertEqual(data['company_next_review_date'],'2027-07-01')
        self.assertEqual(data['business_name_renewal_date'],'')
        values['business_name_renewal_date']='2028-01-02'
        with self.assertRaises(ValueError):r.checked(values)
        values['renewal_source']='https://abr.business.gov.au/'
        with self.assertRaises(ValueError):r.checked(values)
        values['renewal_source']='https://connectonline.asic.gov.au/'
        self.assertEqual(r.checked(values)['business_name_renewal_date'],'2028-01-02')

    def test_invalid_dates_and_identifiers_rejected(self):
        for field,value in [('company_next_review_date','2027-02-30'),('abn','99115089034'),('acn','115089034')]:
            values=self.values();values[field]=value
            with self.assertRaises(ValueError):r.checked(values)

    def test_current_and_historical_csv_include_latest_annotations(self):
        old=csv_bytes([self.row])
        r.save(r.key(self.row),self.values())
        for data in (csv_bytes([self.row]),annotated_csv(old)):
            row=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))[0]
            self.assertEqual(row['Business Name（登记商业名称）'],'Synthetic cafe')
            self.assertEqual(row['注册名称核验状态'],'人工核实')
            self.assertEqual(row['公司下次年度审核日期'],'')

    def test_csv_reexport_does_not_accumulate_formula_escapes(self):
        original=csv_bytes([dict(self.row,name='=test',phone='+61 2 1234 5678')])
        self.assertEqual(annotated_csv(original),original)

    def test_draft_does_not_get_verified_timestamp(self):
        self.assertEqual(r.checked({'business_name':'Candidate'})['identity_checked_at'],'')


if __name__=='__main__':unittest.main()
