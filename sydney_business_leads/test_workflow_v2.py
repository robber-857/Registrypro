import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
import browser_registry as b
import registration
import google_places
from name_matching import similarity, rank_names


class WorkflowV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=patch.object(registration,'PATH',Path(self.temp.name)/'manual.json');self.path.start()
        self.row={'name':'Synthetic Cafe','address':'4 Test St Sydney NSW 2000','abn':'99115089033'}
        self.info={'abn':'99115089033','names':['Other Brand','Synthetic Cafe'],'legal_name':'Synthetic Pty Ltd',
                   'registration_status':'Active from 2005','links':{},'company_link':'https://connectonline.asic.gov.au/company','acn':'115089033'}
    def tearDown(self):
        self.path.stop();self.temp.cleanup()
    def build(self,rows,**options):
        with patch('local_leads.fetch',return_value=('fixture','url',True)),patch.object(b,'parse_abr',return_value=self.info):
            b.build(rows,**options)
        return b.STATE['tasks']
    def test_typo_word_order_spacing_and_suffix(self):
        self.assertGreater(similarity('Synthetic Cafe Pty Ltd','Cafe Synthetic'),.99)
        self.assertGreater(similarity('Hair Love Strathfield','HAIRLOVE STRATHFIELD','10 Rd Strathfield'),.99)
        self.assertGreater(similarity('Synthetic Cafe','Synthetik Cafe'),.65)
    def test_centre_is_not_tenant(self):
        self.assertEqual(similarity('Strathfield Plaza','Strathfield Plaza Florist'),0)
    def test_person_name_does_not_promote_unrelated_brand(self):
        info={'legal_name':'KIM, SUN YOUNG','names':['Kumon Bankstown Education Centre']}
        row={'name':'Kim Sun Young Strathfield','address':'Strathfield NSW 2135'}
        self.assertLess(rank_names(info,row)[0][0],.5)
    def test_one_business_per_abn_and_related_locations_retained(self):
        tasks=self.build([self.row,{**self.row,'address':'5 Test St Sydney NSW 2000'}])
        self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['name'],'Synthetic Cafe')
        self.assertEqual(len(tasks[0]['related_rows']),1)
        self.assertEqual(tasks[0]['kind'],'business')
    def test_company_query_opt_in(self):
        self.assertEqual(len(self.build([self.row],include_company=True)),2)
    def test_force_refresh_and_24h_cache(self):
        registration.save_browser(registration.key(self.row),{'abn':self.row['abn'],'identity_status':'自动匹配',
            'business_name':'Synthetic Cafe','business_name_renewal_date':'2029-10-12','renewal_checked_at':datetime.now(timezone.utc).isoformat()})
        self.assertTrue(self.build([self.row])[0]['cached'])
        self.assertEqual(self.build([self.row],refresh=True)[0]['state'],'queued')
    def test_pause_prevents_claim_resume_restores(self):
        self.build([self.row]);b.pause('verification')
        self.assertIsNone(b.claim());self.assertEqual(b.snapshot()['state'],'paused')
        b.resume();self.assertIsNotNone(b.claim())
    def test_google_missing_configuration_fails_without_fallback(self):
        with patch.dict('os.environ',{},clear=True),patch.object(google_places,'urlopen') as request:
            with self.assertRaisesRegex(Exception,'未回退'):google_places.search_page('cafes NSW 2135')
            request.assert_not_called()
    def test_google_real_endpoint_pagination_and_source_mapping(self):
        from io import BytesIO
        payload={'places':[{'id':'fixture','displayName':{'text':'Synthetic Cafe'},'formattedAddress':'4 Test St, Sydney NSW 2000, Australia','googleMapsUri':'https://maps.google.com/fixture'}],'nextPageToken':'next'}
        with patch.dict('os.environ',{'GOOGLE_PLACES_API_KEY':'synthetic-test-key'}),patch.object(google_places,'urlopen',return_value=BytesIO(json.dumps(payload).encode())) as request:
            items,token=google_places.search_page('cafes NSW 2000','page2')
        req=request.call_args.args[0]
        self.assertEqual(req.full_url,google_places.ENDPOINT)
        self.assertEqual(json.loads(req.data)['pageToken'],'page2')
        self.assertEqual(items[0]['address'],'4 Test St, Sydney NSW 2000')
        self.assertTrue(items[0]['source'].startswith('https://maps.google.com/'))
        self.assertEqual(token,'next')

    def test_dedupe_confirmed_abn_keeps_other_location_source(self):
        first={**self.row,'identity_status':'自动匹配','source':'https://example.com/one','business_name':'Synthetic Cafe'}
        other={**first,'name':'Synthetic Branch','address':'5 Test St Sydney NSW 2000','source':'https://example.com/two','business_name':'Other Brand'}
        result=registration.dedupe([first,other])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['business_name'],'Synthetic Cafe')
        self.assertEqual(len(json.loads(result[0]['related_locations'])),2)
        self.assertEqual(len(registration.dedupe([{**first,'identity_status':'自动未确认'},{**other,'identity_status':'自动未确认'}])),2)

    def test_continuous_pipeline_waits_for_asic_then_exports(self):
        import postcode_automation as a
        from test_automation import options,source
        manager=a.Automation(Path(self.temp.name)/'automation')
        fake={'state':'idle','generation':'synthetic-queue','completed':0,'total':1,'message':'fixture'}
        def start(rows,**kwargs):fake.update(state='done',completed=1)
        with patch.object(a,'fetch',side_effect=source),patch.object(a,'enrich',side_effect=lambda row,refresh:row),patch.object(manager,'_wait',return_value=True),patch.object(manager.stop,'wait',return_value=False),patch.object(b,'snapshot',side_effect=lambda:dict(fake)),patch.object(b,'start',side_effect=start) as begin:
            manager.start({**options(), 'end_postcode':'2134','use_asic':True})
            manager.thread.join(5)
        self.assertFalse(manager.running())
        self.assertEqual(manager.data['state'],'done')
        self.assertEqual(begin.call_count,2)
        self.assertEqual(manager.data['history'][0]['count'],25)


if __name__=='__main__':unittest.main()
