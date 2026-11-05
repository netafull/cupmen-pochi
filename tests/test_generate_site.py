import re
import unittest

import helpers  # noqa: F401
import generate_site as g


def item(**kw):
    base = {
        "chain": "nissin", "product_id": "t9001", "name": "テストヌードル",
        "price_excl_tax": 259, "price_label": None,
        "launch_date": "2026-11-09", "launch_month": None,
        "net_amount": "76g（60g）", "net_label": "麺量", "sales_area": "全国", "ec_only": False, "jan": "4900000000001",
        "nutrition": {"kcal": 353.0, "protein_g": 8.0, "fat_g": 15.0, "carbs_g": 46.4, "salt_g": 4.3,
                      "other": {"カルシウム": "134mg"}},
        "nutrition_label": "1食（76g）当たり",
        "allergens": {"contains": ["小麦", "卵"]},
        "discontinued": False,
        "official_url": "https://www.nissin.com/jp/product/items/t9001/",
        "first_seen_at": "2026-11-05T07:00:00+09:00",
    }
    base.update(kw)
    return base


def body_of(html: str) -> str:
    return html[html.index("<main>"):html.index("</main>")]


class TestPriceAndDates(unittest.TestCase):
    def test_price_is_excl_tax(self):
        self.assertEqual(g.fmt_price(item()), "259円（税別）")  # 税込に換算しない
        self.assertEqual(g.fmt_price(item(price_excl_tax=1280)), "1,280円（税別）")
        self.assertEqual(g.fmt_price(item(price_excl_tax=None, price_label="オープンプライス")), "オープンプライス")
        self.assertEqual(g.fmt_price(item(price_excl_tax=None)), "")

    def test_launch_text(self):
        self.assertEqual(g.launch_text(item()), "2026年11月9日")
        self.assertEqual(g.launch_text(item(launch_date=None, launch_month="2026-11")), "2026年11月")

    def test_month_only_sorts_as_first_of_month(self):
        self.assertEqual(g.sort_date(item(launch_date=None, launch_month="2026-11")), "2026-11-01")
        self.assertEqual(g.start_month(item(launch_date=None, launch_month="2026-11")), "2026-11")

    def test_net_amount_label(self):
        self.assertEqual(g.fmt_net(item()), "76g（麺量 60g）")
        self.assertEqual(g.fmt_net(item(net_amount="92g（めん 78g）", net_label=None)), "92g（めん 78g）")


class TestTopPage(unittest.TestCase):
    def items(self):
        return [
            item(product_id="a", name="古い品", launch_date="2026-10-05"),
            item(product_id="b", name="未来の品", launch_date="2026-11-20"),
            item(product_id="c", chain="sanyo", name="月だけの品", launch_date=None, launch_month="2026-11"),
            item(product_id="d", name="今月の品", launch_date="2026-11-09"),
        ]

    def test_single_flat_list_newest_first_future_at_top(self):
        h = g.render_top(self.items())
        names = re.findall(r'<div class="t">(.*?)</div>', h.split('id="search-results"')[1])
        self.assertEqual(names, ["未来の品", "今月の品", "月だけの品", "古い品"])
        # 見出しで分けない: 一覧の見出し(h2)は1つ、h3による区分も無い
        main = body_of(h)
        self.assertEqual(len(re.findall(r'class="grid"[^>]*data-sortable', main)), 1)
        self.assertNotIn("<h3>", main.split("カップ麺の新商品")[1].split("ネタフル")[0])
        for word in ("これから発売", "発売済み", "発売前", "販売中", "終了"):
            self.assertNotIn(word, main)

    def test_sanyo_card_shows_month(self):
        h = g.render_top(self.items())
        self.assertIn("2026年11月発売", h)
        self.assertIn("2026年11月9日発売", h)

    def test_tie_between_month_and_first_of_month(self):
        items = [item(product_id="x", name="X", launch_date="2026-11-01"),
                 item(product_id="y", chain="sanyo", name="Y", launch_date=None, launch_month="2026-11")]
        flat = g.render_flat(items)
        self.assertEqual(len(re.findall(r'class="item"', flat)), 2)


class TestItemPage(unittest.TestCase):
    def test_section_order(self):
        h = body_of(g.render_item_page(item()))
        pos = [h.index(x) for x in ("259円（税別）", "発売日", "発売地域", "内容量", "JANコード",
                                      "栄養成分", "アレルゲン", "補足と出典")]
        self.assertEqual(pos, sorted(pos))
        self.assertLess(h.index("<h1>"), h.index("259円（税別）"))

    def test_facts_follow_name_and_no_images(self):
        full = g.render_item_page(item())
        h = body_of(full)
        # 公式の画像は載せない(ネタフル自身のレビュー記事のサムネイルだけが出うる)
        for src in re.findall(r'<img[^>]*src="([^"]*)"', h):
            self.assertTrue(src.startswith("https://netaful.jp/"), src)
        self.assertIn("76g（麺量 60g）", h)
        self.assertIn("4900000000001", h)

    def test_discontinued_tag_only_when_flagged(self):
        self.assertNotIn("生産終了", g.render_item_page(item()))
        self.assertIn("生産終了", g.render_item_page(item(discontinued=True)))

    def test_allergens_unknown_links_to_official(self):
        h = g.render_item_page(item(allergens=None))
        self.assertIn("アレルゲンは公式サイトでご確認ください", h)

    def test_sanyo_month_note(self):
        h = g.render_item_page(item(chain="sanyo", launch_date=None, launch_month="2026-11"))
        self.assertIn("2026年11月", h)
        self.assertIn("月で表示しています", h)

    def test_no_ended_wording_or_status(self):
        h = g.render_item_page(item())
        for word in ("販売終了", "終了日", "再登場", "販売中", "昨季", "data-status"):
            self.assertNotIn(word, h)

    def test_seo(self):
        self.assertEqual(g.seo_title(item()), "日清食品「テストヌードル」の発売日・カロリー・価格")
        d = g.seo_description(item())
        self.assertIn("2026年11月9日発売", d)
        self.assertIn("259円（税別）", d)


class TestSiteWide(unittest.TestCase):
    def test_footer_and_shell(self):
        h = g.page_shell("x", "d", "<p>b</p>", "https://cupmen.netaful.jp/")
        self.assertIn("希望小売価格（税別）", h)
        self.assertIn("発売月別アーカイブ", h)
        self.assertNotIn("終了した", h)
        self.assertIn("回転寿司ポチ", h)  # 関連サイト(フッター)

    def test_config(self):
        c = g.CONFIG
        self.assertEqual(c["site_url"], "https://cupmen.netaful.jp/")
        self.assertEqual(c["ga_measurement_id"], "")
        self.assertEqual(c["adsense_ad_slot"], "1215675017")
        self.assertEqual([x["slug"] for x in c["chains"]], ["nissin", "myojo", "sanyo", "acecook"])
        self.assertNotIn("toyo", " ".join(x["slug"] for x in c["chains"]))

    def test_search_index_and_rss(self):
        items = [item(), item(product_id="t9002", name="別の品", launch_date="2026-10-01")]
        idx = g.build_search_index(items)
        self.assertEqual(idx[0]["n"], "テストヌードル")
        self.assertEqual(idx[0]["d"], "2026年11月9日発売")
        self.assertIn("<item>", g.generate_rss(items))
        self.assertIn("companies/nissin/", g.generate_sitemap(items, ["2026-11"]))

    def test_filter_bar_has_sort_and_company_only(self):
        f = g.filter_bar()
        self.assertIn("カロリー順", f)
        self.assertIn("日清食品", f)
        self.assertNotIn("status-filter", f)


if __name__ == "__main__":
    unittest.main()
