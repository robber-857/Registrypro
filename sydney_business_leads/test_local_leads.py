import csv
import html
import io
import json
import unittest
import tempfile
from pathlib import Path
import registration
from unittest.mock import patch
import local_leads as l


def listing(name='Cafe Test',address='1 Test St, Burwood NSW 2134',website=''):
    return '<a data-entity="'+html.escape(json.dumps({'entity':{'title':name,'address':address,'website':website,'phone':'02 9000 0000','id':'ypid.test'}}),quote=True)+'">x</a>'


class LocalLeadsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.storage=patch.object(registration,'PATH',Path(self.temp.name)/'annotations.json')
        self.storage.start()
    def tearDown(self):
        self.storage.stop();self.temp.cleanup()
    def test_entity_attribute_decoding(self):
        self.assertEqual(l.parse_listings(listing('A & B'))[0]['title'],'A & B')
    def test_challenge_is_not_empty_success(self):
        with self.assertRaises(l.CollectionError): l.parse_listings('<html>Verify you are human</html>')
    def test_strict_postcode_and_state(self):
        self.assertTrue(l.address_matches('Burwood New South Wales 2134','2134','NSW'))
        self.assertFalse(l.address_matches('Burwood Victoria 3125','2134','NSW'))
        self.assertFalse(l.address_matches('Strathfield NSW 2135','2134','NSW'))
        self.assertFalse(l.address_matches('NSW 12134','2134','NSW'))
    def test_private_links_rejected(self):
        with self.assertRaises(l.CollectionError): l.public_url('http://127.0.0.1/test')
        with self.assertRaises(l.CollectionError): l.public_url('file:///c:/test')
    def test_script_contacts_excluded(self):
        p=l.Page(); p.feed('<script>info@fake.example</script><p>info@business.example</p>')
        emails,_=l.extract_contacts(p.text)
        self.assertEqual(emails,['info@business.example'])
    def test_named_person_mailbox_excluded(self):
        emails,_=l.extract_contacts('jane.smith@example.com info@example.com')
        self.assertEqual(emails,['info@example.com'])
    def test_multi_branch_emails_not_selected(self):
        row={'website':'https://example.com','phone':'02 9000 0000','notes':'','postcode':'2134'}
        with patch.object(l,'fetch',return_value=('<p>info@one.example admin@two.example</p>','https://example.com',False)):
            result=l.enrich(row)
        self.assertEqual(result['email'],'')
        self.assertIn('多个公共邮箱',result['notes'])
    def test_csv_injection_and_unicode(self):
        data=l.csv_bytes([{'name':'=SUM(1,2)','category':'餐饮','phone':'+61 2 9000 0000'}])
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        row=list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))[1]
        self.assertEqual(row[0],"'=SUM(1,2)")
    def test_validation(self):
        for args in [('abc','NSW',20),('2134','ZZ',20),('2134','NSW',101)]:
            with self.assertRaises(l.CollectionError): l.validate(*args)
    def test_nearby_keeps_actual_postcode_and_deduplicates(self):
        body=listing()+listing()+listing('Next Cafe','2 Test St, Strathfield NSW 2135')+listing('Far','Victoria 3125')
        with patch.object(l,'fetch',return_value=(body,'https://bing.com',False)),patch.object(l.time,'sleep'):
            rows,meta=l.collect('2134',limit=5,categories=['餐饮'],include_nearby=True)
        self.assertEqual([r['postcode'] for r in rows],['2134','2135'])
        self.assertEqual(rows[1]['requested_postcode'],'2134')
        self.assertIn('邻区',rows[1]['location_scope'])
        self.assertFalse(meta['target_reached'])
    def test_strict_collection_excludes_neighbours(self):
        body=listing()+listing('Next','2 Test St, Strathfield NSW 2135')
        with patch.object(l,'fetch',return_value=(body,'https://bing.com',False)),patch.object(l.time,'sleep'):
            rows,_=l.collect('2134',limit=5,categories=['餐饮'],include_nearby=False)
        self.assertEqual(len(rows),1)
    def test_partial_source_error_is_visible(self):
        with patch.object(l,'fetch',side_effect=[(listing(),'https://bing.com',False),l.CollectionError('blocked')]),patch.object(l.time,'sleep'):
            rows,meta=l.collect('2134',limit=5,categories=['餐饮'])
        self.assertEqual(len(rows),1)
        self.assertEqual(meta['status'],'partial')
        self.assertIn('blocked',meta['errors'][0])


if __name__=='__main__': unittest.main()
