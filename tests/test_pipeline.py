import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import digest
import local_scheduler
import pipeline


class PipelineTests(unittest.TestCase):
    def rows(self):
        return [dict(id=7, rank=1, title='test', url='https://example.com', excerpt='x'*1000, basis='可见网页正文')]

    def test_package_rejects_tampering_and_wrong_day(self):
        package = pipeline.make_package('2026-09-18', self.rows(), [])
        pipeline.validate_package(package, '2026-09-18')
        with self.assertRaises(ValueError):
            pipeline.validate_package(package, '2026-09-19')
        package['items'][0]['excerpt'] = 'changed'
        with self.assertRaises(ValueError):
            pipeline.validate_package(package, '2026-09-18')

    def test_consumer_never_fetches_and_reuses_send_checkpoint(self):
        day = digest.datetime.now(digest.ZoneInfo('Asia/Shanghai')).date().isoformat()
        package = pipeline.make_package(day, self.rows(), [])
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'STATE', Path(tmp)/'state'), patch.object(pipeline, 'DATA_DIR', Path(tmp)/'data'), patch.object(pipeline, 'require_private_repo'), patch.object(pipeline, 'download_package', return_value=package), patch.object(digest, 'fetch_stories', side_effect=AssertionError('consumer accessed HN')), patch.object(digest, 'model_json', return_value={'items':[dict(id=7, summary='摘要', reason='推荐', relevance=3)]}), patch.object(digest, 'send_card') as send, patch.dict(os.environ, {'LLM_PROVIDER':'llamacpp','LLM_BASE_URL':'http://localhost:8080/v1','LLM_MODEL':'local'}):
            pipeline.consume(day)
            pipeline.consume(day)
            self.assertEqual(send.call_count, 1)

    def test_llamacpp_http_no_key_and_optional_json_format(self):
        reply = {'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}]}
        env = {'LLM_PROVIDER':'llamacpp','LLM_BASE_URL':'http://127.0.0.1:8080/v1','LLM_MODEL':'local','LLM_API_KEY':'','LLM_JSON_MODE':'false'}
        with patch.dict(os.environ, env), patch.object(digest, 'request_json', return_value=reply) as request:
            digest.model_json('prompt', {})
            url, body, headers = request.call_args.args
            self.assertEqual(url, 'http://127.0.0.1:8080/v1/chat/completions')
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('thinking', body)
            self.assertNotIn('response_format', body)

    def test_collector_needs_no_model_or_feishu(self):
        local_scheduler.require_configuration({'PUSH_MODE':'company','TASK_ROLE':'collector','DATA_REPO':'owner/data','DATA_TOKEN':'fixture'})

    def test_collect_publishes_and_uses_winning_package_on_conflict(self):
        day = '2026-09-18'
        winner = pipeline.make_package(day, [dict(self.rows()[0], id=99)], [])
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'DATA_DIR', Path(tmp)), patch.object(pipeline, 'require_private_repo'), patch.object(pipeline, 'download_package', side_effect=[None, winner]), patch.object(pipeline, 'github', return_value=None), patch.object(digest, 'fetch_stories', return_value=self.rows()), patch.object(digest, 'model_json', side_effect=AssertionError('collector called model')):
            pipeline.collect(day)
            saved = json.loads((Path(tmp)/f'{day}.json').read_text())
            self.assertEqual(saved, winner)

    def test_missing_today_does_not_call_model_or_send(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'STATE', Path(tmp)/'state'), patch.object(pipeline, 'DATA_DIR', Path(tmp)/'data'), patch.object(pipeline, 'require_private_repo'), patch.object(pipeline, 'download_package', return_value=None), patch.object(digest, 'main', side_effect=AssertionError('ran without sources')):
            pipeline.consume('2026-09-18')

    def test_failed_summary_does_not_send(self):
        day = digest.datetime.now(digest.ZoneInfo('Asia/Shanghai')).date().isoformat()
        package = pipeline.make_package(day, self.rows(), [])
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'STATE', Path(tmp)/'state'), patch.object(pipeline, 'DATA_DIR', Path(tmp)/'data'), patch.object(pipeline, 'require_private_repo'), patch.object(pipeline, 'download_package', return_value=package), patch.object(digest, 'model_json', return_value={'items':[]}), patch.object(digest.time, 'sleep'), patch.object(digest, 'send_card') as send, patch.dict(os.environ, {'LLM_PROVIDER':'llamacpp','LLM_BASE_URL':'http://localhost:8080/v1','LLM_MODEL':'local'}):
            with self.assertRaises(ValueError):
                pipeline.consume(day)
            send.assert_not_called()

    def test_collector_reuses_previous_backlog(self):
        previous_rows = self.rows()
        carry = [dict(previous_rows[0], id=8)]
        previous = pipeline.make_package('2026-09-17', previous_rows, carry)
        current = pipeline.make_package('2026-09-18', carry, [])
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'DATA_DIR', Path(tmp)), patch.object(pipeline, 'require_private_repo'), patch.object(pipeline, 'download_package', side_effect=[None, previous, current]), patch.object(pipeline, 'github', side_effect=[[{'name':'2026-09-17.json'}], {}]), patch.object(digest, 'fetch_stories', return_value=[]) as fetch:
            pipeline.collect('2026-09-18')
            self.assertEqual(fetch.call_args.kwargs['exclude_ids'], {7, 8})
            saved = json.loads((Path(tmp)/'2026-09-18.json').read_text())
            self.assertEqual(saved['items'][0]['id'], 8)

    def test_consumer_validates_repo_access_before_waiting_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'STATE', Path(tmp)/'state'), patch.object(pipeline, 'DATA_DIR', Path(tmp)/'data'), patch.object(pipeline, 'require_private_repo', side_effect=RuntimeError('bad repo')) as check, patch.object(pipeline, 'download_package', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'bad repo'):
                pipeline.consume('2026-09-18')

    def test_offline_main_refuses_missing_sources(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'STATE', Path(tmp)), patch.object(digest, 'fetch_stories', side_effect=AssertionError('offline fetch')), patch.dict(os.environ, {'LLM_PROVIDER':'llamacpp', 'LLM_BASE_URL':'http://localhost:8080/v1', 'LLM_MODEL':'local'}):
            with self.assertRaisesRegex(RuntimeError, 'Offline mode'):
                digest.main(['run', '--offline'])

    def test_collector_slot_and_local_default(self):
        now = digest.datetime(2026, 9, 18, 8, 0, tzinfo=digest.ZoneInfo('Asia/Shanghai'))
        self.assertEqual(local_scheduler.next_slot(now, ((8, 30), (9, 30))).hour, 8)
        self.assertEqual(local_scheduler.task_role({}), 'local')
        self.assertEqual(local_scheduler.task_role({'PUSH_MODE':'company', 'TASK_ROLE':'consumer'}), 'consumer')
        with self.assertRaises(RuntimeError):
            local_scheduler.task_role({'PUSH_MODE':'invalid'})

    def test_lock_excludes_second_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pipeline.run_lock(Path(tmp)/'lock'):
                with self.assertRaises(RuntimeError):
                    with pipeline.run_lock(Path(tmp)/'lock'):
                        pass

if __name__ == '__main__':
    unittest.main()
