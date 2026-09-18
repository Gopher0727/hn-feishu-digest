import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import digest


class DigestTests(unittest.TestCase):
    def sample(self, n=15):
        return [dict(id=i, rank=i, title=f'新闻{i}', url=f'https://example.com/{i}',
                     summary='有依据的中文摘要。' * 10, relevance=3,
                     reason='值得阅读', basis='仅标题', repeated=False) for i in range(1, n+1)]

    def test_complete_coverage(self):
        rows = self.sample()
        self.assertEqual(len(digest.validate_rows({'items': rows}, rows)), 15)
        for bad in (rows[:-1], rows + [rows[0]]):
            with self.assertRaises(ValueError):
                digest.validate_rows({'items': bad}, rows)

    def test_model_cannot_change_source_url(self):
        source = self.sample(1)
        model = dict(source[0], url='https://evil.example/', rank=999)
        self.assertEqual(digest.validate_rows({'items':[model]}, source)[0]['url'], source[0]['url'])

    def test_overlong_summary_is_preserved(self):
        source = self.sample(1)
        model = dict(source[0], summary='摘要' * 200)
        row = digest.validate_rows({'items':[model]}, source)[0]
        self.assertEqual(row['summary'], model['summary'])

    def test_model_output_does_not_require_or_keep_topics(self):
        source = self.sample(1)
        model = dict(source[0], topic='AI')
        row = digest.validate_rows({'items':[model]}, source)[0]
        self.assertNotIn('topic', row)

    def test_feishu_business_error_rejected(self):
        with patch.dict('os.environ', {'FEISHU_WEBHOOK':'https://open.feishu.cn/open-apis/bot/v2/hook/test', 'FEISHU_SIGN_SECRET':'test'}):
            with patch.object(digest, 'request_json', return_value={'code':19021}):
                with self.assertRaises(RuntimeError):
                    digest.send_card({'msg_type':'interactive'})

    def test_one_sequential_card_keeps_titles_links_and_summaries(self):
        cards = digest.make_cards(self.sample(), '2026-09-08', 'https://github.com/owner/repo', 0)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['card']['schema'], '2.0')
        elements = cards[0]['card']['body']['elements']
        self.assertEqual([element['tag'] for element in elements], ['markdown', 'collapsible_panel'])
        for card in cards:
            self.assertLess(len(json.dumps(card, ensure_ascii=False).encode()), 20000)
        text = json.dumps(cards, ensure_ascii=False)
        for i in range(1, 16):
            self.assertIn(f'[{i}. 新闻{i}](https://example.com/{i})', text)
        self.assertNotIn('topic', text.lower())
        self.assertNotIn('主题', text)
        self.assertIn('有依据的中文摘要', text)
        self.assertNotIn('值得阅读', text)
        self.assertNotIn('HN讨论', text)
        self.assertNotIn('<at ', text)

    def test_fetch_scans_past_failures_until_target_count(self):
        requested_items = []
        def fake_request(url, *args, **kwargs):
            if url.endswith('topstories.json'):
                return list(range(1, 7))
            item_id = int(url.split('/')[-1].split('.')[0])
            requested_items.append(item_id)
            if item_id % 2 == 0:
                return {'id':item_id, 'title':f'新闻{item_id}', 'text':'短帖正文'}
            return {'id':item_id, 'title':f'新闻{item_id}', 'url':f'https://example.com/{item_id}'}
        with patch.object(digest, 'request_json', side_effect=fake_request), \
             patch.object(digest, 'article_excerpt', return_value=('', '仅标题（原文无法访问）')):
            rows = digest.fetch_stories(target=3, batch_size=2)
        self.assertEqual([row['id'] for row in digest.accessible_stories(rows)], [2, 4, 6])
        self.assertEqual(sorted(requested_items), list(range(1, 7)))

    def test_fetch_exhaustion_returns_available_stories(self):
        def fake_request(url, *args, **kwargs):
            if url.endswith('topstories.json'):
                return [1, 2, 3]
            item_id = int(url.split('/')[-1].split('.')[0])
            return {'id':item_id, 'title':f'新闻{item_id}',
                    'text':'可用正文' if item_id == 2 else ''}
        with patch.object(digest, 'request_json', side_effect=fake_request), \
             patch.object(digest, 'article_excerpt', return_value=('', '仅标题（原文无法访问）')):
            rows = digest.fetch_stories(target=15, batch_size=2)
        self.assertEqual([row['id'] for row in digest.accessible_stories(rows)], [2])

    def test_fetch_can_scan_beyond_first_hundred(self):
        def fake_request(url, *args, **kwargs):
            if url.endswith('topstories.json'):
                return list(range(1, 102))
            item_id = int(url.split('/')[-1].split('.')[0])
            return {'id':item_id, 'title':f'新闻{item_id}',
                    'text':'可用正文' if item_id == 101 else ''}
        with patch.object(digest, 'request_json', side_effect=fake_request), \
             patch.object(digest, 'article_excerpt', return_value=('', '仅标题（原文无法访问）')):
            rows = digest.fetch_stories(target=1, batch_size=10)
        self.assertEqual([row['id'] for row in digest.accessible_stories(rows)], [101])

    def test_fetch_skips_ids_already_cached_or_sent(self):
        requested = []
        def fake_request(url, *args, **kwargs):
            if url.endswith('topstories.json'):
                return [1, 2, 3]
            item_id = int(url.split('/')[-1].split('.')[0])
            requested.append(item_id)
            return {'id':item_id, 'title':f'新闻{item_id}', 'text':'可用正文'}
        with patch.object(digest, 'request_json', side_effect=fake_request):
            rows = digest.fetch_stories(target=1, batch_size=10, exclude_ids={1, 2})
        self.assertEqual([row['id'] for row in rows], [3])
        self.assertEqual(requested, [3])

    def test_backlog_is_consumed_first_and_batch_extras_are_retained(self):
        backlog = self.sample(4)
        fresh = self.sample(13)
        for index, row in enumerate(fresh, 5):
            row.update(id=index, rank=index, title=f'新闻{index}')
        for row in backlog + fresh:
            row.update(excerpt='正文' * 500, basis='可见网页正文')
        selected, remaining = digest.select_daily_stories(backlog, fresh, target=15)
        self.assertEqual([row['id'] for row in selected], list(range(1, 16)))
        self.assertEqual([row['rank'] for row in selected], list(range(1, 16)))
        self.assertEqual([row['id'] for row in remaining], [16, 17])

    def test_inaccessible_external_story_is_skipped(self):
        rows = self.sample(3)
        rows[0].update(excerpt='x' * 1000, basis='可见网页正文')
        rows[1].update(excerpt='x' * 999, basis='正文不足')
        rows[2].update(excerpt='', basis='原文无法访问')
        self.assertEqual([r['id'] for r in digest.accessible_stories(rows)], [1])

    def test_hn_post_body_is_kept_without_external_minimum(self):
        row = self.sample(1)[0]
        row.update(excerpt='短帖正文', basis='HN帖子正文')
        self.assertEqual(digest.accessible_stories([row]), [row])

    def test_batch_validation_failure_retries_without_repeating_completed_batch(self):
        stories = self.sample(18)
        for row in stories:
            row['excerpt'] = 'x' * 1000
        calls = []
        def fake_model(prompt, data):
            calls.append([item['id'] for item in data['items']])
            if calls.count(calls[-1]) == 1 and data['items'][0]['id'] == 9:
                return {'items':data['items'][:-1]}
            return {'items':data['items']}
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'model_json', side_effect=fake_model), \
             patch.object(digest.time, 'sleep'):
            rows = digest.summarize(stories, '', Path(tmp))
        self.assertEqual(len(rows), 18)
        self.assertEqual(calls.count(list(range(1, 9))), 1)
        self.assertEqual(calls.count(list(range(9, 17))), 2)
        self.assertEqual(calls[-1], [17, 18])
        self.assertEqual(len(calls), 4)

    def test_failed_batches_split_and_resume_completed_children(self):
        stories = self.sample(4)
        fail = True
        def model(prompt, data):
            items = data['items']
            if len(items) > 1 or (fail and items[0]['id'] == 3):
                return {'items': [dict(row, id=999) for row in items]}
            return {'items': items}
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'model_json', side_effect=model), patch.object(digest.time, 'sleep'):
            root = Path(tmp)
            with self.assertRaises(ValueError):
                digest.summarize(stories, '', root)
            self.assertTrue((root / 'batch-0-L-L.json').exists())
            self.assertTrue((root / 'batch-0-L-R.json').exists())
            failures = list((root / 'failures').glob('*.json'))
            self.assertTrue(failures)
            event = json.loads(failures[0].read_text())
            self.assertIn('expected_ids', event)
            self.assertIn('actual_ids', event)
            self.assertIn('response', event)
            fail = False
            rows = digest.summarize(stories, '', root)
            self.assertEqual([row['id'] for row in rows], [1, 2, 3, 4])
            with patch.object(digest, 'model_json', side_effect=AssertionError('cache not reused')):
                self.assertEqual(digest.summarize(stories, '', root), rows)

    def test_retry_contains_id_correction_and_keeps_failure_response(self):
        stories = self.sample(2)
        def model(prompt, data):
            if 'retry_feedback' not in data:
                return {'items': [dict(row, id=999) for row in stories]}
            self.assertEqual(data['retry_feedback']['missing_ids'], [1, 2])
            self.assertEqual(data['retry_feedback']['unexpected_ids'], [999])
            return {'items': stories}
        with tempfile.TemporaryDirectory() as tmp, patch.object(digest, 'model_json', side_effect=model), patch.object(digest.time, 'sleep'):
            self.assertEqual(len(digest.summarize(stories, '', Path(tmp))), 2)

    def test_digest_shows_seven_then_toggle_panel(self):
        rows = self.sample(15)
        card = digest.make_cards(rows, '2026-09-09', '', 0)[0]
        elements = card['card']['body']['elements']
        self.assertEqual(len(elements), 2)
        self.assertEqual(elements[0]['tag'], 'markdown')
        self.assertIn('[7. 新闻7]', elements[0]['content'])
        self.assertNotIn('[8. 新闻8]', elements[0]['content'])
        self.assertEqual(elements[1]['tag'], 'collapsible_panel')
        self.assertEqual(elements[1]['header']['title']['content'], '显示更多 / 收起')
        self.assertIn('[8. 新闻8]', elements[1]['elements'][0]['content'])
        self.assertIn('[15. 新闻15]', elements[1]['elements'][0]['content'])

    def test_work_cache_keeps_only_three_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for day in ('2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', 'invalid'):
                (root / day).mkdir()
            digest.prune_work_cache(root, '2026-09-10', keep_days=3)
            self.assertFalse((root / '2026-09-07').exists())
            self.assertTrue((root / '2026-09-08').exists())
            self.assertTrue((root / '2026-09-10').exists())
            self.assertTrue((root / 'invalid').exists())

    def test_signing(self):
        expected = base64.b64encode(hmac.new(b'123\nsecret', b'', hashlib.sha256).digest()).decode()
        self.assertEqual(digest.signature('secret', '123'), expected)

    def test_style_preview_is_fifteen_item_sequential_card(self):
        payload = digest.style_preview_card()
        elements = payload['card']['body']['elements']
        self.assertEqual([element['tag'] for element in elements], ['markdown', 'collapsible_panel'])
        self.assertEqual(elements[1]['header']['title']['content'], '显示更多 / 收起')
        self.assertIn('[15.', json.dumps(payload, ensure_ascii=False))

    def test_feedback_replaces_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'preferences.json'
            digest.save_preferences(path, '多看系统设计')
            digest.save_preferences(path, '少看AI')
            self.assertEqual(json.loads(path.read_text())['text'], '少看AI')
            digest.save_preferences(path, 'CLEAR')
            self.assertEqual(json.loads(path.read_text())['text'], '')

    def test_failed_send_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sent.json'
            cards = [{'n':1}, {'n':2}, {'n':3}]
            seen = []
            def fail_second(card):
                seen.append(card['n'])
                if card['n'] == 2:
                    raise RuntimeError('failed')
            with self.assertRaises(RuntimeError):
                digest.deliver(cards, path, fail_second, pause=0)
            self.assertEqual(json.loads(path.read_text())['sent'], 1)
            digest.deliver(cards, path, lambda c: seen.append(c['n']), pause=0)
            self.assertEqual(seen, [1, 2, 2, 3])

    def test_markup_is_not_executable(self):
        self.assertNotIn('<at', digest.escape('<at id=all>通知</at> [x](https://evil.example)'))

    def test_private_source_rejected(self):
        with self.assertRaises(ValueError):
            digest.check_public_url('http://127.0.0.1/latest/meta-data')
        with self.assertRaises(ValueError):
            digest.check_public_url('file:///etc/passwd')

    def test_preview_then_send_then_repeat_has_no_duplicate_delivery(self):
        sources = self.sample(15)
        for row in sources:
            row['excerpt'] = '测试用来源材料' * 200
        calls = []
        def fake_model(prompt, data):
            calls.append(prompt)
            return {'items':data['items']}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {'LLM_API_KEY':'fixture', 'LLM_BASE_URL':'https://example.com', 'LLM_MODEL':'fixture'}
            sent = []
            with patch.object(digest, 'ROOT', root), patch.object(digest, 'STATE', root/'state'), \
                 patch.object(digest, 'fetch_stories', return_value=sources), \
                 patch.object(digest, 'model_json', side_effect=fake_model), \
                 patch.object(digest, 'send_card', side_effect=lambda c:sent.append(c)), \
                 patch.object(digest.time, 'sleep'), patch.dict('os.environ', env):
                with patch('sys.argv', ['digest.py', 'run']):
                    digest.main()
                self.assertEqual(sent, [])
                self.assertEqual(len(calls), 2)
                with patch('sys.argv', ['digest.py', 'run', '--send']):
                    digest.main()
                    first_count = len(sent)
                    digest.main()
                self.assertEqual(first_count, 1)
                self.assertEqual(len(sent), first_count)
                self.assertEqual(len(calls), 2)
                report = next((root/'state/reports').glob('*/digest.json'))
                self.assertEqual(len(json.loads(report.read_text())), 15)
                self.assertNotIn('excerpt', report.read_text())
                self.assertNotIn('topic', report.read_text())


if __name__ == '__main__':
    unittest.main()
