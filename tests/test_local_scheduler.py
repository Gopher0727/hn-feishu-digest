from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

import local_scheduler


CST = ZoneInfo('Asia/Shanghai')


class LocalSchedulerTests(unittest.TestCase):
    def test_next_slot_is_same_day_when_time_remains(self):
        now = datetime(2026, 9, 11, 10, 30, tzinfo=CST)
        self.assertEqual(local_scheduler.next_slot(now), datetime(2026, 9, 11, 11, 27, tzinfo=CST))

    def test_next_slot_rolls_to_tomorrow_after_last_run(self):
        now = datetime(2026, 9, 11, 12, 28, tzinfo=CST)
        self.assertEqual(local_scheduler.next_slot(now), datetime(2026, 9, 12, 9, 27, tzinfo=CST))

    def test_exact_slot_advances_to_avoid_duplicate_loop(self):
        now = datetime(2026, 9, 11, 9, 27, tzinfo=CST)
        self.assertEqual(local_scheduler.next_slot(now), datetime(2026, 9, 11, 10, 27, tzinfo=CST))


class EnvFileTests(unittest.TestCase):
    def test_load_env_file_supports_comments_quotes_and_equals(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env.local'
            path.write_text("# local secrets\nA=plain\nB='quoted value'\nC=has=equals\n", encoding='utf-8')
            self.assertEqual(local_scheduler.load_env_file(path), {
                'A': 'plain', 'B': 'quoted value', 'C': 'has=equals'
            })

    def test_missing_required_configuration_is_reported_by_name(self):
        with self.assertRaisesRegex(RuntimeError, 'LLM_API_KEY'):
            local_scheduler.require_configuration({'FEISHU_WEBHOOK': 'x', 'FEISHU_SIGN_SECRET': 'y'})


if __name__ == '__main__':
    unittest.main()
