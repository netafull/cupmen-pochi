import datetime
import unittest

import helpers  # noqa: F401
import parsers as p
import synth as s


class TestNissinYear(unittest.TestCase):
    def test_first_heading_is_closest_to_today(self):
        today = datetime.date(2026, 11, 5)
        got = p.infer_nissin_dates([(11, 9), (11, 2), (10, 26), (10, 6)], today)
        self.assertEqual([d.isoformat() for d in got], ["2026-11-09", "2026-11-02", "2026-10-26", "2026-10-06"])

    def test_year_wraps_backwards(self):
        # 年が明けた直後: 1/12, 1/5, 12/28, 12/21 → 12月は前の年
        today = datetime.date(2027, 1, 6)
        got = p.infer_nissin_dates([(1, 12), (1, 5), (12, 28), (12, 21)], today)
        self.assertEqual([d.isoformat() for d in got], ["2027-01-12", "2027-01-05", "2026-12-28", "2026-12-21"])

    def test_upcoming_across_year_end(self):
        # 12月末の時点で、来年1月発売の予定が先頭に出る
        today = datetime.date(2026, 12, 28)
        got = p.infer_nissin_dates([(1, 12), (12, 21), (12, 14)], today)
        self.assertEqual([d.isoformat() for d in got], ["2027-01-12", "2026-12-21", "2026-12-14"])

    def test_list_parse(self):
        html = s.nissin_list([(11, 9, [("14002", "カップ A &amp; B")]), (11, 2, [("14009", "どん兵衛 東"), ("14008", "どん兵衛 西")])])
        items = p.parse_nissin_list(html, datetime.date(2026, 11, 5))
        self.assertEqual([(i["id"], i["launch_date"]) for i in items],
                         [("14002", "2026-11-09"), ("14009", "2026-11-02"), ("14008", "2026-11-02")])
        self.assertEqual(items[0]["name"], "カップ A & B")


class TestNissinDetail(unittest.TestCase):
    def test_fields(self):
        d = p.parse_nissin_detail(s.nissin_detail())
        self.assertEqual(d["price_excl_tax"], 259)  # 税別のまま。税込に換算しない
        self.assertEqual(d["net_amount"], "76g（60g）")
        self.assertEqual(d["net_label"], "麺量")
        self.assertEqual(d["sales_area"], "全国")
        self.assertEqual(d["jan"], "4900000000001")
        self.assertEqual(d["nutrition"]["kcal"], 353)
        self.assertEqual(d["nutrition"]["salt_g"], 4.3)  # 内訳(めん・かやく)の行は主値に混ざらない
        self.assertEqual(d["nutrition_label"], "1食（76g）当たり")
        self.assertEqual(d["allergens"], ["小麦", "卵"])
        self.assertFalse(d["discontinued"])

    def test_discontinued(self):
        d = p.parse_nissin_detail(s.nissin_detail(discontinued=True))
        self.assertTrue(d["discontinued"])

    def test_no_forbidden_keys(self):
        d = p.parse_nissin_detail(s.nissin_detail())
        for k in d:
            self.assertNotIn("image", k)
            self.assertNotIn("description", k)
            self.assertNotIn("ingredient", k)


class TestCupJudgement(unittest.TestCase):
    def cup(self, **kw):
        return p.nissin_is_cup(p.parse_nissin_detail(s.nissin_detail(**kw)))

    def test_nissin(self):
        self.assertTrue(self.cup())
        self.assertFalse(self.cup(water=None))  # 菓子・冷凍などはお湯の量が無い
        self.assertFalse(self.cup(name="テスト 5食パック", packing="1ケース6パック (1パック5食入)×3合わせ"))
        self.assertFalse(self.cup(name="完全メシ テスト丼", net_label="ライス量"))
        self.assertFalse(self.cup(name="テスト スープ"))
        # コラボ先の名前に「ごはん」が入るカップ麺は除かない
        self.assertTrue(self.cup(name="カップヌードル テスト&ごはんですよ!"))

    def test_myojo_subcategory(self):
        for sub, ok in (("カップ麺", True), ("カップ麺 (ビッグ)", True), ("焼そば", True), ("焼そば (ビッグ)", True),
                        ("袋麺", False), ("スープ", False)):
            e = p.parse_myojo_list(s.myojo_list([s.myojo_item(1, "x", "20261001", sub)]))[0]
            self.assertEqual(p.myojo_is_cup(e), ok, sub)

    def test_sanyo_by_name(self):
        self.assertTrue(p.sanyo_is_cup("サッポロ一番 カップスター 塩"))
        self.assertTrue(p.sanyo_is_cup("サッポロ一番 みそラーメンどんぶり"))
        self.assertTrue(p.sanyo_is_cup("サッポロ一番 名店 タテビッグ"))
        self.assertTrue(p.sanyo_is_cup("サッポロ一番 ミニカップ"))
        self.assertFalse(p.sanyo_is_cup("サッポロ一番 みそラーメン ５食パック"))
        self.assertFalse(p.sanyo_is_cup("サッポロ一番 袋タイプ"))
        # 語が無いときは荷姿(12食入りでパックでない)で判定
        self.assertTrue(p.sanyo_is_cup("サッポロ一番 ぐんまちゃん 醤油らーめん", "1ケース　12食入り"))
        self.assertFalse(p.sanyo_is_cup("サッポロ一番 ぐんまちゃん 醤油らーめん", "1ケース　6パック"))
        self.assertFalse(p.sanyo_is_cup("サッポロ一番 焼そば", None))

    def test_acecook(self):
        self.assertTrue(p.acecook_is_cup("スーパーカップ 1.5倍", "510ml"))
        self.assertFalse(p.acecook_is_cup("テスト（袋）", "510ml"))
        self.assertFalse(p.acecook_is_cup("テスト ５食パック", "510ml"))
        self.assertFalse(p.acecook_is_cup("テスト", None))
        self.assertFalse(p.acecook_is_cup("テストスープ", "200ml"))


class TestMyojo(unittest.TestCase):
    def test_list_url_has_company(self):
        self.assertIn("company=myojofoods", p.MYOJO_LIST_URL)

    def test_list_parse_price(self):
        items = p.parse_myojo_list(s.myojo_list([
            s.myojo_item(1, "a", "20261012"), s.myojo_item(2, "b", "20080401", renew="20260914", price="オープンプライス")]))
        self.assertEqual(items[0]["price_excl_tax"], 252)
        self.assertEqual(items[0]["launch_date"], "2026-10-12")
        self.assertIsNone(items[1]["price_excl_tax"])
        self.assertEqual(items[1]["price_label"], "オープンプライス")
        self.assertEqual(items[1]["renew_date"], "2026-09-14")

    def test_detail(self):
        d = p.parse_myojo_detail(s.myojo_detail(5, area="関東", ec_only=True))
        self.assertEqual(d["sales_area"], "関東")
        self.assertTrue(d["ec_only"])
        self.assertEqual(d["nutrition"]["kcal"], 504)
        self.assertEqual(d["allergens"], ["小麦", "乳成分"])

    def test_bad_structure(self):
        with self.assertRaises(ValueError):
            p.parse_myojo_list('{"items": []}')


class TestSanyoAcecook(unittest.TestCase):
    def test_sanyo_month_only(self):
        d = p.parse_sanyo_detail(s.sanyo_detail())
        self.assertEqual(d["launch_month"], "2026-11")
        self.assertNotIn("launch_date", d)
        self.assertEqual(d["price_excl_tax"], 271)
        self.assertEqual(d["net_label"], "めん重量")
        self.assertEqual(d["nutrition"]["salt_g"], 6.8)
        self.assertEqual(d["allergens"], ["小麦", "卵"])

    def test_sanyo_sitemap(self):
        got = p.parse_sanyo_sitemap(s.sanyo_sitemap(["11580", "9134"]))
        self.assertEqual([g["id"] for g in got], ["11580", "9134"])

    def test_acecook_list_and_detail(self):
        items = p.parse_acecook_list(s.acecook_list([("003432", "テスト　ワンタン", "2026年10月26日", "298円（税抜）")]))
        self.assertEqual(items[0]["launch_date"], "2026-10-26")
        self.assertEqual(items[0]["price_excl_tax"], 298)
        d = p.parse_acecook_detail(s.acecook_detail(date="2026年11月 2日"))
        self.assertEqual(d["launch_date"], "2026-11-02")
        self.assertEqual(d["net_amount"], "92g（めん 78g）")
        self.assertEqual(d["water"], "510ml")
        self.assertEqual(d["nutrition"]["salt_g"], 6.2)
        self.assertEqual(d["allergens"], ["小麦", "乳成分"])

    def test_acecook_allergens_unknown_when_no_color(self):
        d = p.parse_acecook_detail(s.acecook_detail(colored=()))
        self.assertIsNone(d["allergens"])  # 色塗りが読めないときは「なし」と推測しない


class TestPrice(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(p.parse_price_excl_tax("259円 (税別)"), 259)
        self.assertEqual(p.parse_price_excl_tax("255円（税抜）"), 255)
        self.assertEqual(p.parse_price_excl_tax("1,280円"), 1280)
        self.assertIsNone(p.parse_price_excl_tax("オープンプライス"))
        self.assertEqual(p.price_label_of("ノンプリントプライス"), "ノンプリントプライス")
        self.assertIsNone(p.price_label_of("259円"))


if __name__ == "__main__":
    unittest.main()
