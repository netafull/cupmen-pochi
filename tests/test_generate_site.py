import json
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
        self.assertTrue(c["ga_measurement_id"].startswith("G-"))
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


def plain_text(html: str) -> str:
    """タグを空白に置き換えて空白を詰めた、画面に出る文字(検索エンジンが読む文字に近い形)。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def twins():
    """同じ会社・同じ名前で、発売日だけ違う2件。"""
    return [
        item(product_id="s1", name="ツイン麺", launch_date="2026-09-01"),
        item(product_id="s2", name="ツイン　麺", launch_date="2026-11-09"),  # 全角空白でも同名として扱う
    ]


class TestSameName(unittest.TestCase):
    def test_note_by_launch_date_then_net_then_price(self):
        a, b = twins()
        self.assertEqual(g.disambiguation_note(a, [a, b]), "2026年9月1日発売")
        self.assertEqual(g.disambiguation_note(b, [a, b]), "2026年11月9日発売")
        c = item(product_id="c1", name="同日麺", net_amount="80g", price_excl_tax=200)
        d = item(product_id="c2", name="同日麺", net_amount="100g", price_excl_tax=200)
        self.assertEqual(g.disambiguation_note(c, [c, d]), "80g")
        e = item(product_id="e1", name="同日麺", price_excl_tax=200)
        f = item(product_id="e2", name="同日麺", price_excl_tax=250)
        self.assertEqual(g.disambiguation_note(f, [e, f]), "250円（税別）")

    def test_note_empty_when_indistinguishable(self):
        e = item(product_id="e1", name="同日麺")
        f = item(product_id="e2", name="同日麺")
        self.assertEqual(g.disambiguation_note(e, [e, f]), "")
        self.assertEqual(g.disambiguation_note(e, [e]), "")

    def test_note_uses_combination_when_single_part_is_not_enough(self):
        a = item(product_id="a", name="組麺", launch_date="2026-09-01", net_amount="80g")
        b = item(product_id="b", name="組麺", launch_date="2026-09-01", net_amount="100g")
        c = item(product_id="c", name="組麺", launch_date="2026-10-01", net_amount="80g")
        self.assertEqual(g.disambiguation_note(a, [a, b, c]), "2026年9月1日発売・80g")

    def test_item_pages_get_note_in_title_description_h1_and_links(self):
        a, b = twins()
        h = g.render_item_page(a, [a, b, item(product_id="x", name="別の品")])
        self.assertIn("<title>日清食品「ツイン麺」（2026年9月1日発売）の発売日・カロリー・価格｜", h)
        self.assertIn("「ツイン麺」（2026年9月1日発売）、", h)  # description
        self.assertEqual(h.count("<h1"), 1)
        self.assertIn('<span class="h1-sub">（2026年9月1日発売）</span>', h)
        self.assertIn("<h2>同じ名前の商品</h2>", h)
        self.assertIn('href="/items/nissin-s2.html">ツイン　麺（2026年11月9日発売）</a>', h)
        self.assertIn(f'<link rel="canonical" href="{g.CONFIG["site_url"]}items/nissin-s1.html">', h)  # 自分自身のまま

    def test_same_name_page_excludes_twin_from_related_items(self):
        a, b = twins()
        h = g.render_item_page(a, [a, b])
        self.assertNotIn("同じ時期の", h)

    def test_other_company_same_name_is_not_a_twin(self):
        a = item(product_id="s1", name="ツイン麺")
        b = item(product_id="s2", name="ツイン麺", chain="myojo")
        h = g.render_item_page(a, [a, b])
        self.assertNotIn("同じ名前の商品", h)
        self.assertIn("<title>日清食品「ツイン麺」の発売日", h)

    def test_unique_name_keeps_title_and_h1(self):
        a = item()
        h = g.render_item_page(a, [a, item(product_id="x", name="別の品")])
        self.assertIn("<title>日清食品「テストヌードル」の発売日・カロリー・価格｜カップ麺ポチ</title>", h)
        self.assertIn("<h1>テストヌードル</h1>", h)
        self.assertNotIn("同じ名前の商品", h)


class TestRelatedItems(unittest.TestCase):
    def pool(self):
        days = ["2026-08-01", "2026-08-10", "2026-09-01", "2026-09-15", "2026-10-01", "2026-10-20",
                "2026-11-01", "2026-11-09"]
        pool = [item(product_id=f"p{i}", name=f"品{i}", launch_date=d) for i, d in enumerate(days)]
        pool.append(item(product_id="m1", name="他社品", chain="myojo", launch_date="2026-10-01"))
        return pool

    def test_nearest_dates_same_company_max_6_excluding_self(self):
        pool = self.pool()
        me = pool[4]  # 2026-10-01
        rel = g.related_items(me, pool)
        self.assertEqual(len(rel), 6)
        self.assertNotIn(me, rel)
        self.assertTrue(all(o["chain"] == "nissin" for o in rel))
        # 自分以外の7件のうち、いちばん遠い(8月1日)が落ちる。表示は発売日の新しい順
        self.assertEqual([o["product_id"] for o in rel], ["p7", "p6", "p5", "p3", "p2", "p1"])

    def test_deterministic_and_stable_when_new_far_item_added(self):
        pool = self.pool()
        me = pool[1]  # 2026-08-10
        before = [o["product_id"] for o in g.related_items(me, pool)]
        self.assertEqual(before, [o["product_id"] for o in g.related_items(me, list(reversed(pool)))])
        newer = pool + [item(product_id="new", name="新品", launch_date="2026-12-20")]
        self.assertEqual(before, [o["product_id"] for o in g.related_items(me, newer)])

    def test_excludes_same_name_and_handles_few_items(self):
        a, b = twins()
        other = item(product_id="o", name="別の品", launch_date="2026-10-01")
        self.assertEqual([o["product_id"] for o in g.related_items(a, [a, b, other])], ["o"])
        self.assertEqual(g.related_items(a, [a]), [])
        self.assertNotIn("同じ時期", g.render_item_page(a, [a]))

    def test_links_are_plain_hrefs_with_price_and_kcal(self):
        pool = self.pool()
        h = g.render_item_page(pool[4], pool)
        self.assertIn("<h2>同じ時期の日清食品のカップ麺</h2>", h)
        self.assertIn('href="/items/nissin-p7.html">品7</a>', h)
        self.assertIn("259円（税別）・353kcal", h)
        for word in ("販売終了", "販売中"):
            self.assertNotIn(word, h)


class TestItemPageSeo(unittest.TestCase):
    def test_jan_stays_as_text_on_every_real_product_page(self):
        items = g.load_all_products()
        with_jan = [it for it in items if it.get("jan")]
        self.assertTrue(with_jan)
        for it in with_jan:
            text = plain_text(g.render_item_page(it, items))
            self.assertIn(f"JANコード {it['jan']}", text, it["product_id"])

    def test_jan_stays_as_text_on_same_name_pages(self):
        a, b = twins()
        for it in (a, b):
            self.assertIn("JANコード 4900000000001", plain_text(g.render_item_page(it, [a, b])))

    def test_single_h1_and_section_headings_are_h2(self):
        a = item()
        h = g.render_item_page(a, [a])
        self.assertEqual(h.count("<h1"), 1)
        self.assertNotIn("<h3", h)
        for head in ("栄養成分", "アレルゲン", "補足と出典"):
            self.assertRegex(h, rf"<h2>{head}")

    def test_site_name_is_h1_only_on_top(self):
        top = g.page_shell(g.CONFIG["site_title"], "d", "<p>b</p>", g.CONFIG["site_url"], is_top=True)
        sub = g.page_shell("x", "d", "<p>b</p>", g.CONFIG["site_url"] + "x/")
        self.assertEqual(top.count("<h1"), 1)
        self.assertEqual(sub.count("<h1"), 0)
        self.assertIn('class="site-name"', sub)

    def test_breadcrumb_json_ld_on_item_page(self):
        a = item()
        h = g.render_item_page(a, [a])
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', h)
        data = json.loads(m.group(1))
        self.assertEqual(data["@type"], "BreadcrumbList")
        urls = [e["item"] for e in data["itemListElement"]]
        base = g.CONFIG["site_url"]
        self.assertEqual(urls, [base, base + "companies/nissin/", base + "items/nissin-t9001.html"])
        self.assertEqual([e["position"] for e in data["itemListElement"]], [1, 2, 3])
        self.assertNotIn('"Product"', h)


class TestCompanyPageSeo(unittest.TestCase):
    def test_title_and_description(self):
        items = [item(product_id=str(i), name=f"品{i}", launch_date=f"2026-10-0{i}") for i in range(1, 6)]
        t, d = g.company_seo("日清食品", items)
        self.assertEqual(t, "日清食品のカップ麺の新商品一覧（発売日順）")
        self.assertIn("5件を発売日順に記録", d)
        self.assertIn("「品5」「品4」「品3」", d)
        self.assertNotIn("品2", d)
        self.assertIn("カロリー", d)
        self.assertLessEqual(len(d), 120)

    def test_description_drops_names_to_fit_and_omits_missing_facts(self):
        long = "あ" * 45
        items = [item(product_id=str(i), name=f"{long}{i}", launch_date=f"2026-10-0{i}", nutrition=None,
                      price_excl_tax=None, price_label="オープンプライス") for i in range(1, 4)]
        t, d = g.company_seo("日清食品", items)
        self.assertLessEqual(len(d), 120)
        self.assertNotIn("カロリー", d)
        self.assertNotIn("価格", d)

    def test_company_page_has_breadcrumb_and_page_title_h1(self):
        c = g.CHAINS[0]
        items = [item()]
        seo_t, seo_d = g.company_seo(c["name"], items)
        h = g.render_grid_page("見出し", seo_d, items, g.CONFIG["site_url"] + "companies/nissin/",
                               seo_title=seo_t, extra_head=g.breadcrumb_ld([("a", "https://x/")]))
        self.assertIn(f"<title>{seo_t}｜カップ麺ポチ</title>", h)
        self.assertIn("BreadcrumbList", h)
        self.assertEqual(h.count("<h1"), 1)
        self.assertIn('<h1 class="page-title">見出し', h)


class TestOneH1PerPage(unittest.TestCase):
    def test_every_page_type_has_exactly_one_h1(self):
        items = [item(), item(product_id="t9002", name="別の品", launch_date="2026-10-01")]
        pages = {
            "top": g.render_top(items),
            "top_empty": g.render_top([]),
            "company": g.render_grid_page("日清食品のカップ麺の新商品", "d", items, "https://x/c/"),
            "company_empty": g.render_grid_page("日清食品のカップ麺の新商品", "d", [], "https://x/c/"),
            "companies": g.render_companies_top(),
            "archive": g.render_archive_index(items),
            "archive_empty": g.render_archive_index([]),
            "month": g.render_grid_page("2026年11月に発売", "d", items, "https://x/a/"),
            "item": g.render_item_page(items[0], items),
        }
        for name, h in pages.items():
            self.assertEqual(h.count("<h1"), 1, name)

    def test_is_top_is_explicit(self):
        # タイトルがサイト名と同じでも、is_top を渡さなければ h1 にしない
        h = g.page_shell(g.CONFIG["site_title"], "d", "<p>b</p>", "https://x/")
        self.assertEqual(h.count("<h1"), 0)


class TestSitemapLastmod(unittest.TestCase):
    def test_lastmod_is_first_seen_not_today_and_lists_use_max(self):
        a = item(product_id="a", first_seen_at="2026-10-01T07:00:00+09:00", launch_date="2026-10-05")
        b = item(product_id="b", first_seen_at="2026-11-05T07:00:00+09:00", launch_date="2026-11-09",
                 detail_checked_at="2026-11-08")
        x = g.generate_sitemap([a, b], ["2026-10", "2026-11"])
        self.assertIn("<loc>https://cupmen.netaful.jp/items/nissin-a.html</loc><lastmod>2026-10-01</lastmod>", x)
        self.assertIn("<loc>https://cupmen.netaful.jp/items/nissin-b.html</loc><lastmod>2026-11-05</lastmod>", x)
        self.assertIn("<loc>https://cupmen.netaful.jp/archive/2026-10/</loc><lastmod>2026-10-01</lastmod>", x)
        self.assertIn("<loc>https://cupmen.netaful.jp/companies/nissin/</loc><lastmod>2026-11-05</lastmod>", x)
        self.assertIn("<loc>https://cupmen.netaful.jp/</loc><lastmod>2026-11-05</lastmod>", x)
        # 商品が無い会社のページには lastmod を付けない
        self.assertIn("<loc>https://cupmen.netaful.jp/companies/myojo/</loc></url>", x)
        self.assertEqual(g.item_lastmod(item(first_seen_at=None)), None)


if __name__ == "__main__":
    unittest.main()
