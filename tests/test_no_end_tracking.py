"""「終了は追わない」方針の確認: 終了判定・再登場・seasons の仕組みがコードに残っていないこと。"""
import re
import unittest
from pathlib import Path

import helpers  # noqa: F401

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FORBIDDEN = ("ended_observed_at", "missed_runs", "last_miss_date", "MISSES_TO_END", "apply_missing",
             "start_new_season", "relaunches", "seasons", "ended_basis", "status-filter", "data-status")


class TestNoEndTracking(unittest.TestCase):
    def test_no_forbidden_identifiers(self):
        for f in SCRIPTS.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            for word in FORBIDDEN:
                code_lines = [ln for ln in text.splitlines() if word in ln and not ln.lstrip().startswith("#")]
                self.assertEqual(code_lines, [], f"{f.name}: {word}")

    def test_no_forbidden_sites(self):
        for f in list(SCRIPTS.glob("*.py")) + [SCRIPTS.parent / "config.json"]:
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("maruchan", text)
            self.assertNotIn("movabletype", text)

    def test_only_discontinued_flag_mentions_end_of_production(self):
        text = (SCRIPTS / "generate_site.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"生産終了", text)), 1)


if __name__ == "__main__":
    unittest.main()
