"""Replay the observed Seedance failure without submitting a real generation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from models.tudou_client import TudouClient

TASK_ID = 'task_p8JVHqHCbAw8G0UR7qRqWbntxz0IxvG5'
EVIDENCE = Path(__file__).resolve().parent / 'fixtures/tudou_seedance_timeout.json'


def response(payload):
    value = Mock()
    value.json.return_value = payload
    value.raise_for_status.return_value = None
    return value


class TudouDiagnosticsTests(unittest.TestCase):
    def test_observed_upstream_failure_keeps_task_and_request_receipt(self):
        observed = json.loads(EVIDENCE.read_text())['observation']
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'clip.mp4'
            client = TudouClient(api_key='test-key')
            with patch('models.tudou_client.requests.post', return_value=response({'data': {'id': TASK_ID}})) as submit, \
                 patch('models.tudou_client.requests.get', return_value=response({'data': observed})), \
                 patch.object(client, '_download') as download:
                with self.assertRaises(RuntimeError) as caught:
                    client.generate_video('雨夜进店', 'https://media.example/SD01.png', str(target), model='seedance-2.0')
                self.assertEqual(getattr(caught.exception, 'task_id', None), TASK_ID,
                                 '上游失败必须保留原任务ID，才能核查而不重新扣费')
                receipt = json.loads(Path(f'{target}.tudou-task.json').read_text())
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['error']['code'], 'upstream_failed')
                self.assertEqual(receipt['request']['model'], 'seedance-2.0')
                self.assertNotIn('test-key', json.dumps(receipt))
                submit.assert_called_once()
                download.assert_not_called()

    def test_local_poll_timeout_is_unknown_provider_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'clip.mp4'
            client = TudouClient(api_key='test-key')
            with patch.object(client, '_submit', return_value=TASK_ID) as submit, \
                 patch.object(client, '_poll', side_effect=TimeoutError(f'土豆 API 任务超时：{TASK_ID}')):
                with self.assertRaises(TimeoutError):
                    client.generate_video('雨夜进店', '', str(target), model='seedance-2.0')
                self.assertTrue(Path(f'{target}.tudou-task.json').is_file(),
                                '本地查询超时前必须留下已接受的任务，不能把未知状态当作生成失败')
                receipt = json.loads(Path(f'{target}.tudou-task.json').read_text())
                self.assertEqual(receipt['status'], 'unknown')
                self.assertEqual(receipt['error']['code'], 'poll_timeout')
                self.assertEqual(receipt['task_id'], TASK_ID)
                submit.assert_called_once()

    def test_submission_timeout_is_not_resent_when_acceptance_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'clip.mp4'
            client = TudouClient(api_key='test-key')
            with patch('models.tudou_client.requests.post', side_effect=requests.Timeout('response lost')) as submit:
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, '避免重复扣费'):
                        client.generate_video('雨夜进店', '', str(target), model='seedance-2.0')
                receipt = json.loads(Path(f'{target}.tudou-task.json').read_text())
                self.assertEqual((receipt['status'], receipt['error']['code']), ('unknown', 'submit_unknown'))
                self.assertIsNone(receipt['task_id'])
                submit.assert_called_once()

    def test_explicit_http_rejection_is_not_an_unknown_paid_task(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'clip.mp4'
            client = TudouClient(api_key='test-key')
            rejected = response({'error': 'invalid request'})
            rejected.status_code = 422
            rejected.raise_for_status.side_effect = requests.HTTPError('Unprocessable Entity', response=rejected)
            with patch('models.tudou_client.requests.post', return_value=rejected):
                with self.assertRaisesRegex(RuntimeError, '拒绝提交'):
                    client.generate_video('雨夜进店', '', str(target), model='seedance-2.0')
                receipt = json.loads(Path(f'{target}.tudou-task.json').read_text())
                self.assertEqual(receipt['status'], 'rejected')
                self.assertEqual(receipt['error']['code'], 'submit_rejected')


if __name__ == '__main__':
    unittest.main()
