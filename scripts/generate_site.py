#!/usr/bin/env python3
"""data/products/*/*.json から docs/ 一式を生成する静的サイトジェネレータ。

回転寿司ポチ・コンビニポチ等の骨格を踏襲した、カップ麺ポチ(cupmen.netaful.jp)のジェネレータ。
軸は**発売日**。トップは発売日の新しい順に並べた1本の一覧(見出しで分けない)で、
会社別ページ・発売月別アーカイブ・検索・RSS・sitemapがある。終了は追わないので「販売中/終了」の区分は無い。

日清・明星・サンヨー・エースコックの4社が対象。表示するのは事実(商品名・価格(税別)・内容量・発売日・
発売地域・JAN・栄養成分・アレルゲン)と公式ページへのリンクだけ。画像・商品説明文・原材料名は載せない(保存もしていない)。
識別子の `chain` / `CHAINS` は会社(メーカー)のこと(回転寿司ポチのエンジンの名前を引き継いでいる)。
"""

from __future__ import annotations

import datetime
import html
import json
import re
import shutil
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
PRODUCTS = DATA / "products"
DOCS = ROOT / "docs"
JST = datetime.timezone(datetime.timedelta(hours=9))

CHAINS = CONFIG["chains"]
CHAIN_NAME = {c["slug"]: c["name"] for c in CHAINS}
CHAIN_OFFICIAL = CONFIG.get("chain_official") or {}
# 出典の表記(出典：○○公式サイト)
CHAIN_SOURCE = {slug: v.get("source") or CHAIN_NAME.get(slug, slug) + "公式サイト" for slug, v in CHAIN_OFFICIAL.items()}
# 全社名の並び(ページの説明文用)
CHAIN_LIST_TEXT = "・".join(c["name"] for c in CHAINS)

REVIEWS_PATH = DATA / "netaful_reviews.json"


def load_reviews() -> dict:
    try:
        return json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"articles": {}, "matches": {}}


REVIEWS = load_reviews()
ARTICLES_BY_ID: dict[int, dict] = {}
_chain = _arts = None  # 記事キャッシュが空のとき del で NameError にならないように
for _chain, _arts in REVIEWS.get("articles", {}).items():
    for _a in _arts:
        _a = dict(_a)
        _a["chain"] = _chain
        ARTICLES_BY_ID[_a["id"]] = _a
del _chain, _arts


def item_reviews(item: dict) -> list[dict]:
    """商品にひも付いたネタフルのレビュー記事(新しい順)。"""
    key = f"{item['chain']}-{item['product_id']}"
    ids = REVIEWS.get("matches", {}).get(key, [])
    arts = [ARTICLES_BY_ID[i] for i in ids if i in ARTICLES_BY_ID]
    return sorted(arts, key=lambda a: a.get("date") or "", reverse=True)


def latest_reviews(chain: str | None, limit: int) -> list[dict]:
    arts = list(ARTICLES_BY_ID.values())
    if chain:
        arts = [a for a in arts if a["chain"] == chain]
    return sorted(arts, key=lambda a: a.get("date") or "", reverse=True)[:limit]


def render_review_card(a: dict) -> str:
    thumb = a.get("thumb") or {}
    img_html = ""
    if thumb.get("url"):
        w, h = thumb.get("width"), thumb.get("height")
        wh_attr = f' width="{int(w)}" height="{int(h)}"' if w and h else ""
        img_html = (
            f'<img src="{esc(thumb["url"])}" alt="{esc(a.get("title") or "")}" '
            f'loading="lazy"{wh_attr}>'
        )
    return f"""<a class="review-card" href="{esc(a.get('link') or '')}" target="_blank" rel="noopener">
{img_html}
<div class="rc-body">
<div class="rc-t">{esc(a.get('title') or '')}</div>
<div class="rc-date">{esc((a.get('date') or '')[:10])}</div>
</div>
</a>"""


def render_review_cards(title: str, articles: list[dict]) -> str:
    """ネタフルのレビュー記事を画像付きカードのグリッドで表示する。"""
    if not articles:
        return ""
    cards = "\n".join(render_review_card(a) for a in articles)
    return f"""<h2>{esc(title)}</h2>
<div class="review-grid">
{cards}
</div>"""


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


RELATED_KEYWORDS: list[dict] = CONFIG.get("related_keywords") or []


def match_keyword_for_name(name: str, keywords: list[dict] | None = None) -> dict | None:
    """商品名にマッチする関連キーワードのうち、一致した語が最も長い(具体的な)
    ものを返す。excludeに指定された語を含む場合、その語による一致は無視する。
    複数のキーワードが同じ長さで一致した場合はkeywordsに書かれた順を優先する。"""
    keywords = RELATED_KEYWORDS if keywords is None else keywords
    name_n = nfkc(name)
    best_kw = None
    best_len = -1
    for kw in keywords:
        excludes = kw.get("exclude") or []
        for w in kw.get("words") or []:
            if w not in name_n:
                continue
            if any(ex in name_n for ex in excludes):
                continue
            if len(w) > best_len:
                best_len = len(w)
                best_kw = kw
            break  # このキーワード内では最初にマッチした語で十分(長さはword単位)
    return best_kw


def articles_for_keyword(keyword: dict, articles: list[dict]) -> list[dict]:
    """記事タイトルにキーワードのいずれかの語を含む記事(除外語を含むものは除く)。"""
    words = keyword.get("words") or []
    excludes = keyword.get("exclude") or []
    result = []
    for a in articles:
        title_n = nfkc(a.get("title"))
        if any(ex in title_n for ex in excludes):
            continue
        if any(w in title_n for w in words):
            result.append(a)
    return result


def select_related_articles(
    item: dict, articles_by_id: dict[int, dict], keywords: list[dict] | None = None,
    limit: int = 4,
) -> tuple[str, list[dict]] | None:
    """商品名から関連キーワードを判定し、そのキーワードに該当する記事(直接ひも
    付いたレビュー記事を除く)を、同じチェーン優先・新しい順に最大limit件返す。
    該当キーワードが無い、または記事が1件も無ければNoneを返す。"""
    kw = match_keyword_for_name(item.get("name", ""), keywords)
    if not kw:
        return None
    candidates = articles_for_keyword(kw, list(articles_by_id.values()))
    linked_ids = {a["id"] for a in item_reviews(item)}
    candidates = [a for a in candidates if a["id"] not in linked_ids]
    if not candidates:
        return None
    # 日付の新しい順に並べた後、同じチェーンのものを優先する安定ソート
    candidates.sort(key=lambda a: a.get("date") or "", reverse=True)
    candidates.sort(key=lambda a: a.get("chain") != item.get("chain"))
    return kw["label"], candidates[:limit]


def esc(s) -> str:
    return html.escape(s or "", quote=True)


def has_asset(name: str) -> bool:
    return (DOCS / "assets" / name).is_file()


def load_all_products() -> list[dict]:
    items = []
    for chain in CHAIN_NAME:
        d = PRODUCTS / chain
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return items


def sort_date(item: dict) -> str:
    """並び順に使う日付(YYYY-MM-DD)。発売月だけの商品(サンヨー食品)はその月の1日として扱う。"""
    if item.get("launch_date"):
        return item["launch_date"]
    if item.get("launch_month"):
        return item["launch_month"] + "-01"
    return (item.get("first_seen_at") or "")[:10]


def fmt_date(iso: str | None) -> str:
    """2026-11-09 → 2026年11月9日"""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso or "")
    return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日" if m else ""


def launch_text(item: dict) -> str:
    """発売日の表示。日付があれば「2026年11月9日」、月だけなら「2026年11月」。"""
    if item.get("launch_date"):
        return fmt_date(item["launch_date"])
    lm = item.get("launch_month")
    m = re.match(r"^(\d{4})-(\d{2})$", lm or "")
    return f"{int(m.group(1))}年{int(m.group(2))}月" if m else ""


def start_month(item: dict) -> str | None:
    d = sort_date(item)
    return d[:7] if re.match(r"^\d{4}-\d{2}", d or "") else None


# 注意: item_url はサイトルートからの相対パス(先頭に / が無い)。**カードのhrefに使うときは先頭に "/" を付ける**。
# 付け忘れると、/companies/xxx/ や /archive/xxx/ のような階層のあるページで相対パスになって404になる。
def item_url(item: dict) -> str:
    return f"items/{item['chain']}-{item['product_id']}.html"


def fmt_price(item: dict) -> str:
    """希望小売価格の税別表示「259円（税別）」。税込には換算しない。数字が無ければ「オープンプライス」等の表記。"""
    p = item.get("price_excl_tax")
    if p is None:
        return item.get("price_label") or ""
    return f"{int(p):,}円（税別）"


def fmt_net(item: dict) -> str:
    """内容量「76g（麺量 60g）」。"""
    net = item.get("net_amount") or ""
    label = item.get("net_label")
    if net and label and "（" in net:
        a, b = net.split("（", 1)
        return f"{a}（{label} {b}"
    return net


def render_card(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])
    kcal = (item.get("nutrition") or {}).get("kcal")
    kcal_html = f'<span class="kcal">{kcal:g}kcal</span>' if kcal is not None else ""
    review_html = '<div class="review-badge">レビューあり</div>' if item_reviews(item) else ""
    launch = launch_text(item)
    return f"""<a class="item" href="/{esc(item_url(item))}" data-kcal="{kcal if kcal is not None else ''}"
   data-date="{esc(sort_date(item))}" data-chain="{esc(item['chain'])}">
  <div class="badge">{esc(chain_name)}</div>
  <div class="t">{esc(item['name'])}</div>
  <div class="price">{esc(fmt_price(item))}{kcal_html}</div>
  <div class="meta">{esc(launch + '発売') if launch else ''}</div>
  {review_html}
</a>"""


CSS = """
:root {
  --bg: #f4f7fb; --card: #ffffff; --text: #16202c; --muted: #5a6877;
  --accent: __ACCENT__; --line: #dde5ee;
__CHAIN_VARS_LIGHT__
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #0e1520; --card: #172130; --text: #e6edf5; --muted: #9aa8b8;
    --line: #29364a; --accent: __ACCENT_DARK__;
__CHAIN_VARS_DARK__ }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
  font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; line-height: 1.6; }
a { color: inherit; }
header { padding: 24px 16px 12px; max-width: 980px; margin: 0 auto; }
header h1 a { text-decoration: none; font-size: 22px;
  display: inline-flex; align-items: center; gap: 8px; }
/* ロゴは文字とほぼ同じ高さに揃える(96px画像を縮小して表示。姉妹サイトと同じ) */
header h1 img { width: 32px; height: 32px; }
header p { color: var(--muted); font-size: 13px; margin-top: 4px; }
nav.crumbs { font-size: 12px; color: var(--muted); margin-top: 8px; }
nav.crumbs a { text-decoration: none; color: var(--accent); }
.sites { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
.sites .lbl { font-size: 12px; color: var(--muted); }
.sites a { font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); text-decoration: none; }
main { max-width: 980px; margin: 0 auto; padding: 8px 16px 48px; }
h2 { font-size: 18px; padding-left: 10px; border-left: 4px solid var(--accent); margin: 24px 0 12px; }
.sort-bar { display: flex; gap: 8px; margin: 8px 0 16px; flex-wrap: wrap; align-items: center; }
.sort-bar select, .sort-bar input { font-size: 13px; padding: 5px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--card); color: var(--text); font-family: inherit; }
.chain-group h3 { font-size: 15px; margin: 18px 0 8px; padding-left: 8px; border-left: 4px solid var(--line); }
.grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.item { display: block; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px; text-decoration: none; }
.item:hover { border-color: var(--accent); }
.item .badge { display: inline-block; font-size: 10px; font-weight: 700; color: #fff;
  border-radius: 4px; padding: 1px 6px; margin-bottom: 6px; }
.item .t { font-size: 14px; font-weight: 600; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.6em; }
.item .price { margin-top: 6px; font-size: 13px; }
.item .price .kcal { margin-left: 8px; color: var(--muted); }
.item .meta { font-size: 11px; color: var(--muted); margin-top: 4px; }
.monthlist { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.monthlist a { text-decoration: none; padding: 10px 14px; background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }
.monthlist a:hover { border-color: var(--accent); }
.monthlist .n { color: var(--muted); font-size: 12px; margin-left: 8px; }
.chainlist { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.chainlist a { text-decoration: none; padding: 8px 16px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); font-size: 13px; }
.detail { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 20px; margin-top: 12px; }
.detail h1 { font-size: 20px; margin-bottom: 8px; }
.chain-badge { display: inline-block; font-size: 11px; font-weight: 700; color: #fff;
  border-radius: 4px; padding: 2px 8px; }
.detail .price { font-size: 18px; font-weight: 700; margin: 8px 0; }
.detail .price small { font-size: 12px; font-weight: 400; color: var(--muted); }
.detail table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
.detail table th, .detail table td { border-bottom: 1px solid var(--line); text-align: left;
  padding: 6px 4px; }
.detail table th { color: var(--muted); font-weight: 500; width: 40%; }
.detail .tablewrap { overflow-x: auto; }
.detail table.multi th, .detail table.multi td { width: auto; text-align: right; white-space: nowrap; }
.detail table.multi th:first-child { text-align: left; }
.detail table.pv { max-width: 320px; }
.detail table.facts { max-width: 420px; margin-bottom: 4px; }
.detail h3 { margin-top: 16px; font-size: 14px; }
.detail details { margin-top: 10px; font-size: 13px; }
.detail details summary { cursor: pointer; color: var(--accent); }
.detail blockquote { border-left: 3px solid var(--accent); margin: 12px 0; padding: 8px 14px;
  color: var(--text); background: var(--bg); border-radius: 4px; font-size: 14px; }
.detail .source { font-size: 12px; color: var(--muted); margin-top: 4px; }
.detail .warn { font-size: 12px; color: var(--muted); margin-top: 2px; }
.detail .note { font-size: 12px; color: var(--muted); margin-top: 6px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
.tags span { font-size: 11px; border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px; }
.searchbox { margin: 16px 0; }
.searchbox input { width: 100%; font-size: 14px; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--text); font-family: inherit; }
footer { max-width: 980px; margin: 0 auto; padding: 16px; color: var(--muted); font-size: 12px;
  border-top: 1px solid var(--line); }
.about { max-width: 980px; margin: 40px auto 0; padding: 20px 16px 0; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 13px; line-height: 1.9; }
.about h2 { font-size: 14px; border-left-width: 3px; margin-bottom: 8px; color: var(--text); }
.about p { margin-top: 8px; }
.about a { color: var(--accent); }
.empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
.item .review-badge { display: inline-block; margin-top: 4px; font-size: 10px; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 4px; padding: 0 5px; }
.review-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px; margin-top: 8px; }
.review-card { display: block; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden; text-decoration: none; color: inherit; }
.review-card:hover { border-color: var(--accent); }
.review-card img { width: 100%; height: 110px; object-fit: cover; display: block; background: var(--line); }
.review-card .rc-body { padding: 8px 10px; }
.review-card .rc-t { font-size: 12.5px; font-weight: 600; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4; }
.review-card .rc-date { color: var(--muted); font-size: 11px; margin-top: 4px; }
.detail .review-grid { margin-top: 4px; }
@media (max-width: 480px) {
  .review-grid { grid-template-columns: repeat(2, 1fr); }
}
/* 手動のAdSense広告枠(Auto adsは使わない)。設定が空の間は何も出力しない。
   width:100%指定必須: グリッドやflexの中で幅が0に潰れるとAdSenseが配信に失敗する。
   overflow:hiddenは付けない。未充填だけ畳む */
.ad-slot { width: 100%; max-width: 960px; margin: 16px auto; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px; text-align: center; }
.ad-slot:has(ins[data-ad-status="unfilled"]) { display: none; }
/* カード一覧(.grid)の中に差し込まれる枠は、カード1枚ぶんのマス(列幅)を使う */
.grid > .ad-slot { max-width: none; margin: 0; }
"""


def build_css() -> str:
    accent = CONFIG.get("site_accent") or "#6A3FA0"
    accent_dark = CONFIG.get("site_accent_dark") or "#C7A6EE"
    light = "\n".join(f"  --{c['slug']}: {c['color']};" for c in CHAINS)
    dark = "\n".join(f"    --{c['slug']}: {c.get('color_dark') or c['color']};" for c in CHAINS)
    per_chain = "".join(
        f'.chain-group[data-chain="{c["slug"]}"] h3 {{ border-color: var(--{c["slug"]}); }}\n'
        f'.item[data-chain="{c["slug"]}"] .badge {{ background: var(--{c["slug"]}); }}\n'
        for c in CHAINS)
    # ダークモードではチェーン色を明るくしているので、バッジの文字は白ではなく暗い色にして読めるようにする
    badge_dark = "@media (prefers-color-scheme: dark) { .item .badge, .chain-badge { color: #0e1520; } }\n"
    return (CSS.replace("__ACCENT__", accent).replace("__ACCENT_DARK__", accent_dark).replace("__CHAIN_VARS_LIGHT__", light)
            .replace("__CHAIN_VARS_DARK__", dark) + per_chain + badge_dark)


# カード一覧の中に差し込む広告枠の間隔と、1ページ全体での上限(ヘッダー下の1枠は別)。
# SORT_JSがこの値を使うので、変えるとJSも変わる(コンビニポチと同じ仕組み)
ADS_EVERY = 12
ADS_MAX_PER_GRID = 3


def render_ad_slot() -> str:
    """手動設置のディスプレイ広告ユニット。

    adsense_client_id / adsense_ad_slot のどちらかが未設定なら何も出さない。
    サーバー側でHTMLに置くのはヘッダー下と商品ページの枠だけ。カード一覧(.grid)の
    中の枠は、並び替え・絞り込みのたびに位置が変わるのでSORT_JSが差し込む
    (render_ad_template参照)。push(読み込み)は枠ごとのインラインscriptではなく、
    ページ下部のAD_JS(syncAds)がまとめて行う。
    data-ad-formatはrectangle(autoだと縦長の広告が来る)
    """
    ad_slot = CONFIG.get("adsense_ad_slot", "")
    adsense_id = CONFIG.get("adsense_client_id", "")
    if not ad_slot or not adsense_id:
        return ""
    return f"""<div class="ad-slot">
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="{esc(adsense_id)}"
     data-ad-slot="{esc(ad_slot)}"
     data-ad-format="rectangle"
     data-full-width-responsive="true"></ins>
</div>"""


def render_ad_template() -> str:
    """SORT_JSがカード一覧に差し込む広告枠の雛形。広告未設定なら空(=JSは何も差し込まない)。

    <template>の中身はDOMに入らないので、これ自体はpushされない。
    JS無効・検索エンジンにはカードだけが全件見える"""
    ad = render_ad_slot()
    return f'<template id="ad-template">{ad}</template>' if ad else ""


# 広告のpush(AdSenseへ読み込みを依頼する)をまとめて行う。広告が設定されているページだけに入る。
# 一度pushした<ins>は再利用できないので、印(dataset.pushed)を付けて二重pushを防ぐ。
# 隠れている枠は幅0で失敗し、あとで表示しても二度と入らないので、adsbygoogleクラスを
# 外してAdSenseの対象から除外しておく(次のsyncAdsで表示中なら戻してpushする)
AD_JS = """
<script>
function syncAds() {
  document.querySelectorAll("ins[data-ad-slot]").forEach(function (ins) {
    if (ins.dataset.pushed) return;
    if (ins.offsetParent === null) {
      ins.classList.remove("adsbygoogle");
      return;
    }
    ins.classList.add("adsbygoogle");
    ins.dataset.pushed = "1";
    (window.adsbygoogle = window.adsbygoogle || []).push({});
  });
}
syncAds();
</script>
"""


def updated_label(now: datetime.datetime | None = None) -> str:
    """トップの「最終更新: 2026年10月14日 07:37」。電書・家電・林檎ポチと同じ書式(日本時間)。
    トップページだけに出す(全ページに入れると、毎日すべてのページのファイルが変わってしまう)。"""
    now = now or datetime.datetime.now(JST)
    return f"最終更新: {now.strftime('%Y年%m月%d日 %H:%M')}"


def page_shell(title: str, description: str, body: str, canonical: str, extra_head: str = "",
               header_ad: bool = False, header_note: str = "") -> str:
    site_url = CONFIG.get("site_url", "")
    # トップページのtitleは、サイト名のあとに説明を添える(姉妹サイトと同じ「名前｜説明」の形)
    page_title = (f'{esc(title)}｜{esc(CONFIG["site_title"])}' if title != CONFIG["site_title"]
                  else esc(CONFIG.get("top_title") or title))

    ga_id = CONFIG.get("ga_measurement_id")
    ga_tag = ""
    if ga_id:
        ga_tag = (
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={esc(ga_id)}"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{esc(ga_id)}');</script>"
        )

    adsense_id = CONFIG.get("adsense_client_id", "")
    adsense_tag = (
        '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
        f'adsbygoogle.js?client={esc(adsense_id)}" crossorigin="anonymous"></script>'
        if adsense_id else ""
    )
    header_ad_html = render_ad_slot() if header_ad else ""
    ad_js = AD_JS if adsense_id and CONFIG.get("adsense_ad_slot") else ""

    # 見出しの左に置くポチシリーズ共通のアイコン。画像が置かれていなければ何も出さない
    logo_img = (
        '<img src="/assets/logo.png" alt="" width="32" height="32">'
        if has_asset("logo.png") else ""
    )
    icon_tags = []
    if has_asset("favicon.png"):
        # Googleの検索結果に出るファビコンは48pxの倍数が必要(32pxだと地球儀になる。
        # コンビニポチで2026-10-07に判明)。favicon.png は96px。sizes を明示する
        icon_tags.append('<link rel="icon" type="image/png" sizes="96x96" href="/assets/favicon.png">')
    if has_asset("apple-touch-icon.png"):
        icon_tags.append('<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">')
    ogp_tags = []
    if has_asset("ogp.jpg"):
        ogp_tags = [
            f'<meta property="og:image" content="{esc(site_url)}assets/ogp.jpg">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]

    related = CONFIG.get("related_sites") or []
    links = "\n".join(
        f'<a href="{esc(s["url"])}">{esc(s["name"])}'
        + (f'<span class="lbl"> {esc(s["desc"])}</span>' if s.get("desc") else "")
        + "</a>"
        for s in related
    )
    related_html = f'<nav class="sites"><span class="lbl">関連サイト</span>\n{links}\n</nav>' if related else ""

    official_links = " ／ ".join(
        f'<a href="{esc(CHAIN_OFFICIAL[c["slug"]]["url"])}">{esc(c["name"])}</a>'
        for c in CHAINS if c["slug"] in CHAIN_OFFICIAL)
    policy_url = CONFIG.get("policy_url", "")
    policy_link = (
        f'｜ <a href="{esc(policy_url)}">メディアポリシー</a>\n' if policy_url else ""
    )

    out = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
{ga_tag}
{adsense_tag}
{chr(10).join(icon_tags)}
<meta property="og:type" content="website">
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{esc(CONFIG['site_title'])}">
<meta property="og:locale" content="ja_JP">
{chr(10).join(ogp_tags)}
<link rel="alternate" type="application/rss+xml" title="RSS" href="/rss.xml">
<style>{build_css()}</style>
{extra_head}
</head>
<body>
<header>
<h1><a href="/">{logo_img}{esc(CONFIG["site_title"])}</a></h1>
<p>{esc(CONFIG["site_description"])}{(" ｜ " + esc(header_note)) if header_note else ""}</p>
</header>
{header_ad_html}
<main>
{body}
</main>
<footer>
価格は各社の希望小売価格（税別）です。最新の情報は各社公式サイトでご確認ください。
当サイトは{esc(CHAIN_LIST_TEXT)}各社とは関係のない非公式サイトです。記録しているのは事実情報だけで、画像・商品説明文・原材料名は載せていません。
公式サイト： {official_links}
{policy_link}｜ <a href="/rss.xml">RSS</a> ｜ <a href="/archive/">発売月別アーカイブ</a>
{related_html}
</footer>
{ad_js}
</body>
</html>
"""
    if adsense_id:
        # AdSenseダッシュボードではvignette(全画面)広告をサブドメイン単位で無効化
        # できないため、リンクごとにdata-google-vignette="false"を付ける
        out = re.sub(r'<a (?![^>]*data-google-vignette)', '<a data-google-vignette="false" ', out)
    return out


# 広告の配置計算。DOMに触れない純粋関数(テストからnodeで単体実行する)。
# counts: グリッドごとの「見えているカード数」(見えていないグリッドは0)
# 返り値: グリッドごとの、広告を差し込む位置(その位置のカードの直前)の配列
AD_PLAN_JS = """
function computeAdPlan(counts, every, max) {
  var eligible = [];
  counts.forEach(function (n, i) { if (n > every) eligible.push(i); });
  var chosen = eligible;
  if (eligible.length > max) {
    chosen = [];
    for (var k = 0; k < max; k++) chosen.push(eligible[Math.floor(k * eligible.length / max)]);
  }
  var perGrid = Math.max(1, Math.floor(max / (chosen.length || 1)));
  var left = max;
  var plan = counts.map(function () { return []; });
  chosen.forEach(function (gi) {
    var placed = 0;
    for (var i = every; i < counts[gi] && left > 0 && placed < perGrid; i += every) {
      plan[gi].push(i);
      left--;
      placed++;
    }
  });
  return plan;
}
"""



SORT_JS = """
<script>
__AD_PLAN__(function () {
  // チェーンごとのグループ(.chain-group)内でだけ並び替える。絞り込みはカード単位・グループ単位で隠す
  var grids = Array.prototype.slice.call(document.querySelectorAll('[data-sortable]'));
  if (!grids.length) return;
  var select = document.getElementById('sort-select');
  var chainSel = document.getElementById('chain-filter');
  // 広告枠。サーバー側はグリッドに枠を置かず、並び替え・絞り込みのたびにここで差し込み直す。
  // 雛形(#ad-template)が無ければ(広告未設定・非対応ブラウザ)何もしない
  var AD_EVERY = __ADS_EVERY__;
  var AD_MAX = __ADS_MAX__;
  var adTpl = document.getElementById('ad-template');
  function placeAds() {
    var proto = adTpl && adTpl.content && adTpl.content.firstElementChild;
    if (!proto) return;
    // 広告枠(.ad-slot)は、表示済みの広告を読み込み直さないよう、DOMから外さない。
    // 並べ替えで動かすのはカード(.item)だけ。枠をDOMから外すのは、下の「余った枠を捨てる」1か所だけ。
    // 必要な枠数が今の枠数と同じなら枠はそのまま、足りない分だけ新規に作る(pushは新しい枠だけ)。
    // 見えていないグリッドの枠は触らず残しておき、再表示されたら再利用する
    var allItems = function (g) { return Array.prototype.slice.call(g.querySelectorAll('.item')); };
    // 販売状況の絞り込みで隠れたカードは数えない
    var visibleItems = function (g) {
      return allItems(g).filter(function (el) { return el.style.display !== 'none'; });
    };
    var shown = grids.map(function (g) { return g.offsetParent !== null; });
    var counts = grids.map(function (g, i) { return shown[i] ? visibleItems(g).length : 0; });
    var plan = computeAdPlan(counts, AD_EVERY, AD_MAX);
    grids.forEach(function (grid, gi) {
      if (!shown[gi]) return;
      var pos = plan[gi];
      var slots = Array.prototype.filter.call(grid.children, function (el) {
        return el.classList.contains('ad-slot');
      });
      while (slots.length > pos.length) {
        var extra = slots.pop();
        extra.parentNode.removeChild(extra);
      }
      while (slots.length < pos.length) {
        var fresh = proto.cloneNode(true);
        grid.appendChild(fresh);
        slots.push(fresh);
      }
      if (!slots.length) return;
      var put = function (el, ref) {
        if (el.classList.contains('ad-slot')) return;
        if (ref) grid.insertBefore(el, ref); else grid.appendChild(el);
      };
      var vis = visibleItems(grid);
      var hidden = allItems(grid).filter(function (el) { return vis.indexOf(el) < 0; });
      var from = 0;
      pos.forEach(function (p, j) {
        for (var k = from; k < p; k++) put(vis[k], slots[j]);
        from = p;
      });
      for (var k = from; k < vis.length; k++) put(vis[k], null);
      hidden.forEach(function (el) { put(el, null); });
    });
  }
  function apply() {
    var mode = select ? select.value : 'date';
    var chain = chainSel ? chainSel.value : 'all';
    grids.forEach(function (grid) {
      var items = Array.prototype.slice.call(grid.querySelectorAll('.item'));
      items.sort(function (a, b) {
        if (mode === 'kcal') {
          return parseFloat(b.dataset.kcal || '-1') - parseFloat(a.dataset.kcal || '-1');
        }
        return (b.dataset.date || '').localeCompare(a.dataset.date || '');
      });
      var visible = 0;
      items.forEach(function (el) {
        grid.appendChild(el);
        var show = chain === 'all' || el.dataset.chain === chain;
        el.style.display = show ? '' : 'none';
        if (show) visible++;
      });
      var cnt = document.getElementById('item-count');
      if (cnt) cnt.textContent = visible;
    });
    placeAds();
    // 初回はページ末尾のAD_JSがpushするので、まだ無ければ何もしない
    if (window.syncAds) window.syncAds();
  }
  if (select) select.addEventListener('change', apply);
  if (chainSel) chainSel.addEventListener('change', apply);
  apply();
})();
</script>
""".replace("__AD_PLAN__", AD_PLAN_JS).replace("__ADS_EVERY__", str(ADS_EVERY)).replace("__ADS_MAX__", str(ADS_MAX_PER_GRID))




def render_flat(items: list[dict], sortable: bool = True) -> str:
    """商品を発売日の新しい順に並べた1本の一覧(見出しで分けない)。広告枠はSORT_JSが差し込む。"""
    attr = " data-sortable" if sortable else ""
    ordered = sorted(items, key=lambda it: (sort_date(it), it.get("name") or ""), reverse=True)
    cards = "\n".join(render_card(it) for it in ordered)
    return f'<div class="grid"{attr}>\n{cards}\n</div>'


SEARCH_JS = """
<script>
(function () {
  var box = document.getElementById('search-input');
  var results = document.getElementById('search-results');
  if (!box || !results) return;
  var index = null;
  box.addEventListener('input', function () {
    var q = box.value.trim();
    if (!q) { results.innerHTML = ''; return; }
    function render() {
      var lower = q.toLowerCase();
      var hits = index.filter(function (it) {
        return it.n.toLowerCase().indexOf(lower) !== -1;
      }).slice(0, 50);
      results.innerHTML = hits.map(function (it) {
        return '<a class="item" data-chain="' + it.c + '" href="' + it.u + '">' +
          '<div class="badge">' + it.cn + '</div><div class="t">' + it.n +
          '</div><div class="meta">' + (it.d || '') + '</div></a>';
      }).join('');
    }
    if (index) { render(); return; }
    fetch('/search-index.json').then(function (r) { return r.json(); }).then(function (data) {
      index = data; render();
    });
  });
})();
</script>
"""


def build_search_index(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda it: sort_date(it), reverse=True)
    return [
        {
            "n": it["name"],
            "u": "/" + item_url(it),
            "c": it["chain"],
            "cn": CHAIN_NAME.get(it["chain"], it["chain"]),
            "d": (launch_text(it) + "発売") if launch_text(it) else "",
        }
        for it in ordered
    ]


def filter_bar() -> str:
    """並び替えと会社の絞り込み。[data-sortable]の外に置く。"""
    chain_opts = "".join(f'<option value="{esc(c["slug"])}">{esc(c["name"])}</option>' for c in CHAINS)
    return f"""<div class="sort-bar">
<label>並び替え: <select id="sort-select"><option value="date">発売日順</option>
<option value="kcal">カロリー順</option></select></label>
<label>絞り込み: <select id="chain-filter"><option value="all">すべて</option>{chain_opts}</select></label>
</div>"""


def render_grid_page(title: str, description: str, items: list[dict], canonical: str,
                     review_chain: str | None = None, empty_text: str = "商品がありません。") -> str:
    reviews_section = render_review_cards("ネタフルの最新レビュー", latest_reviews(review_chain, 5))
    if not items:
        body = f"<h2>{esc(title)}</h2><p class='empty'>{esc(empty_text)}</p>\n{reviews_section}"
        return page_shell(title, description, body, canonical)
    body = f"""<h2>{esc(title)} (<span id="item-count">{len(items)}</span>件)</h2>
{filter_bar()}
{render_flat(items)}
{render_ad_template()}
{SORT_JS}
{reviews_section}"""
    return page_shell(title, description, body, canonical, header_ad=True)


def render_about() -> str:
    about = CONFIG.get("about") or []
    if not about:
        return ""
    link = '<a href="https://netaful.jp/">ネタフル</a>'
    paras = "\n".join(f"<p>{esc(x).replace('{{ネタフル}}', link)}</p>" for x in about)
    return f'<section class="about"><h2>{esc(CONFIG["site_title"])}について</h2>\n{paras}\n</section>'


def render_top(items: list[dict]) -> str:
    """トップ: 発売日の新しい順に並べた1本の一覧。見出しで分けず、未来の発売日の商品も同じ一覧の先頭に並ぶ。"""
    site_url = CONFIG.get("site_url", "")
    search_html = """<div class="searchbox"><input id="search-input" type="search"
placeholder="商品名で検索（例: カップヌードル、どん兵衛、焼そば）"></div>
<div class="grid" id="search-results"></div>"""
    company_links = "\n".join(
        f'<a href="/companies/{c["slug"]}/">{esc(c["name"])}</a>' for c in CHAINS
    )
    if items:
        list_section = f"""<h2>カップ麺の新商品 (<span id="item-count">{len(items)}</span>件)</h2>
{filter_bar()}
{render_flat(items)}
{render_ad_template()}
{SORT_JS}"""
    else:
        list_section = "<h2>カップ麺の新商品</h2><p class='empty'>まだ記録がありません。</p>"
    body = f"""<div class="chainlist">
{company_links}
</div>
<h2>商品名で検索</h2>
{search_html}
{SEARCH_JS}
{list_section}
{render_review_cards("ネタフルの最新レビュー", latest_reviews(None, 6))}
<p><a href="/archive/">発売月ごとのアーカイブを見る →</a></p>
{render_about()}"""
    # 検索結果のサイト名。サブドメインは何も指定しないと親ドメインの「ネタフル」と表示されるので、
    # WebSite 構造化データでサイト名を伝える(コンビニポチで2026-10-07に判明した対処)
    site_name_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": CONFIG["site_title"],
        "alternateName": ["Cupmen Pochi", "カップ麺ポチ"],
        "url": site_url,
    }, ensure_ascii=False)
    return page_shell(
        CONFIG["site_title"], CONFIG["site_description"], body, site_url,
        extra_head=f'<script type="application/ld+json">{site_name_ld}</script>',
        header_ad=True,
        header_note=updated_label(),
    )


def month_label(m: str) -> str:
    return f"{m[:4]}年{int(m[5:7])}月"


def render_archive_index(items: list[dict]) -> str:
    by_month: dict[str, int] = {}
    for it in items:
        m = start_month(it)
        if m:
            by_month[m] = by_month.get(m, 0) + 1
    if not by_month:
        body = "<h2>発売月別アーカイブ</h2><p class='empty'>まだ記録がありません。</p>"
    else:
        rows = "\n".join(
            f'<a href="/archive/{m}/">{month_label(m)}に発売<span class="n">{n}件</span></a>'
            for m, n in sorted(by_month.items(), reverse=True)
        )
        body = f"""<h2>発売月別アーカイブ ({len(items)}件)</h2>
<div class="monthlist">
{rows}
</div>"""
    return page_shell("発売月別アーカイブ", f"{CHAIN_LIST_TEXT}のカップ麺の新商品を、発売月ごとにまとめたアーカイブ",
                      body, CONFIG.get("site_url", "") + "archive/")


def render_companies_top() -> str:
    links = "\n".join(f'<a href="/companies/{c["slug"]}/">{esc(c["name"])}</a>' for c in CHAINS)
    body = f'<h2>メーカー一覧</h2><div class="chainlist">{links}</div>'
    return page_shell("メーカー一覧", f"{CHAIN_LIST_TEXT}のカップ麺の新商品一覧", body,
                      CONFIG.get("site_url", "") + "companies/")


NUTRITION_LABELS = [
    ("kcal", "熱量", "kcal"), ("protein_g", "たんぱく質", "g"),
    ("fat_g", "脂質", "g"), ("carbs_g", "炭水化物", "g"),
    ("sugar_g", "　糖類", "g"), ("fiber_g", "　食物繊維", "g"),
    ("salt_g", "食塩相当量", "g"),
]
# 「ほか」の項目がこの数以下ならそのまま表に並べ、超えたら折りたたむ
OTHER_INLINE_MAX = 5


def _nutrition_rows(nut: dict) -> list[tuple[str, str]]:
    rows = []
    for key, label, unit in NUTRITION_LABELS:
        v = nut.get(key)
        if v is not None:
            s = f"{v:g}"
            if unit == "g" and "." not in s:
                s += ".0"  # 公式の表記(8.0g)に合わせる
            rows.append((label, f"{s}{unit}"))
    return rows


def link_html(label: str, url: str | None) -> str:
    if not url:
        return esc(label)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}</a>'


def nutrition_note_html(item: dict) -> str:
    """kcalしか公表されていないという断り書きは表を見れば分かるので出さない。"""
    note = item.get("nutrition_note") or ""
    if not note or ("kcal" in note and "のみ" in note):
        return ""
    return f"<p class='note'>{esc(note.rstrip('。'))}。</p>"


def render_nutrition(item: dict) -> str:
    nut = item.get("nutrition")
    if not nut:
        src = item.get("nutrition_source") or {}
        link = f"公式サイトでご確認ください：{link_html(src.get('label') or '公式サイト', src.get('url'))}" if src.get("url") else ""
        return f"<h3>栄養成分</h3><p class='note'>栄養成分は未取得です。{link}</p>"
    variants = item.get("nutrition_variants") or []
    note = nutrition_note_html(item)
    if len(variants) > 1:
        only_kcal = all({k for k, v in (x["nutrition"] or {}).items() if v is not None and k != "other"} <= {"kcal"}
                        for x in variants)
        if only_kcal:
            rows = "".join(f"<tr><th>{esc(x.get('label') or '標準')}</th><td>{x['nutrition']['kcal']:g}kcal</td></tr>"
                           for x in variants)
            return (f"<h3>栄養成分</h3><table>{rows}</table>{note}")
        parsed = [(v.get("label") or "標準", _nutrition_rows(v["nutrition"])) for v in variants]
        row_labels: list[str] = []
        for _, rows in parsed:
            for lb, _v in rows:
                if lb not in row_labels:
                    row_labels.append(lb)
        head = "<tr><th></th>" + "".join(f"<th>{esc(lb)}</th>" for lb, _ in parsed) + "</tr>"
        body_rows = ""
        for rl in row_labels:
            cells = "".join(f"<td>{esc(dict(rows).get(rl, '－'))}</td>" for _, rows in parsed)
            body_rows += f"<tr><th>{esc(rl)}</th>{cells}</tr>"
        return (f"<h3>栄養成分</h3><div class='tablewrap'><table class='multi'>{head}{body_rows}</table></div>"
                f"{note}")
    trs = "".join(f"<tr><th>{esc(label)}</th><td>{esc(val)}</td></tr>" for label, val in _nutrition_rows(nut))
    other = nut.get("other") or {}
    inline_rows, details = "", ""
    if other:
        rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in other.items())
        if len(other) <= OTHER_INLINE_MAX:
            inline_rows = rows
        else:
            details = (f"<details><summary>そのほかの栄養成分（{len(other)}項目）を見る</summary>"
                       f"<table>{rows}</table></details>")
    label = item.get("nutrition_label") or (variants[0].get("label") if variants else None) or ""
    head = f"<h3>栄養成分{('（' + esc(label) + '）') if label else ''}</h3>"
    return (f"{head}<table>{trs}{inline_rows}</table>{details}{note}")



def render_allergens(item: dict) -> str:
    a = item.get("allergens")
    if a is None:
        url = (CHAIN_OFFICIAL.get(item["chain"]) or {}).get("url")
        src = item.get("official_url") or url
        return ("<h3>アレルゲン</h3><p class='note'>アレルゲンは公式サイトでご確認ください："
                f"{link_html('公式の商品ページ', src)}</p>") if src else ""
    contains = a.get("contains") or []
    text = "、".join(contains) if contains else "公式の表で「含む」とされた品目はありません"
    return f"<h3>アレルゲン</h3><p class='tags'><strong>含む（特定原材料等28品目）：</strong>{esc(text)}</p>"


def seo_title(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])
    nut = item.get("nutrition") or {}
    facts = "発売日・カロリー・価格" if nut.get("kcal") is not None else "発売日・価格・内容量"
    return f"{chain_name}「{item['name']}」の{facts}"


def seo_description(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])
    nut = item.get("nutrition") or {}
    bits = [f"{chain_name}のカップ麺「{item['name']}」"]
    if launch_text(item):
        bits.append(f"{launch_text(item)}発売")
    if nut.get("kcal") is not None:
        bits.append(f"{nut['kcal']:g}kcal")
    price = fmt_price(item)
    if price:
        bits.append(price)
    return ("、".join(bits) + "。")[:200]


def render_notes(item: dict) -> str:
    """商品ごとに読む意味がある補足だけ。サイト全体の説明はフッターとaboutにある。"""
    notes = []
    if item.get("launch_month") and not item.get("launch_date"):
        notes.append("発売日は月までしか公表されていないため、月で表示しています。")
    if item.get("ec_only"):
        notes.append("公式オンラインショップ限定の商品です。")
    return "".join(f"<p class='note'>{n}</p>" for n in notes)


def render_sources(item: dict) -> str:
    """公式ページへのリンクを、出典として出す。"""
    chain = item["chain"]
    chain_name = CHAIN_NAME.get(chain, chain)
    off = CHAIN_OFFICIAL.get(chain, {})
    links = []
    if item.get("official_url"):
        links.append(link_html("公式の商品ページ", item["official_url"]))
    if off.get("url"):
        links.append(link_html(f"{chain_name}公式サイト", off["url"]))
    fetched = (item.get("first_seen_at") or "")[:10]
    return (f"<p class='source'>出典：{esc(CHAIN_SOURCE.get(chain, chain_name + '公式サイト'))}（{esc(fetched)}時点）</p>"
            f"<p class='source'>{' ・ '.join(links)}</p>")


def render_facts(item: dict) -> str:
    rows = []
    if launch_text(item):
        rows.append(("発売日" if item.get("launch_date") else "発売月", launch_text(item)))
    if item.get("sales_area"):
        rows.append(("発売地域", item["sales_area"]))
    if fmt_net(item):
        rows.append(("内容量", fmt_net(item)))
    if item.get("jan"):
        rows.append(("JANコード", item["jan"]))
    if not rows:
        return ""
    trs = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
    return f"<table class='facts'>{trs}</table>"


def render_item_page(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])

    reviews_html = ""
    reviews = item_reviews(item)
    if reviews:
        reviews_html = f"""<h3>ネタフルのレビュー</h3>
<div class="review-grid">
{chr(10).join(render_review_card(a) for a in reviews)}
</div>"""

    related_html = ""
    related = select_related_articles(item, ARTICLES_BY_ID)
    if related:
        label, related_articles_list = related
        related_html = f"""<h3>関連するネタフルの記事（{esc(label)}）</h3>
<div class="review-grid">
{chr(10).join(render_review_card(a) for a in related_articles_list)}
</div>"""

    # 直接のレビューもキーワードの関連記事も無い商品には、その会社の最新レビューを出す
    if not reviews and not related:
        recent = latest_reviews(item["chain"], 4)
        if recent:
            related_html = f"""<h3>{esc(chain_name)}のネタフル最新レビュー</h3>
<div class="review-grid">
{chr(10).join(render_review_card(a) for a in recent)}
</div>"""

    nutrition_html = render_nutrition(item)
    allergen_html = render_allergens(item)
    has_extras = bool(item.get("nutrition") or item.get("allergens"))
    mid_ad = render_ad_slot() if has_extras else ""
    bottom_ad = "" if has_extras else render_ad_slot()

    price = fmt_price(item)
    price_html = esc(price) if price else "価格は公式サイトでご確認ください"
    tags = "<div class='tags'><span>生産終了</span></div>" if item.get("discontinued") else ""
    body = f"""<nav class="crumbs"><a href="/">トップ</a> &gt; <a href="/companies/{esc(item['chain'])}/">{esc(chain_name)}</a></nav>
<div class="detail">
<div class="chain-badge" style="background:var(--{esc(item['chain'])})">{esc(chain_name)}</div>
<h1>{esc(item['name'])}</h1>
{tags}
<p class="price">{price_html}</p>
{render_facts(item)}
{nutrition_html}
{allergen_html}
{mid_ad}
{reviews_html}
{related_html}
<h3>補足と出典</h3>
{render_notes(item)}
{render_sources(item)}
{bottom_ad}
</div>"""
    canonical = CONFIG.get("site_url", "") + item_url(item)
    return page_shell(seo_title(item), seo_description(item), body, canonical)


# AIの学習データ集め専用のクローラーは断る。商品説明は各社公式サイトからの引用なので、
# 引用元への配慮として学習用にまとめて持っていかれるのは避ける。
# 検索やAI検索の表示に使うクローラー(Googlebot, Bingbot, OAI-SearchBot, ChatGPT-User,
# PerplexityBot 等)は集客の入口なので止めない。
AI_TRAINING_BOTS = [
    "GPTBot", "CCBot", "Google-Extended", "Applebot-Extended", "anthropic-ai",
    "ClaudeBot", "Bytespider", "meta-externalagent", "cohere-training-data-crawler",
    "Diffbot", "Omgilibot",
]


def generate_robots() -> str:
    lines = [f"User-agent: {bot}" for bot in AI_TRAINING_BOTS]
    lines += ["Disallow: /", "", "User-agent: *", "Allow: /", "",
              f"Sitemap: {CONFIG.get('site_url', '')}sitemap.xml", ""]
    return "\n".join(lines)


def generate_rss(items: list[dict]) -> str:
    """新しく記録したメニューのRSS。"""
    site_url = CONFIG.get("site_url", "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    recent = sorted(items, key=lambda it: it.get("first_seen_at") or "", reverse=True)[
        : CONFIG.get("rss_max_items", 100)]
    entries = []
    for it in recent:
        chain_name = CHAIN_NAME.get(it["chain"], it["chain"])
        title = f"【{chain_name}】{it['name']}"
        link = f"{site_url}{item_url(it)}"
        guid = f"{it['chain']}-{it['product_id']}"
        try:
            pub = datetime.datetime.fromisoformat(it.get("first_seen_at"))
            pub_html = "\n<pubDate>" + pub.strftime("%a, %d %b %Y %H:%M:%S %z") + "</pubDate>"
        except (TypeError, ValueError):
            pub_html = ""
        entries.append(
            f"""<item>
<title>{esc(title)}</title>
<link>{esc(link)}</link>
<guid isPermaLink="false">{esc(guid)}</guid>
<category>{esc(chain_name)}</category>{pub_html}
</item>"""
        )
    items_xml = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>{esc(CONFIG["site_title"])}</title>
<link>{esc(site_url)}</link>
<description>{esc(CONFIG["site_description"])}</description>
<lastBuildDate>{now}</lastBuildDate>
{items_xml}
</channel>
</rss>
"""



def generate_sitemap(items: list[dict], months: list[str]) -> str:
    site_url = CONFIG.get("site_url", "")
    today = datetime.datetime.now(JST).date().isoformat()
    urls = [site_url, site_url + "archive/", site_url + "companies/"]
    for c in CHAINS:
        urls.append(f"{site_url}companies/{c['slug']}/")
    for m in months:
        urls.append(f"{site_url}archive/{m}/")
    for it in items:
        urls.append(site_url + item_url(it))
    body = "\n".join(
        f"<url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>" for u in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""


def main() -> int:
    items = load_all_products()
    DOCS.mkdir(parents=True, exist_ok=True)

    (DOCS / "index.html").write_text(render_top(items), encoding="utf-8")

    # 発売月別アーカイブ。0件の月のページは作らない
    months = sorted({m for it in items if (m := start_month(it))})
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "archive" / "index.html").write_text(render_archive_index(items), encoding="utf-8")
    for m in months:
        month_items = [it for it in items if start_month(it) == m]
        mdir = DOCS / "archive" / m
        mdir.mkdir(parents=True, exist_ok=True)
        page = render_grid_page(
            f"{month_label(m)}に発売", f"{month_label(m)}に発売された{CHAIN_LIST_TEXT}のカップ麺の新商品一覧",
            month_items, CONFIG.get("site_url", "") + f"archive/{m}/",
        )
        (mdir / "index.html").write_text(page, encoding="utf-8")
    stale_months = 0
    for mdir in (DOCS / "archive").iterdir():
        if mdir.is_dir() and mdir.name not in set(months):
            shutil.rmtree(mdir)
            stale_months += 1

    (DOCS / "companies").mkdir(parents=True, exist_ok=True)
    (DOCS / "companies" / "index.html").write_text(render_companies_top(), encoding="utf-8")
    for c in CHAINS:
        cdir = DOCS / "companies" / c["slug"]
        cdir.mkdir(parents=True, exist_ok=True)
        company_items = [it for it in items if it["chain"] == c["slug"]]
        page = render_grid_page(
            f"{c['name']}のカップ麺の新商品",
            f"{c['name']}のカップ麺の新商品の一覧。発売日・価格・カロリーの記録", company_items,
            CONFIG.get("site_url", "") + f"companies/{c['slug']}/",
            review_chain=c["slug"], empty_text="まだ記録がありません。",
        )
        (cdir / "index.html").write_text(page, encoding="utf-8")

    (DOCS / "items").mkdir(parents=True, exist_ok=True)
    for it in items:
        (DOCS / item_url(it)).write_text(render_item_page(it), encoding="utf-8")

    # 不要になった旧itemページ(オーファン)を掃除する
    valid_item_files = {(DOCS / item_url(it)).name for it in items}
    stale_items = 0
    for f in (DOCS / "items").glob("*.html"):
        if f.name not in valid_item_files:
            f.unlink()
            stale_items += 1

    (DOCS / "search-index.json").write_text(
        json.dumps(build_search_index(items), ensure_ascii=False), encoding="utf-8"
    )
    (DOCS / "rss.xml").write_text(generate_rss(items), encoding="utf-8")
    (DOCS / "sitemap.xml").write_text(generate_sitemap(items, months), encoding="utf-8")
    (DOCS / "robots.txt").write_text(generate_robots(), encoding="utf-8")
    (DOCS / "CNAME").write_text(
        CONFIG.get("site_url", "").replace("https://", "").strip("/") + "\n", encoding="utf-8"
    )
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print(
        f"generated: index.html, {len(months)} archive months, {len(CHAINS)} company pages, "
        f"{len(items)} item pages, rss.xml, sitemap.xml, search-index.json"
        + (f" / removed {stale_items} stale item pages" if stale_items else "")
        + (f" / removed {stale_months} stale archive dirs" if stale_months else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
