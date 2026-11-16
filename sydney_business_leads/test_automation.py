import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import postcode_automation as a
from test_local_leads import listing


def options(**kwargs):
    return dict(postcode='2134', end_postcode='2135', state='NSW', delay=5,
                categories=['餐饮'], **kwargs)


def source(url, *args, **kwargs):
    query = parse_qs(urlsplit(url).query)
    code = query['q'][0].split()[-2]
    return (''.join(listing(f'Synthetic {i}', f'{i} Test St NSW {code}') for i in range(25)), url, False)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.manager = a.Automation(self.folder)
        self.patches = [patch.object(a, 'fetch', side_effect=source),
                        patch.object(a, 'enrich', side_effect=lambda row, refresh: row),
                        patch.object(a.Automation, '_wait', return_value=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        self.manager.pause()
        if self.manager.thread:
            self.manager.thread.join(5)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def join(self, manager=None):
        manager = manager or self.manager
        manager.thread.join(5)
        self.assertFalse(manager.running())

    def test_increment_export_and_tail_batch(self):
        batches = []
        original = self.manager._export
        def record(reason):
            batches.append((self.manager.data['postcode'], len(self.manager.data['rows'])))
            original(reason)
        with patch.object(self.manager, '_export', side_effect=record):
            self.manager.start(options())
            self.join()
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot['state'], 'done')
        self.assertEqual([(h['postcode'], h['count']) for h in snapshot['history']], [('2134',25),('2135',25)])
        self.assertEqual(batches, [('2134',20),('2134',25),('2134',25),('2135',20),('2135',25),('2135',25)])
        for h in snapshot['history']:
            rows = list(csv.DictReader(io.StringIO(self.manager.download(h['file']).decode('utf-8-sig'))))
            self.assertEqual(len(rows),25)
            self.assertEqual({r['搜索邮编'] for r in rows},{h['postcode']})
            meta=json.loads((self.folder/h['file'].replace('.csv','.json')).read_text(encoding='utf-8'))
            self.assertFalse(meta['complete_area_census'])

    def test_restart_keeps_pending_and_has_no_duplicates(self):
        def pause_batch(row, refresh):
            self.manager.stop.set()
            return row
        with patch.object(a, 'enrich', side_effect=pause_batch):
            self.manager.start(options())
            self.join()
        self.assertEqual(len(self.manager.data['rows']),20)
        self.assertEqual(len(self.manager.data['pending']),5)
        restored = a.Automation(self.folder)
        self.assertEqual(restored.data['state'],'paused')
        restored.resume()
        self.join(restored)
        self.assertEqual([h['count'] for h in restored.data['history']],[25,25])
        self.assertEqual(len({r['name'] for r in restored.data['rows']}),25)

    def test_challenge_pauses_instead_of_advancing(self):
        with patch.object(a,'fetch',return_value=('<html>Verify you are human</html>','https://bing.com',False)):
            self.manager.start(options())
            self.join()
        self.assertEqual(self.manager.data['state'],'paused')
        self.assertEqual(self.manager.data['postcode'],'2134')
        self.assertEqual(self.manager.data['history'],[])
        self.assertEqual(self.manager.data['page'],0)

    def test_skip_saves_partial_and_advances(self):
        with patch.object(a,'fetch',side_effect=OSError('network failed')):
            self.manager.start(options())
            self.join()
        self.manager.skip()
        self.join()
        self.assertEqual([h['count'] for h in self.manager.data['history']],[0,25])
        self.assertIn('用户跳过',self.manager.data['history'][0]['reason'])

    def test_export_failure_does_not_advance_postcode(self):
        with patch.object(self.manager,'_export',side_effect=OSError('disk unavailable')):
            self.manager.start(options())
            self.join()
        self.assertEqual(self.manager.data['state'],'paused')
        self.assertEqual(self.manager.data['postcode'],'2134')
        self.assertEqual(len(self.manager.data['rows']),20)
        self.manager.resume()
        self.join()
        self.assertEqual([h['count'] for h in self.manager.data['history']],[25,25])

    def test_page_cap_and_cross_category_deduplication(self):
        def unique_source(url,*args,**kwargs):
            q=parse_qs(urlsplit(url).query)
            return listing('Synthetic '+q['first'][0]),url,False
        o=options();o.update(categories=['餐饮','咖啡店'],end_postcode='2134')
        with patch.object(a,'fetch',side_effect=unique_source):
            self.manager.start(o)
            self.join()
        self.assertEqual(len(self.manager.data['rows']),5)
        self.assertEqual(len(self.manager.data['requests']),10)
        self.assertTrue(all('上限' in r['reason'] for r in self.manager.data['reasons']))

    def test_validation_and_download_allowlist(self):
        for value in (0,1,10,True,'5'):
            o=options();o['delay']=value
            with self.assertRaises(ValueError): a.options_checked(o)
        for delay in a.DELAYS:
            o=options();o['delay']=delay
            self.assertEqual(a.options_checked(o)['delay'],delay)
        o=options();o['end_postcode']='2133'
        with self.assertRaises(ValueError): a.options_checked(o)
        with self.assertRaises(ValueError): self.manager.download('../checkpoint.json')

    def test_wait_is_interruptible_and_deadline_persists(self):
        self.patches[-1].stop()
        self.manager.start(options())
        self.manager.pause()
        self.join()
        self.assertEqual(self.manager.data['state'],'paused')
        restored=a.Automation(self.folder)
        self.assertEqual(restored.data.get('next_run_at'),self.manager.data.get('next_run_at'))

    def test_finish_retains_partial_and_allows_new_task(self):
        with patch.object(a,'fetch',side_effect=OSError('network failed')):
            self.manager.start(options())
            self.join()
        with self.assertRaises(ValueError): self.manager.start(options())
        self.manager.finish()
        self.assertEqual(self.manager.data['state'],'done')
        self.assertEqual(len(self.manager.data['history']),1)
        self.manager.start(options())
        self.join()
        self.assertEqual(self.manager.data['state'],'done')


if __name__ == '__main__':
    unittest.main()
