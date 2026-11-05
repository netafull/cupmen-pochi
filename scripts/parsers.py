#!/usr/bin/env python3
"""4社(日清・明星・サンヨー・エースコック)の公式ページの読み取り。標準ライブラリだけ。

保存するのは事実だけ(商品名・価格・内容量・発売日・発売地域・JAN・栄養成分・アレルゲン)。
画像・商品説明文・原材料名は読み取りもしない(パーサがそのキーを持たない)。
ネットワークには触らない(HTML/JSON/XMLの文字列を受け取って辞書を返す)。
"""

from __future__ import annotations

import datetime
import html as html_lib
import json
import re
import unicodedata

NISSIN_BASE = "https://www.nissin.com"
MYOJO_BASE = "https://www.myojofoods.co.jp"
SANYO_BASE = "https://www.sanyofoods.co.jp"
ACECOOK_BASE = "https://www.acecook.co.jp"

# 明星の一覧は company=myojofoods が無いと日清の商品が返る。必ず付ける
MYOJO_LIST_URL = MYOJO_BASE + "/api/v1.1/ja_jp/products/list?company=myojofoods"
MYOJO_PAGE_SIZE = 100  # 1ページ100件で切れる(x-total-count は全件数)。page=2 以降を取る


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def strip_tags(s: str | None) -> str:
    """タグを外し、実体参照を戻し、空白をまとめる。"""
    s = re.sub(r"<br\s*/?>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def normalize_name(s: str | None) -> str:
    """ネタフル記事との照合用の正規化。"""
    s = nfkc(s)
    s = re.sub(r"[\s「」『』\"“”'’・<>()（）【】\[\]～〜~!！?？\-ー―—:：/／,、。.&＆®™©*＊★☆×]+", "", s)
    return s.lower()


def parse_price_excl_tax(text: str | None) -> int | None:
    """「259円 (税別)」「255円（税抜）」「271円」→ 税別の整数。オープンプライス等は None。"""
    t = nfkc(text)
    m = re.search(r"([\d,]+)\s*円", t)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def price_label_of(text: str | None) -> str | None:
    """価格が数字でないときの表記(オープンプライス・ノンプリントプライス)。数字があれば None。"""
    t = nfkc(text).strip()
    if not t or parse_price_excl_tax(t) is not None:
        return None
    return t[:20]


def clean_net_amount(text: str | None) -> str | None:
    """「76g (60g)」「92g\n（めん\n78g）」→ 空白を整えた表記。"""
    t = nfkc(strip_tags(text))
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*\(\s*", "（", t)
    t = re.sub(r"\s*\)", "）", t)
    t = re.sub(r"（\s+", "（", t)
    return t or None


def clean_label(text: str | None) -> str | None:
    """「栄養成分表示 [1食 (76g) 当たり]」「【1食（92g）あたり】」→「1食（76g）当たり」。"""
    t = nfkc(text)
    t = re.sub(r"^栄養成分表示", "", t.strip())
    t = re.sub(r"[\[\]【】［］]", "", t).strip()
    t = re.sub(r"\s*\(\s*", "（", t)
    t = re.sub(r"\s*\)\s*", "）", t)
    t = re.sub(r"\s+", "", t).replace("あたり", "当たり")
    return t or None


def _num(s: str | None) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", nfkc(s))
    return float(m.group(1)) if m else None


_NUT_KEYS = (
    ("kcal", ("熱量", "エネルギー")),
    ("protein_g", ("たんぱく質", "たん白質", "たんぱく")),
    ("fat_g", ("脂質",)),
    ("carbs_g", ("炭水化物",)),
    ("salt_g", ("食塩相当量",)),
)


def build_nutrition(rows: list[tuple[str, str]]) -> dict | None:
    """[(項目名, 値の文字列)] → {kcal, protein_g, fat_g, carbs_g, salt_g, other:{名前: 値}}。

    食塩相当量は最初に出た「全体」の値を使う。めん・かやく/スープの内訳や、ビタミン等は other に入れる。
    kcal が取れなければ None(表が別の構造になっている疑い)。"""
    out: dict = {}
    other: dict[str, str] = {}
    for name, amount in rows:
        name = nfkc(name).replace(" ", "").replace("※", "").strip()
        amount = nfkc(amount).strip()
        if not name or not amount:
            continue
        matched = False
        for key, words in _NUT_KEYS:
            if any(name.startswith(w) for w in words):
                is_breakdown = key == "salt_g" and ("めん" in name or "スープ" in name or "かやく" in name)
                if is_breakdown:
                    other[name] = amount
                elif key not in out:
                    v = _num(amount)
                    if v is not None:
                        out[key] = v
                matched = True
                break
        if not matched:
            other[name] = amount
    if "kcal" not in out:
        return None
    out["other"] = other
    return out


# ---------------------------------------------------------------------------
# 日清食品
# ---------------------------------------------------------------------------

_NISSIN_TOKEN = re.compile(
    r'<h2 class="CardProductsHead"><span>(\d{1,2})月(\d{1,2})日発売</span></h2>'
    r'|<a href="/jp/product/items/(\d+)/" class="CardProducts__link">(.*?)</a>',
    re.S,
)


def infer_nissin_dates(month_days: list[tuple[int, int]], today: datetime.date) -> list[datetime.date]:
    """一覧の見出し「M月D日発売」(新しい順。年は書かれていない)に年を補う。

    最初の見出しは、今日に一番近くなる年を選ぶ(これから発売も、数か月前もありうる)。
    以降は新しい順に並んでいる前提で、前の見出しより後の日付になったら前の年にする
    (年またぎ: 1/12, 1/5, 12/28 → 2027-01-12, 2027-01-05, 2026-12-28)。"""
    out: list[datetime.date] = []
    for i, (m, d) in enumerate(month_days):
        if i == 0:
            cands = []
            for y in (today.year - 1, today.year, today.year + 1):
                try:
                    cands.append(datetime.date(y, m, d))
                except ValueError:
                    pass
            out.append(min(cands, key=lambda c: abs((c - today).days)))
            continue
        prev = out[-1]
        try:
            cur = datetime.date(prev.year, m, d)
        except ValueError:
            cur = datetime.date(prev.year - 1, m, d)
        if cur > prev:
            cur = datetime.date(prev.year - 1, m, d)
        out.append(cur)
    return out


def parse_nissin_list(html: str, today: datetime.date) -> list[dict]:
    """新発売一覧 → [{id, name, launch_date, url}]。見出しの前に出た商品(日付不明)は含めない。"""
    html = _strip_astro(html)
    heads: list[tuple[int, int]] = []
    raw: list[tuple[int, dict]] = []  # (見出し番号, 項目)
    for m in _NISSIN_TOKEN.finditer(html):
        if m.group(1):
            heads.append((int(m.group(1)), int(m.group(2))))
        elif heads:
            t = re.search(r'CardProducts__title">(.*?)</div>', m.group(4), re.S)
            name = strip_tags(t.group(1)) if t else ""
            raw.append((len(heads) - 1, {"id": m.group(3), "name": name}))
    dates = infer_nissin_dates(heads, today)
    out = []
    for hi, e in raw:
        e["launch_date"] = dates[hi].isoformat()
        e["url"] = f"{NISSIN_BASE}/jp/product/items/{e['id']}/"
        out.append(e)
    return out


def _rows(table_html: str) -> list[tuple[str, str, str]]:
    """<tr> を (trのclass, th, td) に。thが複数あるときは最後のthを項目名とする(内訳行)。"""
    rows = []
    for tr in re.finditer(r"<tr([^>]*)>(.*?)</tr>", table_html, re.S):
        ths = re.findall(r"<th[^>]*>(.*?)</th>", tr.group(2), re.S)
        td = re.search(r"<td[^>]*>(.*?)</td>", tr.group(2), re.S)
        if not ths or not td:
            continue
        cls = re.search(r'class="([^"]*)"', tr.group(1))
        rows.append((cls.group(1) if cls else "", strip_tags(ths[-1]), td.group(1)))
    return rows


def _strip_astro(html: str) -> str:
    return re.sub(r' data-astro-cid-[\w-]+(?:="[^"]*")?', "", html)


def parse_nissin_detail(html: str) -> dict:
    """日清の商品詳細。カップ麺の判定に使う water(必要なお湯の目安量)・net_label(麺量)・packing も返す。"""
    html = _strip_astro(html)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    out: dict = {"name": strip_tags(h1.group(1)) if h1 else None}
    t = re.search(r'<table class="Table simple">(.*?)</table>', html, re.S)
    info: dict[str, str] = {}
    if t:
        for _cls, th, td in _rows(t.group(1)):
            info[nfkc(th).replace(" ", "")] = strip_tags(td)
    price_text = info.get("希望小売価格")
    out["price_excl_tax"] = parse_price_excl_tax(price_text)
    out["price_label"] = price_label_of(price_text)
    net_key = next((k for k in info if k.startswith("内容量")), None)
    out["net_amount"] = clean_net_amount(info.get(net_key)) if net_key else None
    m = re.search(r"[（(]([^）)]+)[）)]", net_key or "")
    out["net_label"] = m.group(1) if m else None
    out["sales_area"] = info.get("発売地域")
    out["jan"] = info.get("JANコード")
    out["packing"] = info.get("荷姿")
    out["water"] = info.get("必要なお湯の目安量")
    # 栄養成分(最初の見出し「栄養成分表示 [1食 (76g) 当たり]」の直後の表)
    rows: list[tuple[str, str]] = []
    nut_label = None
    k = html.find("栄養成分表示")
    if k >= 0:
        seg_end = html.find("アレルゲン情報", k)
        seg = html[k: seg_end if seg_end > 0 else k + 20000]
        lm = re.match(r"栄養成分表示([^<]*)<", seg)
        nut_label = strip_tags(lm.group(1)) if lm else None
        for tb in re.finditer(r'<table class="Table nutrition">(.*?)</table>', seg, re.S):
            for cls, th, td in _rows(tb.group(1)):
                if "is-breakdown" in cls:
                    continue
                rows.append((th, strip_tags(td)))
    out["nutrition"] = build_nutrition(rows)
    out["nutrition_label"] = clean_label(nut_label)
    # アレルゲン(■で塗られた=class="active")。28品目の<li>が並ぶ<ul>を探す
    out["allergens"] = None
    ul = re.search(r'<ul>((?:<li class="[^"]*"[^>]*><span>[^<]*</span></li>\s*){20,})</ul>', html)
    if ul:
        items = re.findall(r'<li class="([^"]*)"[^>]*><span>([^<]*)</span>', ul.group(1))
        hits = [html_lib.unescape(n).strip() for c, n in items if "active" in c.split()]
        if hits:  # 1つも塗られていなければ未取得扱い(構造変更の疑い)
            out["allergens"] = hits
    out["discontinued"] = "この商品の生産は終了" in html
    return out


# ---------------------------------------------------------------------------
# 明星食品
# ---------------------------------------------------------------------------

MYOJO_CUP_SUBCATEGORIES = {"カップ麺", "カップ麺(ビッグ)", "焼そば", "焼そば(ビッグ)"}


def _ymd(s: str | None) -> str | None:
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def parse_myojo_list(text: str) -> list[dict]:
    """一覧JSON(1ページ分) → 項目のリスト。構造が違えば ValueError。"""
    data = json.loads(text)
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list):
        raise ValueError("products が無い")
    out = []
    for p in products:
        if not isinstance(p, dict) or "id" not in p or "name" not in p:
            raise ValueError("項目の形が想定と違う")
        sub = nfkc((p.get("subcategory") or {}).get("label")).replace(" ", "")
        out.append({
            "id": str(p["id"]),
            "name": strip_tags(p["name"]),
            "launch_date": _ymd(p.get("date")),
            "renew_date": _ymd(p.get("renew_date")),
            "price_excl_tax": parse_price_excl_tax(p.get("price")),
            "price_label": price_label_of(p.get("price")),
            "net_amount": clean_net_amount(p.get("net_amount")),
            "jan": p.get("jan") or None,
            "subcategory": sub,
            "category": (p.get("category") or {}).get("label"),
            "url": f"{MYOJO_BASE}/products/items/{p['id']}/",
        })
    return out


def myojo_is_cup(entry: dict) -> bool:
    return entry.get("subcategory") in MYOJO_CUP_SUBCATEGORIES


def parse_myojo_detail(text: str) -> dict:
    d = json.loads(text)
    if not isinstance(d, dict) or "id" not in d:
        raise ValueError("詳細JSONの形が想定と違う")
    rows = [(n.get("name"), n.get("amount")) for n in (d.get("nutrition") or []) if isinstance(n, dict)]
    allergens = d.get("allergens")
    return {
        "company_slug": d.get("company_slug"),
        "sales_area": d.get("sales_area") or None,
        "ec_only": bool(d.get("is_ec_only")),
        "launch_date": _ymd(d.get("release_date")),
        "net_amount": clean_net_amount(d.get("net_amount")),
        "net_label": d.get("net_label") or None,
        "packing": d.get("packing") or None,
        "water": d.get("water_consumption") or None,
        "nutrition": build_nutrition([(a or "", b or "") for a, b in rows]),
        "nutrition_label": clean_label(d.get("nutrition_label")),
        "allergens": [str(a) for a in allergens] if isinstance(allergens, list) and allergens else None,
    }


# ---------------------------------------------------------------------------
# サンヨー食品
# ---------------------------------------------------------------------------

def parse_sanyo_sitemap(xml: str) -> list[dict]:
    out = []
    for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
        m = re.search(r"/products/(\d+)/?$", loc)
        if m:
            out.append({"id": m.group(1), "url": f"{SANYO_BASE}/products/{m.group(1)}/"})
    return out


def parse_sanyo_detail(html: str) -> dict:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    out: dict = {"name": strip_tags(h1.group(1)) if h1 else None}
    info: dict[str, str] = {}
    for dt, dd in re.findall(r"<dl>\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>\s*</dl>", html, re.S):
        info[nfkc(strip_tags(dt)).replace(" ", "")] = strip_tags(dd)
    net_key = next((k for k in info if k.startswith("内容量")), None)
    out["net_amount"] = clean_net_amount(info.get(net_key)) if net_key else None
    m = re.search(r"[（(]([^）)]+)[）)]", net_key or "")
    out["net_label"] = m.group(1) if m else None
    price_key = next((k for k in info if k.startswith("希望小売価格")), None)
    out["price_excl_tax"] = parse_price_excl_tax(info.get(price_key)) if price_key else None
    out["price_label"] = price_label_of(info.get(price_key)) if price_key else None
    out["sales_area"] = info.get("発売地区")
    out["jan"] = info.get("JANコード")
    out["packing"] = info.get("荷姿")
    mm = re.search(r"(\d{4})年\s*(\d{1,2})月", nfkc(info.get("発売月")))
    out["launch_month"] = f"{int(mm.group(1)):04d}-{int(mm.group(2)):02d}" if mm else None
    # 栄養成分
    rows: list[tuple[str, str]] = []
    nut_label = None
    nm = re.search(r"<h2[^>]*>栄養成分表示([^<]*)<", html)
    if nm:
        nut_label = nfkc(strip_tags(nm.group(1))).strip() or None
        seg = html[nm.end(): nm.end() + 6000]
        end = seg.find("<h2")
        seg = seg[: end if end > 0 else len(seg)]
        for tb in re.finditer(r"<table[^>]*>(.*?)</table>", seg, re.S):
            for _cls, th, td in _rows(tb.group(1)):
                rows.append((th, strip_tags(td)))
    out["nutrition"] = build_nutrition(rows)
    out["nutrition_label"] = clean_label(nut_label)
    # アレルゲン(td class="hit")
    out["allergens"] = None
    am = re.search(r"<h2[^>]*>本製品に含まれるアレルギー物質.*?</h2>(.*?)</table>", html, re.S)
    if am:
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", am.group(1), re.S)
        hits = [strip_tags(c) for a, c in cells if re.search(r'class="[^"]*\bhit\b', a)]
        if len(cells) >= 20 and hits:
            out["allergens"] = hits
    return out


_SANYO_CUP_WORDS = ("カップ", "タテビッグ", "タテ型", "大口径", "どんぶり", "ミニどんぶり", "ビッグ", "カップスター", "焼そば", "焼きそば")
_SANYO_NOT_CUP_WORDS = ("パック", "袋", "冷凍", "チルド")


def sanyo_is_cup(name: str | None, packing: str | None = None) -> bool:
    """サンヨー食品は商品名で判定する。「5食パック」「袋」は除外。

    名前にカップ系の語(カップ・タテビッグ・タテ型・大口径・どんぶり・ビッグ)があればカップ。
    焼そばは「カップ」「ビッグ」などの語が無いと袋麺と区別できないので、語が無ければ荷姿が
    「1ケース 12食入り」のようにパックでない形のときだけカップとする。"""
    n = nfkc(name)
    if any(w in n for w in _SANYO_NOT_CUP_WORDS):
        return False
    if any(w in n for w in ("カップ", "タテビッグ", "タテ型", "大口径", "どんぶり", "ビッグ")):
        return True
    p = nfkc(packing)
    return bool(re.search(r"\d+食入", p)) and "パック" not in p and "袋" not in p and "束" not in p


# ---------------------------------------------------------------------------
# エースコック
# ---------------------------------------------------------------------------

def _jp_date(text: str | None) -> str | None:
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", nfkc(text))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_acecook_list(html: str) -> list[dict]:
    out = []
    for m in re.finditer(
            r'<a href="/products/(\d{6})/">(.*?)</a>', html, re.S):
        body = m.group(2)
        title = re.search(r'p-top__product__item__title">(.*?)</span>', body, re.S)
        date = re.search(r'p-top__product__item__date">(.*?)</span>', body, re.S)
        price = re.search(r'p-top__product__item__price">(.*?)</span>', body, re.S)
        if not title:
            continue
        out.append({
            "id": m.group(1),
            "name": strip_tags(title.group(1)),
            "launch_date": _jp_date(date.group(1)) if date else None,
            "price_excl_tax": parse_price_excl_tax(strip_tags(price.group(1))) if price else None,
            "url": f"{ACECOOK_BASE}/products/{m.group(1)}/",
        })
    return out


def parse_acecook_detail(html: str) -> dict:
    h2 = re.search(r'<div class="p-product__info">\s*<h2>(.*?)</h2>', html, re.S)
    out: dict = {"name": strip_tags(h2.group(1)) if h2 else None}
    info: dict[str, str] = {}
    for dt, dd in re.findall(r"<dl>\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>\s*</dl>", html, re.S):
        info[nfkc(strip_tags(dt)).replace(" ", "")] = strip_tags(dd)
    out["launch_date"] = _jp_date(info.get("発売日"))
    out["net_amount"] = clean_net_amount(info.get("内容量"))
    out["net_label"] = None  # 「92g（めん 78g）」と表記の中に入っている
    price_text = info.get("希望小売価格")
    out["price_excl_tax"] = parse_price_excl_tax(price_text)
    out["price_label"] = price_label_of(price_text)
    out["sales_area"] = info.get("発売地域")
    out["jan"] = info.get("JANコード")
    # 栄養成分(「栄養成分表示【1食（92g）あたり】」の直後の表)
    rows: list[tuple[str, str]] = []
    nut_label = None
    nm = re.search(r"<h4>栄養成分表示<small>(.*?)</small></h4>", html, re.S)
    if nm:
        nut_label = nfkc(strip_tags(nm.group(1))).strip("【】 ") or None
        seg = html[nm.end(): nm.end() + 5000]
        tb = re.search(r"<table>(.*?)</table>", seg, re.S)
        if tb:
            for tr in re.finditer(r"<tr>\s*<th>(.*?)</th>\s*<td>(.*?)</td>", tb.group(1), re.S):
                name = strip_tags(tr.group(1))
                val = strip_tags(tr.group(2))
                if name.startswith("食塩相当量"):
                    # 「6.2g（めん・かやく1.9g/スープ4.3g）」→ 全体の値と内訳に分ける
                    v = re.match(r"([\d.]+\s*g)\s*(.*)", nfkc(val))
                    if v:
                        rows.append(("食塩相当量", v.group(1)))
                        if v.group(2):
                            rows.append(("食塩相当量（内訳）", v.group(2).strip("（）")))
                        continue
                rows.append((name, val))
        wm = re.search(r"必要なお湯の目安量[：:]\s*([^<\s]+)", seg)
        out["water"] = wm.group(1) if wm else None
    else:
        out["water"] = None
    out["nutrition"] = build_nutrition(rows)
    out["nutrition_label"] = clean_label(nut_label)
    # アレルゲン(色塗り=class に cell-beige)
    out["allergens"] = None
    am = re.search(r"<h4>アレルゲン情報</h4>(.*?)</table>", html, re.S)
    if am:
        cells = re.findall(r"<td([^>]*)>(.*?)</td>", am.group(1), re.S)
        hits = [strip_tags(c) for a, c in cells if "cell-beige" in a]
        if len(cells) >= 20 and hits:
            out["allergens"] = hits
    return out


_ACECOOK_NOT_CUP = ("（袋）", "(袋)", "袋）", "５食パック", "5食パック", "食パック", "スープ")


def acecook_is_cup(name: str | None, water: str | None) -> bool:
    """エースコックは、栄養成分の欄に「必要なお湯の目安量」があり(カップの注ぎ湯)、
    商品名に「（袋）」「5食パック」「スープ」を含まないものをカップ麺とする。"""
    n = nfkc(name)
    if any(nfkc(w) in n for w in _ACECOOK_NOT_CUP):
        return False
    return bool(water)


# ---------------------------------------------------------------------------
# 日清のカップ判定
# ---------------------------------------------------------------------------

_NISSIN_NOT_CUP_NAME = ("メシ", "スープ", "パック", "袋")


def nissin_is_cup(detail: dict) -> bool:
    """日清のカップ麺の判定。次をすべて満たすもの。
    ・詳細に「必要なお湯の目安量」がある(菓子・冷凍・チルドには無い)
    ・内容量の欄が「(麺量)」(ごはん・スープ系は「(ライス量)」など)
    ・荷姿に「パック」が無い(袋麺5食パックは「1パック5食入」)
    ・商品名に メシ・スープ・パック・袋 を含まない
    (「ごはんですよ!」のようにコラボ先の名前に「ごはん」が入るカップ麺があるため、ごはん系は内容量の欄の判定に任せる)"""
    if not detail.get("water"):
        return False
    if (detail.get("net_label") or "") != "麺量":
        return False
    if "パック" in nfkc(detail.get("packing")):
        return False
    n = nfkc(detail.get("name"))
    return not any(w in n for w in _NISSIN_NOT_CUP_NAME)
