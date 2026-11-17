import json
import sqlite3
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import abn_bulk as a
import browser_registry as b
import registration
import postcode_automation as automation

XML='''<ABR><ABN status="ACT" ABNStatusFromDate="20000101">99115089033</ABN><EntityType><EntityTypeText>Australian Private Company</EntityTypeText></EntityType><MainEntity><NonIndividualName type="MN"><NonIndividualNameText>Test Pty Ltd</NonIndividualNameText></NonIndividualName><BusinessAddress><AddressDetails><State>NSW</State><Postcode>2134</Postcode></AddressDetails></BusinessAddress></MainEntity><ASICNumber ASICNumberType="ACN">115089033</ASICNumber><OtherEntity><NonIndividualName type="BN"><NonIndividualNameText>Test Cafe</NonIndividualNameText></NonIndividualName></OtherEntity><OtherEntity><NonIndividualName type="TRD"><NonIndividualNameText>Old Trading Name</NonIndividualNameText></NonIndividualName></OtherEntity></ABR>'''

class ABNBulkTests(unittest.TestCase):
    def test_incomplete_snapshot_keeps_previous_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'abn.sqlite';old.write_bytes(b'previous index')
            resources=[{'format':'ZIP','url':'https://example.test/part'+str(i)+'.zip'} for i in (1,2)]
            (root/'package.json').write_text(json.dumps({'result':{'resources':resources}}))
            for i in (1,2):
                with zipfile.ZipFile(root/f'part{i}.zip','w') as z:
                    z.writestr(f'part{i}.xml','<Transfer>'+XML.replace('99115089033',str(i))+'</Transfer>')
            with self.assertRaisesRegex(ValueError,'20'):
                a.build_index(root)
            self.assertEqual(old.read_bytes(),b'previous index')

    def test_registered_names_only_and_active_filter(self):
        row=a.parse_entity(ET.fromstring(XML))
        self.assertEqual(row['business_names'],['Test Cafe'])
        self.assertEqual(row['acn'],'115089033')
        self.assertIsNone(a.parse_entity(ET.fromstring(XML.replace('status="ACT"','status="CAN"'))))
        self.assertEqual(a.parse_entity(ET.fromstring(XML.replace('ASICNumberType="ACN"','ASICNumberType="ARBN"')))['acn'],'')

    def test_undetermined_number_requires_company_type_and_valid_matching_number(self):
        e={**a.parse_entity(ET.fromstring(XML)),'acn':'','asic_number':'115089033','asic_number_type':'undetermined'}
        self.assertEqual(a.company_acn(e),'115089033')
        self.assertEqual(a.company_acn({**e,'entity_type':'Fixed Unit Trust'}),'')
        self.assertEqual(a.company_acn({**e,'asic_number_type':'ARBN'}),'')
        self.assertEqual(a.company_acn({**e,'asic_number':'115089034'}),'')

    def test_index_filters_state_and_preserves_every_business_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('create table entities(abn text,state text,postcode text,payload text)')
                db.execute('create table metadata(value text)')
                db.execute('insert into metadata values (?)',(json.dumps({'extract_times':['2026-11-11']}),))
                for index,state,names in [(1,'NSW',['Brand A','Brand B']),(2,'NSW',[]),(3,'VIC',['Other'])]:
                    row={**a.parse_entity(ET.fromstring(XML)),'abn':str(index),'state':state,'business_names':names}
                    db.execute('insert into entities values (?,?,?,?)',(str(index),state,'2134',json.dumps(row)))
            db.close()
            rows,meta=a.query('2134','NSW',1,db_path=path)
            self.assertEqual([r['business_name'] for r in rows],['Brand A','Brand B'])
            self.assertEqual(meta['total_active_entities'],2)
            self.assertTrue(meta['has_more'])
            tail,meta=a.query('2134','NSW',1,meta['next_offset'],path)
            self.assertEqual(tail[0]['business_name'],'')
            self.assertFalse(meta['has_more'])

    def test_plugin_skips_legal_only_without_network_and_keeps_sibling_names(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(registration,'PATH',Path(folder)/'manual.json'),patch.object(b,'checkpoint'),patch.dict(b.STATE,{},clear=True),patch('local_leads.fetch',side_effect=AssertionError('must not fetch ABR')):
            rows=[{'name':name or 'Test Pty Ltd','business_name':name,'abn':'99115089033','address':'NSW 2134','source_provider':'abn_bulk'} for name in ('Brand A','Brand B','')]
            b.build(rows)
            self.assertEqual([t['state'] for t in b.STATE['tasks']],['queued','queued','done'])
            self.assertEqual([t['name'] for t in b.STATE['tasks'][:2]],['Brand A','Brand B'])
            self.assertEqual(len(b.STATE['results']),1)
            b.build(rows[-1:])
            self.assertEqual(b.STATE['state'],'done')

    def test_export_does_not_merge_sibling_names_after_manual_confirmation(self):
        rows=[{'name':n,'abn':'99115089033','identity_status':'人工核实','source_provider':'abn_bulk'} for n in ('Brand A','Brand B')]
        self.assertEqual(len(registration.dedupe(rows)),2)

    def test_continuous_pagination_csv_and_next_postcode_without_enrichment(self):
        calls=[]
        def query(postcode,state,limit,offset):
            calls.append((postcode,offset))
            n=20 if offset==0 else 1
            rows=[{'name':f'Entity {postcode} {offset+i}','address':postcode,'source_provider':'abn_bulk','abn':str(offset+i)} for i in range(n)]
            return rows,{'next_offset':offset+n,'has_more':offset==0,'snapshot':{},'offset':offset}
        with tempfile.TemporaryDirectory() as folder,patch.object(a,'query',side_effect=query),patch.object(automation.Automation,'_wait',return_value=True),patch.object(automation,'enrich',side_effect=AssertionError('no website enrichment')):
            manager=automation.Automation(folder)
            manager.start(dict(postcode='2134',end_postcode='2135',state='NSW',provider='abn',delay=5,categories=[]))
            manager.thread.join(5)
            self.assertFalse(manager.running())
            self.assertEqual(manager.data['state'],'done')
            self.assertEqual(calls,[('2134',0),('2134',20),('2135',0),('2135',20)])
            self.assertEqual([r['count'] for r in manager.data['history']],[21,21])
            self.assertEqual(len(list(Path(folder).glob('*.csv'))),2)

if __name__=='__main__':unittest.main()
