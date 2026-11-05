import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import notify


def _summary(**kw):
    base = {"new": {}, "suspected": {}, "blocked": [], "list_failed": [], "errors": [],
            "streaks": {}, "detail_failed": {}}
    base.update(kw)
    return base


class TestNotify(unittest.TestCase):
    def test_no_news_no_message(self):
        self.assertIsNone(notify.build_message(_summary(new={"nissin": 0, "myojo": 0})))

    def test_new(self):
        title, body = notify.build_message(_summary(new={"nissin": 2, "myojo": 1}))
        self.assertEqual(title, "カップ麺ポチ: 新商品 3件")
        self.assertIn("日清食品 2件", body)
        self.assertIn("明星食品 1件", body)
        self.assertNotIn("終了", title + body)

    def test_run_summary_without_ended_key(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "run_summary.json"
            p.write_text(json.dumps({"new": {"sanyo": 4}}), encoding="utf-8")
            s = notify.read_run_summary(p)
            self.assertEqual(s["new"], {"sanyo": 4})
            self.assertNotIn("ended", s)
            self.assertEqual(notify.read_run_summary(Path(t) / "none.json")["new"], {})

    def test_problem_and_hold(self):
        s = _summary(list_failed=["acecook"], streaks={"acecook": 3},
                     suspected={"nissin": {"count": 50, "threshold": 40, "consecutive_days": 3}})
        title, body = notify.build_message(s)
        self.assertIn("エースコック: 一覧の取得に失敗(3日連続)", body)
        self.assertIn("保留あり", title)
        warn = notify.build_warnings(s)
        self.assertIn("要判断", warn[0][0])

    def test_no_topic_sends_nothing(self):
        import os
        old = os.environ.pop("NTFY_TOPIC", None)
        try:
            saved = sys_argv = None
            import sys
            saved = sys.argv
            sys.argv = ["notify.py", "--summary"]
            self.assertEqual(notify.main(), 0)
        finally:
            sys.argv = saved
            if old is not None:
                os.environ["NTFY_TOPIC"] = old


if __name__ == "__main__":
    unittest.main()
