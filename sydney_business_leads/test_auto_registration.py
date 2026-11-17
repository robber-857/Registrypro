import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import registration
import auto_registration as a

ABN='99115089033'
def official(name='Synthetic Cafe',status='Active from 01 Jul 2005'):
    return f'''<h1>Current details for ABN 99 115 089 033</h1>
    <table><tr><th>Entity name:</th><td>Synthetic Pty Ltd</td></tr><tr><th>ABN status:</th><td>{status}</td></tr><tr><th>Entity type:</th><td>Australian Private Company</td></tr></table>
    <table><tr><td>Explanation</td></tr><tr><th>Business name</th><th>From</th></tr><tr><td><a href="https://connectonline.asic.gov.au/business">{name}</a></td><td>01 Jul 2005</td></tr></table>
    <table><caption>Trading name(s)</caption><tr><th>Trading name</th><th>From</th></tr><tr><td>Old name</td><td>2001</td></tr></table>
    <table><caption>ASIC registration - ACN or ARBN or ARSN or ARFN</caption><tr><td>115 089 033 <a href="https://connectonline.asic.gov.au/company">View record on the ASIC website</a></td></tr></table>'''


class AutomaticRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.storage=patch.object(registration,'PATH',Path(self.temp.name)/'manual.json');self.storage.start()
        self.row={'name':'Synthetic Cafe','address':'1 Test St NSW 2134','postcode':'2134','website':'https://synthetic.example/','phone':'02 9000 0000'}
        self.site='<p>Synthetic Cafe NSW 2134 02 9000 0000 ABN: 99 115 089 033</p>'
    def tearDown(self):self.storage.stop();self.temp.cleanup()
    def fetch(self,url,*args,**kwargs):
        if 'synthetic.example' in url:return self.site,url,False
        if 'abr.business' in url:return official(),url,False
        return 'JavaScript required',url,False
    def test_strong_match_auto_saves_names_but_never_invents_dates(self):
        with patch('local_leads.fetch',side_effect=self.fetch):row=a.investigate(self.row)
        self.assertEqual(row['identity_status'],'自动匹配')
        self.assertEqual(row['business_name'],'Synthetic Cafe')
        self.assertEqual(row['legal_name'],'Synthetic Pty Ltd')
        self.assertEqual(row['acn'],'115089033')
        self.assertEqual(row['company_next_review_date'],'')
        self.assertIn('JavaScript',row['date_status'])
        self.assertEqual(registration.apply([self.row])[0]['abn'],ABN)
    def test_multiple_website_abns_are_not_arbitrarily_selected(self):
        self.site+=' ABN 51 642 681 114'
        with patch('local_leads.fetch',side_effect=self.fetch):row=a.investigate(self.row)
        self.assertEqual(row['identity_status'],'自动未确认')
        self.assertEqual(row['abn'],'')
        self.assertEqual(len(json.loads(row['auto_candidates'])),2)
    def test_missing_location_evidence_does_not_confirm(self):
        self.site='<p>Synthetic Cafe ABN 99 115 089 033</p>'
        with patch('local_leads.fetch',side_effect=self.fetch):row=a.investigate(self.row)
        self.assertEqual(row['identity_status'],'自动未确认')
        self.assertEqual(row['legal_name'],'')
    def test_similar_name_and_cancelled_entity_do_not_confirm(self):
        for html in (official('Synthetic Cafe Franchise'),official(status='Cancelled from 01 Jan 2020')):
            with patch('local_leads.fetch',side_effect=lambda url,*args,**kwargs:(self.site if 'synthetic.example' in url else html,url,False)):
                row=a.investigate(self.row,True)
            self.assertEqual(row['identity_status'],'自动未确认')
    def test_wrong_abn_and_challenges_are_rejected(self):
        with self.assertRaises(ValueError):a.parse_abr(official(),'51642681114')
        with self.assertRaises(ValueError):a.parse_abr('Please verify you are human',ABN)
    def test_business_names_exclude_legacy_trading_names(self):
        self.assertEqual(a.parse_abr(official(),ABN)['names'],['Synthetic Cafe'])
    def test_asic_date_requires_matching_record_and_correct_label(self):
        html='Company Summary ACN: 115 089 033 Next review date: 1/07/2027'
        self.assertEqual(a.date_from_asic(html,'Next review date','115089033'),'2027-07-01')
        self.assertEqual(a.date_from_asic(html,'Next renewal date','115089033'),'')
        self.assertEqual(a.date_from_asic(html,'Next review date','111111111'),'')
    def test_manual_verification_is_not_overwritten(self):
        registration.save(registration.key(self.row),{'legal_name':'Manual company','abn':ABN,'identity_status':'人工核实','identity_source':'https://abr.business.gov.au/','identity_evidence':'Manual fixture'})
        with patch('local_leads.fetch',side_effect=AssertionError('must not fetch')):
            row=a.investigate(self.row,True)
        self.assertEqual(row['identity_status'],'人工核实')
    def test_failed_website_records_reason_and_continues(self):
        with patch('local_leads.fetch',side_effect=OSError('unavailable')):row=a.investigate(self.row)
        self.assertIn('unavailable',row['auto_reason'])
        self.assertEqual(row['identity_status'],'自动未确认')
    def test_no_abn_search_produces_candidates_not_confirmed_identity(self):
        def fetch(url,*args,**kwargs):
            if 'synthetic.example' in url:return 'Synthetic Cafe 2134',url,False
            if '/Search/' in url:return '<a href="/ABN/View?abn=99115089033">Synthetic Cafe</a>',url,False
            return official(),url,False
        with patch('local_leads.fetch',side_effect=fetch):row=a.investigate(self.row)
        self.assertEqual(row['identity_status'],'自动未确认')
        self.assertEqual(json.loads(row['auto_candidates'])[0]['abn'],ABN)
        self.assertEqual(row['business_name'],'')


if __name__=='__main__':unittest.main()
