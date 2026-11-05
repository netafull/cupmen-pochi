"""合成フィクスチャ: 4社の公式データの「構造だけ」を再現した、名前・数値を創作したデータ。

各社の規約が複製・転載を禁じているため、公式のHTML/JSONをそのままリポジトリに入れない。
ここにあるのはパーサが読む箇所(タグ・クラス名・キー)だけを再現した最小のものと、それを組み立てる関数。
商品名・価格・カロリー・アレルゲンはすべて創作(実在の商品とは無関係)。
"""

from __future__ import annotations

import json

from helpers import ACECOOK, MYOJO, NISSIN, SANYO

ALLERGEN_28 = ["小麦", "そば", "卵", "乳成分", "えび", "かに", "落花生", "くるみ", "カシューナッツ", "アーモンド",
               "マカダミアナッツ", "豚肉", "鶏肉", "牛肉", "いか", "さけ", "さば", "いくら", "あわび", "大豆", "やまいも",
               "ごま", "りんご", "オレンジ", "バナナ", "もも", "キウイ", "ゼラチン"]

# ---------------------------------------------------------------------------
# 日清
# ---------------------------------------------------------------------------
NISSIN_LIST = NISSIN + "/jp/product/new/"


def nissin_url(pid: str) -> str:
    return f"{NISSIN}/jp/product/items/{pid}/"


def nissin_list(groups: list[tuple[int, int, list[tuple[str, str]]]]) -> str:
    """groups = [(月, 日, [(id, 名前)])]。新しい順に並べて渡す。"""
    out = ['<div class="ProductsGrid">']
    for m, d, items in groups:
        out.append(f'<h2 class="CardProductsHead"><span>{m}月{d}日発売</span></h2>')
        for pid, name in items:
            out.append(f'<div class="CardProducts"> <a href="/jp/product/items/{pid}/" class="CardProducts__link">'
                       f'<div class="CardProducts__image"><img src="x.png" alt=""></div>'
                       f'<div class="CardProducts__content"><div class="CardProducts__title">{name}</div></div></a></div>')
    out.append("</div>")
    return "<html><body>" + "".join(out) + "</body></html>"


def nissin_detail(name: str = "テストカップ", price: str = "259円 (税別)", net: str = "76g (60g)",
                  net_label: str = "麺量", area: str = "全国", jan: str = "4900000000001",
                  packing: str = "1ケース20食入", water: str | None = "290ml", kcal: int = 353,
                  allergens: list[str] | None = None, discontinued: bool = False) -> str:
    allergens = ["小麦", "卵"] if allergens is None else allergens
    rows = [("希望小売価格", price), (f"内容量 ({net_label})", net), ("発売地域", area),
            ("JANコード", jan), ("荷 姿", packing)]
    if water:
        rows.append(("必要なお湯の目安量", water))
    trs = "".join(f'<tr> <th class="w-[171px]">{k}</th> <td>{v}</td> </tr>' for k, v in rows)
    lis = "".join(f'<li class="{"active " if a in allergens else " "}" aria-hidden="false" data-astro-cid-x="true">'
                  f'<span data-astro-cid-x="true">{a}</span></li>' for a in ALLERGEN_28)
    gone = "<p>この商品の生産は終了しています。</p>" if discontinued else ""
    return f"""<html><body><main><h1 data-astro-cid-u="true"><div class="Heading__inner"> {name} </div></h1>{gone}
<table class="Table simple"><tbody>{trs}</tbody></table>
<h2>栄養成分表示　[1食 (76g) 当たり]</h2>
<table class="Table nutrition"><tbody>
<tr class="with-breakdown"><th colspan="2"> 熱量 </th><td>{kcal}kcal</td></tr>
<tr class="is-breakdown"><th>&nbsp;</th><th>めん・かやく</th><td>300kcal</td></tr>
<tr><th colspan="2"> たんぱく質 </th><td>8.0g</td></tr>
<tr><th colspan="2"> 脂質 </th><td>15.0g</td></tr>
<tr><th colspan="2"> 炭水化物 </th><td>46.4g</td></tr>
<tr><th colspan="2"> 食塩相当量 </th><td>4.3g</td></tr></tbody></table>
<table class="Table nutrition"><tbody><tr><th> カルシウム </th><td>134mg</td></tr></tbody></table>
<h2>アレルゲン情報</h2><ul data-astro-cid-x="true">{lis}</ul>
</main></body></html>"""


# ---------------------------------------------------------------------------
# 明星
# ---------------------------------------------------------------------------
MYOJO_LIST_P1 = MYOJO + "/api/v1.1/ja_jp/products/list?company=myojofoods"
MYOJO_LIST_P2 = MYOJO_LIST_P1 + "&page=2"


def myojo_item(pid: int, name: str, date: str, sub: str = "カップ麺", renew: str | None = None,
               price: str = "252円 (税別)") -> dict:
    d = {"id": pid, "date": date, "name": name, "calorie": "400kcal", "net_amount": "68g (55g) ",
         "jan": f"49028810{pid:05d}", "price": price, "is_new": False, "is_before_release": False,
         "category": {"label": "即席麺"}, "subcategory": {"label": sub}}
    if renew:
        d["renew_date"] = renew
    return d


def myojo_list(items: list[dict]) -> str:
    return json.dumps({"products": items}, ensure_ascii=False)


def myojo_detail(pid: int, area: str = "全国", ec_only: bool = False, company: str = "myojofoods") -> str:
    return json.dumps({
        "id": pid, "company_slug": company, "sales_area": area, "is_ec_only": ec_only,
        "release_date": "2026-10-26", "net_amount": "107g (90g) ", "net_label": "麺量",
        "packing": "1ケース(12食入)", "water_consumption": "550ml",
        "nutrition_label": "栄養成分表示　[1食 (107g) 当たり]",
        "nutrition": [{"name": "熱量", "amount": "504kcal"}, {"name": "たんぱく質", "amount": "9.8g"},
                      {"name": "脂質", "amount": "25.5g"}, {"name": "炭水化物", "amount": "58.8g"},
                      {"name": "食塩相当量", "amount": "3.5g"}, {"name": "カルシウム", "amount": "200mg"}],
        "allergens": ["小麦", "乳成分"],
    }, ensure_ascii=False)


def myojo_detail_url(pid: int) -> str:
    return f"{MYOJO}/api/v1/ja_jp/products/detail?id={pid}"


# ---------------------------------------------------------------------------
# サンヨー
# ---------------------------------------------------------------------------
SANYO_SITEMAP = SANYO + "/wp-sitemap-posts-products-1.xml"


def sanyo_url(pid: str) -> str:
    return f"{SANYO}/products/{pid}/"


def sanyo_sitemap(ids: list[str]) -> str:
    locs = "".join(f"<url><loc>{sanyo_url(i)}</loc></url>" for i in ids)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def sanyo_detail(name: str = "テスト一番 カップ", month: str | None = "2026年11月", packing: str = "1ケース　12食入り",
                 price: str = "271円") -> str:
    hits = {"小麦", "卵"}
    cells = "".join(f'<td class="{"hit" if a in hits else ""}">{a}</td>' for a in ALLERGEN_28)
    mrow = f"<dl><dt>発売月</dt><dd>{month}</dd></dl>" if month else ""
    return f"""<html><body><h1 class="new">{name}</h1>
<dl><dt>
 内容量（めん重量）
</dt><dd>96g（70g）</dd></dl>
<dl><dt>希望小売価格（税抜）</dt><dd>{price}</dd></dl><dl><dt>発売地区</dt><dd>全国</dd></dl>
<dl><dt>JANコード</dt><dd>4901734000001</dd></dl>{mrow}<dl><dt>荷姿</dt><dd>{packing}</dd></dl>
<h2 class="sp_toggle">本製品に含まれるアレルギー物質（<strong>■</strong>で表示）<span></span></h2>
<table><tr>{cells}</tr></table>
<h2 class="sp_toggle">栄養成分表示　1食（96g）当たり<span></span></h2>
<table class="base base1"><tr><th>熱量</th><td>392kcal</td></tr><tr><th>たんぱく質</th><td>8.5g</td></tr>
<tr><th>脂質</th><td>12.1g</td></tr><tr><th>炭水化物</th><td>62.2g</td></tr></table>
<table class="base base2"><tr><th>食塩相当量（全体）</th><td>6.8g</td></tr><tr><th>食塩相当量（スープ）</th><td>4.5g</td></tr></table>
<h2 class="sp_toggle">製品に使用している主な原材料の産地情報</h2></body></html>"""


# ---------------------------------------------------------------------------
# エースコック
# ---------------------------------------------------------------------------
ACECOOK_LIST = ACECOOK + "/products/arrival/"


def acecook_url(pid: str) -> str:
    return f"{ACECOOK}/products/{pid}/"


def acecook_list(items: list[tuple[str, str, str, str]]) -> str:
    """items = [(id, 名前, 日付 2026年11月02日, 価格 255円（税抜）)]"""
    cards = "".join(f"""<div class="p-arrival--itemlist-card"><a href="/products/{pid}/"><span class="p-top__item__title">
<span class="p-top__product__item__title">{name}</span><span class="p-top__product__item__date">{date}</span>
<span class="p-top__product__item__price">{price}</span></span></a></div>""" for pid, name, date, price in items)
    return f"<html><body>{cards}</body></html>"


def acecook_detail(name: str = "テストヌードル", date: str = "2026年10月26日", water: str | None = "510ml",
                   colored: tuple[str, ...] = ("小麦", "乳成分")) -> str:
    cells = "".join(f'<td class="{"cell-beige " if a in colored else ""}text-center">{a}</td>' for a in ALLERGEN_28)
    wrow = f"<div class='comments'><p>必要なお湯の目安量：{water}</p></div>" if water else ""
    return f"""<html><body><div class="p-product__info"><h2>{name}</h2>
<ul class="p-product__tags"><li><dl><dt>発売日</dt><dd>{date}</dd></dl></li>
<li><dl><dt>内容量</dt><dd>
 92g
 （めん
 78g）
</dd></dl></li><li><dl><dt>希望小売価格</dt><dd>298円（税抜）</dd></dl></li>
<li><dl><dt>発売地域</dt><dd>全国</dd></dl></li><li><dl><dt>JANコード</dt><dd>4901071400001</dd></dl></li></ul></div>
<div class="p-product__description-post"><h4>栄養成分表示<small>【1食（92g）あたり】</small></h4>
<table><tr><th>エネルギー</th><td>398kcal</td></tr><tr><th>たん白質</th><td>8.7g</td></tr><tr><th>脂質</th><td>14.0g</td></tr>
<tr><th>炭水化物</th><td>59.2g</td></tr>
<tr><th>食塩相当量<br>（めん・かやく/スープ）</th><td>6.2g<br>（めん・かやく1.9g/スープ4.3g）</td></tr></table>{wrow}</div>
<div class="p-product__description-post"><h4>アレルゲン情報</h4><table class="layout-fix"><tr>{cells}</tr></table></div>
</body></html>"""
