import unittest

import helpers  # noqa: F401
import fetch_netaful_reviews as nr


class TestNetaful(unittest.TestCase):
    def test_config(self):
        self.assertEqual(nr.TAGS, {"nissin": None, "myojo": None, "sanyo": None, "acecook": None})
        self.assertEqual(nr.TITLE_ALWAYS, {"nissin", "myojo", "sanyo", "acecook"})
        self.assertIn("CupmenPochi", nr.UA)

    def test_title_filter(self):
        self.assertTrue(nr.title_passes_filter("nissin", "【カップヌードル】新作を食べてみた"))
        self.assertFalse(nr.title_passes_filter("nissin", "【セブン】新商品"))
        self.assertTrue(nr.title_passes_filter("sanyo", "サッポロ一番の新作"))

    def test_search_url(self):
        seen = {}

        def fake_get(url):
            seen["url"] = url
            return b"[]", {"X-WP-TotalPages": "1"}
        saved = nr.http_get
        nr.http_get = fake_get
        try:
            nr.fetch_tag_page(None, 1, 50, "日清")
        finally:
            nr.http_get = saved
        self.assertIn("orderby=date", seen["url"])
        self.assertNotIn("tags=", seen["url"])

    def test_names_match_and_effective_date(self):
        n = nr.normalize_name
        self.assertTrue(nr.names_match(n("カップヌードル 辛豚"), n("カップヌードル 辛豚")))
        self.assertFalse(nr.names_match(n("ラーメン"), n("横浜家系ラーメン")))
        self.assertEqual(nr.product_effective_date({"launch_date": "2026-10-01", "first_seen_at": "2026-11-05T07:00"}), "2026-10-01")
        self.assertEqual(nr.product_effective_date({"launch_month": "2026-11", "first_seen_at": "2026-11-05T07:00"}), "2026-11-01")


if __name__ == "__main__":
    unittest.main()
