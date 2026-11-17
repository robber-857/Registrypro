import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ocr_vision as vision

class VisionTests(unittest.TestCase):
    def test_disabled_does_not_send_image(self):
        with patch.object(vision,'configuration',return_value={'enabled':False}),patch.object(vision,'urlopen') as send:
            self.assertIsNone(vision.extract('image'));send.assert_not_called()

    def test_missing_config_fails_without_network(self):
        with patch.object(vision,'configuration',return_value={'enabled':True}),patch.dict('os.environ',{},clear=True),patch.object(vision,'urlopen') as send:
            with self.assertRaises(ValueError):vision.extract('image')
            send.assert_not_called()

    def test_screenshot_request_uses_structured_output_and_no_storage(self):
        parsed={'page_type':'results','query':'young accountants','text':'Search Results','rows':[{'name':'Young Accountants','status':'Cancelled','abn':'','acn':'','renewal_date':''}]}
        response={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(parsed)}]}]}
        with patch.object(vision,'configuration',return_value={'enabled':True,'api_key':'synthetic-test-key','model':'test-vision'}),patch.dict('os.environ',{},clear=True),patch.object(vision,'urlopen') as send:
            send.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            out=vision.extract('data:image/png;base64,synthetic')
            body=json.loads(send.call_args.args[0].data)
            self.assertFalse(body['store']);self.assertTrue(body['text']['format']['strict'])
            self.assertEqual(out['rows'][0]['status'],'Cancelled');self.assertEqual(out['rows'][0]['abn'],'')

    def test_incomplete_response_is_not_accepted(self):
        with patch.object(vision,'configuration',return_value={'enabled':True,'api_key':'synthetic','model':'test'}),patch.object(vision,'urlopen') as send:
            send.return_value.__enter__.return_value.read.return_value=b'{"status":"incomplete","output":[]}'
            with self.assertRaises(ValueError):vision.extract('image')
