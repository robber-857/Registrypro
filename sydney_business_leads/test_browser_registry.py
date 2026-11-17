import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import registration
import browser_registry as b


class BrowserRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.storage=patch.object(registration,'PATH',Path(self.temp.name)/'manual.json');self.storage.start()
        self.row={'name':'Synthetic Cafe','address':'Unit 2/4 Rochester St Homebush NSW 2140'}
        self.task={'id':'test','key':registration.key(self.row),'row':self.row,'kind':'business','name':'SYNTHETIC CAFE','abn':'99115089033','acn':''}
        self.summary='Business Name Summary\nBusiness name: SYNTHETIC CAFE\nStatus: Registered\nRenewal date: 12/10/2029\nPrincipal place of business: Shop 2 4 Rochester Street Homebush NSW 2140\nHolder(s) details: ABN: 99 115 089 033\n'
        self.info={'legal_name':'SYNTHETIC PTY LTD','abn':'99115089033','acn':'115089033','names':['SYNTHETIC CAFE'],'registration_status':'Active from 01 Jul 2005'}
        self.fetch=patch('local_leads.fetch',return_value=('fixture','https://abr.business.gov.au/',False));self.fetch.start()
        self.parse=patch.object(b,'parse_abr',return_value=self.info);self.parse.start()
    def tearDown(self):self.parse.stop();self.fetch.stop();self.storage.stop();self.temp.cleanup()
    def payload(self,text=None):return {'url':'https://connectonline.asic.gov.au/RegistrySearch/faces/landing/panelSearch.jspx','text':text or self.summary}
    def test_full_address_promotes_candidate_and_stores_real_date(self):
        result=b.accept(self.task,self.payload())
        self.assertIn('2029-10-12',result)
        row=registration.apply([self.row])[0]
        self.assertEqual(row['business_name_renewal_date'],'2029-10-12')
        self.assertEqual(row['company_next_review_date'],'')
        self.assertEqual(row['identity_status'],'自动匹配')
    def test_suburb_only_and_wrong_unit_do_not_confirm(self):
        for address in ('Homebush NSW 2140','Unit 3/4 Rochester St Homebush NSW 2140'):
            text=self.summary.replace('Shop 2 4 Rochester Street Homebush NSW 2140',address)
            self.assertIn('关联仍不足',b.accept(self.task,self.payload(text)))
            self.assertEqual(registration.apply([self.row])[0]['business_name'],'')
    def test_wrong_summary_wrong_abn_and_fake_source_rejected(self):
        for payload in (self.payload(self.summary.replace('SYNTHETIC CAFE','OTHER CAFE')),
                        self.payload(self.summary.replace('99 115 089 033','51 642 681 114')),
                        dict(self.payload(),url='https://connectonline.asic.gov.au.evil.example/')):
            with self.assertRaises(ValueError):b.accept(self.task,payload)
    def test_automatic_refresh_cannot_erase_browser_evidence(self):
        b.accept(self.task,self.payload())
        registration.save_auto(self.task['key'],{'identity_status':'自动未确认','business_name_renewal_date':''})
        self.assertEqual(registration.apply([self.row])[0]['business_name_renewal_date'],'2029-10-12')
    def test_company_date_only_for_existing_matched_abn(self):
        text='Company Summary\nName: SYNTHETIC PTY LTD\nACN: 115 089 033\nABN: 99 115 089 033\nStatus: Registered\nNext review date: 10/05/2027\nLocality of registered office: HOMEBUSH NSW 2140'
        task={**self.task,'kind':'company','name':'SYNTHETIC PTY LTD','acn':'115089033'}
        self.assertIn('关联仍不足',b.accept(task,self.payload(text)))
        registration.save_auto(task['key'],{'identity_status':'自动匹配','abn':'99115089033'})
        self.assertIn('2027-05-10',b.accept(task,self.payload(text)))
        self.assertEqual(registration.apply([self.row])[0]['business_name_renewal_date'],'')
    def test_name_aliases_do_not_use_arbitrary_substring(self):
        self.assertTrue(b.compatible('SYNTHETIC CAFE PTY LTD',self.row))
        self.assertFalse(b.compatible('Other Synthetic Cafe Franchise',self.row))
    def test_registered_address_does_not_use_service_address(self):
        text=self.summary.replace('Principal place of business:','Address for service of documents:')
        self.assertIn('关联仍不足',b.accept(self.task,self.payload(text)))
    def test_manual_conflict_is_protected(self):
        registration.save(self.task['key'],{'identity_status':'存在冲突'})
        self.assertIn('保留',b.accept(self.task,self.payload()))

    def test_existing_other_entity_is_not_replaced(self):
        registration.save_auto(self.task['key'],{'identity_status':'自动匹配','abn':'51642681114'})
        self.assertIn('冲突',b.accept(self.task,self.payload()))
        self.assertEqual(registration.apply([self.row])[0]['abn'],'51642681114')

    def test_existing_match_keeps_original_identity_evidence(self):
        registration.save_auto(self.task['key'],{'identity_status':'自动匹配','abn':self.task['abn'],'identity_evidence':'Original website ABN evidence'})
        self.assertIn('2029-10-12',b.accept(self.task,self.payload(self.summary.replace('Shop 2 4 Rochester Street Homebush NSW 2140','Sydney NSW 2000'))))
        self.assertEqual(registration.apply([self.row])[0]['identity_evidence'],'Original website ABN evidence')

    def test_street_ranges_are_not_collapsed(self):
        self.assertFalse(b.same_address('4-8 Rochester St Homebush NSW 2140','4 Rochester St Homebush NSW 2140'))

    def test_unlabelled_unit_cannot_disappear(self):
        self.assertFalse(b.same_address('1 210A Burwood Rd Burwood NSW 2134','210A Burwood Rd Burwood NSW 2134'))
        self.assertTrue(b.same_address('1 210A Burwood Rd Burwood NSW 2134','Level 1/210A Burwood Rd Burwood NSW 2134'))

    def test_website_unit_corroboration_requires_same_phone_and_host(self):
        row={**self.row,'phone':'(02) 8203 8760','website':'https://example.com/','contact_page':'https://example.com/contact'}
        source='<script type="application/ld+json">{"telephone":"+61 2 8203 8760","address":{"streetAddress":"Level 1/210A Burwood Rd","addressLocality":"Burwood","addressRegion":"NSW","postalCode":"2134"}}</script>'
        with patch('local_leads.fetch',return_value=(source,row['contact_page'],False)):
            self.assertIn('Level 1',b.website_address(row,'1 210A Burwood Rd Burwood NSW 2134'))
            self.assertEqual(b.website_address({**row,'phone':'(02) 8203 8761'},'1 210A Burwood Rd Burwood NSW 2134'),'')
            self.assertEqual(b.website_address({**row,'contact_page':'https://other.example/contact'},'1 210A Burwood Rd Burwood NSW 2134'),'')


if __name__=='__main__':unittest.main()
