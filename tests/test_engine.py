import json
import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain
import crawl
import synth as s

DAY1 = "2026-11-05"
DAY2 = "2026-11-06"


def nissin_site(groups, details):
    site = FakeSite({s.NISSIN_LIST: s.nissin_list(groups)})
    for pid, html in details.items():
        site.set(s.nissin_url(pid), html)
    return site


class TestNissin(unittest.TestCase):
    def test_records_cup_only_and_remembers_skips(self):
        with TempData():
            groups = [(11, 9, [("100", "カップA")]), (11, 2, [("101", "菓子B"), ("102", "袋麺 5食パック")])]
            site = nissin_site(groups, {
                "100": s.nissin_detail(name="カップA"),
                "101": s.nissin_detail(name="菓子B", water=None),
                "102": s.nissin_detail(name="袋麺 5食パック", packing="1パック5食入"),
            })
            _, stats = run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertEqual(stats["nissin"]["new_products"], 1)
            self.assertEqual(stats["nissin"]["skipped"], 2)
            p = prod("nissin", "100")
            self.assertEqual(p["launch_date"], "2026-11-09")
            self.assertEqual(p["price_excl_tax"], 259)
            self.assertIsNone(p["launch_month"])
            skipped = json.loads(crawl.SKIPPED_PATH.read_text(encoding="utf-8"))["nissin"]
            self.assertEqual(set(skipped), {"101", "102"})
            # 2回目: 詳細は1件も取り直さない(一覧とrobotsだけ)
            site2 = nissin_site(groups, {})
            run_chain(crawl.crawl_nissin, site2, DAY2)
            self.assertEqual(site2.count("/items/"), 0)

    def test_before_record_from_not_recorded(self):
        with TempData():
            groups = [(11, 9, [("100", "新")]), (7, 20, [("90", "古い")])]
            site = nissin_site(groups, {"100": s.nissin_detail(), "90": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertIsNotNone(prod("nissin", "100"))
            self.assertIsNone(prod("nissin", "90"))
            self.assertEqual(site.count("/items/90/"), 0)  # 取りにも行かない

    def test_upcoming_release_is_recorded(self):
        with TempData():
            site = nissin_site([(11, 9, [("100", "これから")])], {"100": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertEqual(prod("nissin", "100")["launch_date"], "2026-11-09")

    def test_year_wrap_in_crawl(self):
        with TempData():
            site = nissin_site([(1, 12, [("100", "来年")]), (12, 28, [("101", "今年")])],
                               {"100": s.nissin_detail(), "101": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, "2026-12-30")
            self.assertEqual(prod("nissin", "100")["launch_date"], "2027-01-12")
            self.assertEqual(prod("nissin", "101")["launch_date"], "2026-12-28")

    def test_no_end_tracking_when_item_leaves_list(self):
        with TempData():
            groups = [(11, 9, [("100", "カップA"), ("101", "カップB")])]
            site = nissin_site(groups, {"100": s.nissin_detail(), "101": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, DAY1)
            before = json.dumps(prod("nissin", "101"), sort_keys=True)
            for i, day in enumerate(("2026-11-06", "2026-11-07", "2026-11-08")):
                site2 = nissin_site([(11, 9, [("100", "カップA")])], {})
                _, stats = run_chain(crawl.crawl_nissin, site2, day)
            p = prod("nissin", "101")
            self.assertEqual(json.dumps(p, sort_keys=True), before)  # 一覧から消えても何も変わらない
            for forbidden in ("status", "ended_observed_at", "missed_runs", "seasons", "relaunches"):
                self.assertNotIn(forbidden, p)
            self.assertNotIn("ended", stats["nissin"])

    def test_discontinued_flag_on_new_fetch_and_recheck(self):
        with TempData():
            site = nissin_site([(11, 9, [("100", "終了品")])], {"100": s.nissin_detail(discontinued=True)})
            run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertTrue(prod("nissin", "100")["discontinued"])
        with TempData():
            site = nissin_site([(11, 9, [("100", "カップA")])], {"100": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertFalse(prod("nissin", "100")["discontinued"])
            # 29日後はまだ取り直さない / 30日後に取り直して生産終了を拾う
            site29 = nissin_site([(11, 9, [("100", "カップA")])], {"100": s.nissin_detail(discontinued=True)})
            run_chain(crawl.crawl_nissin, site29, "2026-12-04")
            self.assertEqual(site29.count("/items/100/"), 0)
            site30 = nissin_site([(11, 9, [("100", "カップA")])], {"100": s.nissin_detail(discontinued=True)})
            run_chain(crawl.crawl_nissin, site30, "2026-12-05")
            self.assertEqual(site30.count("/items/100/"), 1)
            self.assertTrue(prod("nissin", "100")["discontinued"])

    def test_blocked_stops_without_workaround(self):
        with TempData():
            site = nissin_site([(11, 9, [("100", "カップA")])], {})
            site.blocked_hosts.add("/items/")
            _, stats = run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertTrue(stats["nissin"]["blocked"])
            self.assertIsNone(prod("nissin", "100"))

    def test_user_agent_is_honest(self):
        self.assertEqual(crawl.UA, "CupmenPochi/1.0 (+https://cupmen.netaful.jp/)")
        self.assertEqual(crawl.MAX_REQUESTS["nissin"], 50)
        self.assertEqual(crawl.INTERVALS["acecook"], 2)


class TestMyojo(unittest.TestCase):
    def site(self, items, page2=None):
        site = FakeSite({s.MYOJO_LIST_P1: s.myojo_list(items)})
        if page2 is not None:
            site.set(s.MYOJO_LIST_P2, s.myojo_list(page2))
        for it in items + (page2 or []):
            site.set(s.myojo_detail_url(it["id"]), s.myojo_detail(it["id"]))
        return site

    def test_company_param_and_filters(self):
        with TempData():
            items = [
                s.myojo_item(1, "明星 新カップ", "20261012"),
                s.myojo_item(2, "明星 新焼そば", "20261019", sub="焼そば"),
                s.myojo_item(3, "明星 袋麺", "20261012", sub="袋麺"),
                s.myojo_item(4, "明星 リニューアル品", "20080401", renew="20260914"),
                s.myojo_item(5, "明星 古いカップ", "20260701"),
            ]
            site = self.site(items)
            run_chain(crawl.crawl_myojo, site, DAY1)
            self.assertTrue(all("company=myojofoods" in u for u in site.calls if "/list" in u))
            self.assertEqual(sorted(p["product_id"] for p in crawl.load_chain_products("myojo")), ["1", "2"])
            self.assertEqual(prod("myojo", "1")["price_excl_tax"], 252)
            self.assertEqual(prod("myojo", "1")["sales_area"], "全国")

    def test_pagination_when_first_page_is_full(self):
        with TempData():
            page1 = [s.myojo_item(1000 + i, f"明星 古い{i}", "19900101") for i in range(100)]
            page2 = [s.myojo_item(2, "明星 二ページ目の新商品", "20261026")]
            site = self.site(page1, page2)
            run_chain(crawl.crawl_myojo, site, DAY1)
            self.assertEqual(site.count("&page=2"), 1)
            self.assertIsNotNone(prod("myojo", "2"))

    def test_wrong_company_response_is_not_recorded(self):
        with TempData():
            items = [s.myojo_item(i, f"日清 商品{i}", "20261012") for i in range(1, 6)]
            site = self.site(items)
            _, stats = run_chain(crawl.crawl_myojo, site, DAY1)
            self.assertFalse(stats["myojo"]["list_ok"])
            self.assertEqual(crawl.load_chain_products("myojo"), [])


class TestSanyo(unittest.TestCase):
    def test_month_only_and_filters(self):
        with TempData():
            site = FakeSite({
                s.SANYO_SITEMAP: s.sanyo_sitemap(["1", "2", "3", "4"]),
                s.sanyo_url("1"): s.sanyo_detail(name="テスト カップ 塩", month="2026年11月"),
                s.sanyo_url("2"): s.sanyo_detail(name="テスト みそ ５食パック", month="2026年11月"),
                s.sanyo_url("3"): s.sanyo_detail(name="テスト カップ 古い", month="2026年7月"),
                s.sanyo_url("4"): s.sanyo_detail(name="テスト カップ 月なし", month=None),
            })
            run_chain(crawl.crawl_sanyo, site, DAY1)
            p = prod("sanyo", "1")
            self.assertEqual(p["launch_month"], "2026-11")
            self.assertIsNone(p["launch_date"])
            self.assertEqual(p["price_excl_tax"], 271)
            self.assertEqual([x["product_id"] for x in crawl.load_chain_products("sanyo")], ["1"])
            skipped = json.loads(crawl.SKIPPED_PATH.read_text(encoding="utf-8"))["sanyo"]
            self.assertEqual({k: v["reason"] for k, v in skipped.items()},
                             {"2": "not_cup", "3": "before_record_from", "4": "no_launch_month"})
            self.assertEqual(skipped["2"]["name"], "テスト みそ ５食パック")  # 除外した名前を残す

    def test_request_cap_defers_rest_to_next_run(self):
        with TempData():
            ids = [str(i) for i in range(10, 20)]
            routes = {s.SANYO_SITEMAP: s.sanyo_sitemap(ids)}
            for i in ids:
                routes[s.sanyo_url(i)] = s.sanyo_detail(name=f"テスト カップ{i}")
            site = FakeSite(routes, max_requests=2 + 4)  # robots + sitemap + 詳細4件
            _, stats = run_chain(crawl.crawl_sanyo, site, DAY1)
            self.assertEqual(stats["sanyo"]["new_products"], 4)
            self.assertEqual(stats["sanyo"]["deferred"], 6)
            self.assertFalse(stats["sanyo"]["blocked"])
            # IDの大きい(新しい)順に取る
            self.assertEqual(sorted(p["product_id"] for p in crawl.load_chain_products("sanyo")), ["16", "17", "18", "19"])
            site2 = FakeSite(routes)
            run_chain(crawl.crawl_sanyo, site2, DAY2)
            self.assertEqual(len(crawl.load_chain_products("sanyo")), 10)
            self.assertEqual(site2.count("/products/"), 6)  # 未取得の6件だけ


class TestAcecook(unittest.TestCase):
    def test_records_and_allergens(self):
        with TempData():
            site = FakeSite({
                s.ACECOOK_LIST: s.acecook_list([
                    ("003458", "テスト 中華そば", "2026年11月02日", "255円（税抜）"),
                    ("003459", "テスト（袋）", "2026年11月02日", "255円（税抜）"),
                    ("003400", "古いカップ", "2026年07月20日", "255円（税抜）"),
                ]),
                s.acecook_url("003458"): s.acecook_detail(name="テスト 中華そば", date="2026年11月 2日"),
                s.acecook_url("003459"): s.acecook_detail(name="テスト（袋）"),
            })
            run_chain(crawl.crawl_acecook, site, DAY1)
            p = prod("acecook", "003458")
            self.assertEqual(p["launch_date"], "2026-11-02")
            self.assertEqual(p["allergens"], {"contains": ["小麦", "乳成分"]})
            self.assertIsNone(prod("acecook", "003459"))
            self.assertEqual(site.count("003400"), 0)


class TestGuards(unittest.TestCase):
    def test_burst_guard_holds_new_when_baseline_exists(self):
        with TempData():
            groups = [(11, 9, [("1", "A")])]
            site = nissin_site(groups, {"1": s.nissin_detail()})
            run_chain(crawl.crawl_nissin, site, DAY1)  # 初回: baseline を作る
            many = [(str(100 + i), f"新{i}") for i in range(60)]
            site2 = nissin_site([(11, 9, [("1", "A")] + many)], {pid: s.nissin_detail() for pid, _ in many})
            pending, stats = run_chain(crawl.crawl_nissin, site2, DAY2)
            self.assertEqual(stats["nissin"]["new_products"], 0)
            self.assertIn("suspected_restructure", stats["nissin"])
            self.assertEqual(site2.count("/items/"), 0)

    def test_first_run_is_exempt_from_burst_guard(self):
        with TempData():
            many = [(str(100 + i), f"新{i}") for i in range(30)]
            site = nissin_site([(11, 9, many)], {pid: s.nissin_detail() for pid, _ in many})
            _, stats = run_chain(crawl.crawl_nissin, site, DAY1)
            self.assertEqual(stats["nissin"]["new_products"], 30)

    def test_list_drop_holds_new_products(self):
        with TempData():
            crawl.LIST_DROP_MIN_PREV["nissin"] = 15
            items = [(str(i), f"カップ{i}") for i in range(1, 21)]
            site = nissin_site([(11, 2, items)], {pid: s.nissin_detail() for pid, _ in items})
            run_chain(crawl.crawl_nissin, site, DAY1)
            # 翌日、一覧が急に3件になり、新規が1件ある → 3日続くまで記録しない
            site2 = nissin_site([(11, 9, [("500", "新"), ("1", "a"), ("2", "b")])], {"500": s.nissin_detail()})
            _, stats = run_chain(crawl.crawl_nissin, site2, DAY2)
            self.assertFalse(stats["nissin"]["list_ok"])
            self.assertIsNone(prod("nissin", "500"))


class TestSummary(unittest.TestCase):
    def test_summary_has_no_ended(self):
        with TempData():
            stats = {"nissin": {**crawl.new_stats(), "list_ok": True, "new_products": 2}}
            crawl.save_run_summary(stats, {})
            data = json.loads(crawl.RUN_SUMMARY.read_text(encoding="utf-8"))
            self.assertEqual(data["new"], {"nissin": 2})
            self.assertNotIn("ended", data)


if __name__ == "__main__":
    unittest.main()
