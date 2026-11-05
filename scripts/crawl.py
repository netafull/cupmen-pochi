#!/usr/bin/env python3
"""日清食品・明星食品・サンヨー食品・エースコックのカップ麺の新商品を巡回し、
data/products/{nissin|myojo|sanyo|acecook}/{id}.json に1商品1ファイルで蓄積する。

このサイトの軸は「発売日」。**終了は追わない**(運営者の方針: 終了があれば載せる、なければ開始だけでよい。
メーカーのサイトには終了の表示が無い)。一覧から消えた商品には何もせず、記録はそのまま残す。
例外は日清の詳細ページの「この商品の生産は終了しています」だけ。新規取得時と、30日以上たった記録の
再確認時(1回3件まで)に見つけたら `discontinued: true` を保存する。

・保存するのは事実(商品名・価格(税別)・内容量・発売日・発売地域・JAN・栄養成分・アレルゲン)だけ。
  画像・商品説明文・原材料名は読み取りも保存もしない。
・発売日が config の record_from(2026-08-01)より前の商品は記録しない(明星のリニューアル品=発売日が古く
  renew_date だけ新しいものも、これで除かれる)。
・カップ麺だけを記録する(判定は parsers.py。袋麺・5食パック・カップめし・スープ・菓子は除く)。
  判定で除いた商品はIDを data/skipped.json に覚えて、毎日取り直さない。
・取得失敗は会社単位で隔離し(data/pending.json)、他の会社へ波及させない。
・ブロック(403・チャレンジ)されたらUA偽装などに切り替えず、止まって報告する。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402
from fetch import BlockedError, NotFoundError, fetch  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
PRODUCTS = DATA / "products"
PENDING_PATH = DATA / "pending.json"
BASELINE_PATH = DATA / "baseline_ids.json"
SKIPPED_PATH = DATA / "skipped.json"  # 判定で除いた商品のID(毎日取り直さないため)
RUN_SUMMARY = DATA / "run_summary.json"
LIST_STATE_PATH = DATA / "list_state.json"  # 会社ごとの前回の一覧の件数(急減の検知用)
JST = datetime.timezone(datetime.timedelta(hours=9))

UA = CONFIG.get("user_agent", "CupmenPochi/1.0 (+https://cupmen.netaful.jp/)")
INTERVALS = CONFIG.get("request_interval_seconds", {})
MAX_REQUESTS = CONFIG.get("max_requests_per_run", {})
MIN_INTERVAL = 1.0
RECORD_FROM = CONFIG.get("record_from", "2026-08-01")
RECHECK_DAYS = int(CONFIG.get("recheck_days", 30))
RECHECK_MAX = int(CONFIG.get("recheck_max_per_run", 3))
LIST_DROP_MIN_PREV = CONFIG.get("list_drop_min_prev") or {}
RATIO_MIN = 0.5
RATIO_ACCEPT_DAYS = 3


def now_iso() -> str:
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def today_jst() -> str:
    return datetime.datetime.now(JST).date().isoformat()


# ---------------------------------------------------------------------------
# 保存まわり
# ---------------------------------------------------------------------------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj, **kw) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, **kw), encoding="utf-8")


def product_path(chain: str, product_id: str) -> Path:
    return PRODUCTS / chain / f"{product_id}.json"


def save_product(chain: str, product: dict) -> None:
    save_json(product_path(chain, product["product_id"]), product)


def load_product(chain: str, product_id: str) -> dict | None:
    return load_json(product_path(chain, product_id), None)


def load_chain_products(chain: str) -> list[dict]:
    out = []
    d = PRODUCTS / chain
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            p = load_json(f, None)
            if p:
                out.append(p)
    return out


class RequestLimitReached(RuntimeError):
    """1回の実行あたりのリクエスト上限。残りは翌日以降に回す(失敗ではない)。"""


class Throttle:
    def __init__(self):
        self.last: dict[str, float] = {}

    def wait(self, chain: str) -> None:
        interval = max(INTERVALS.get(chain, 2), MIN_INTERVAL)
        last = self.last.get(chain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self.last[chain] = time.monotonic()


class Getter:
    """リクエスト数の上限つきの取得器。テストでは差し替える。素のUA(config の user_agent)で取る。"""

    def __init__(self):
        self.throttle = Throttle()
        self.counts: dict[str, int] = {}

    def __call__(self, chain: str, url: str) -> str:
        limit = MAX_REQUESTS.get(chain)
        if limit is not None and self.counts.get(chain, 0) >= limit:
            raise RequestLimitReached(f"{chain}: 1回の実行あたりのリクエスト上限({limit})に達しました")
        self.throttle.wait(chain)
        self.counts[chain] = self.counts.get(chain, 0) + 1
        return fetch(url, UA)


# ---------------------------------------------------------------------------
# 一覧の急減の検知・歯止め
# ---------------------------------------------------------------------------

def new_stats() -> dict:
    return {"list_requests": 0, "detail_requests": 0, "new_products": 0, "skipped": 0,
            "failed": 0, "blocked": False, "list_ok": False, "seen": 0, "deferred": 0}


def baseline_for(chain: str) -> set[str] | None:
    ids = load_json(BASELINE_PATH, {}).get(chain)
    return set(ids) if ids is not None else None


def save_baseline(chain: str, ids: set[str]) -> None:
    data = load_json(BASELINE_PATH, {})
    data[chain] = sorted(ids)
    save_json(BASELINE_PATH, data)


def ratio_ok(chain: str, n: int, today: str) -> bool:
    """一覧の件数が前回の半分未満に急減したら、3日続くまで新規を記録しない(取得・解析の不具合の疑い)。
    本当に減ったなら、3日目に受け入れて基準を更新する。会社ごとの最小件数(list_drop_min_prev)未満、
    または 0 のときは見ない。"""
    min_prev = int(LIST_DROP_MIN_PREV.get(chain, 10))
    state = load_json(LIST_STATE_PATH, {})
    cur = state.get(chain) or {}
    prev = int(cur.get("count") or 0)
    if min_prev and prev >= min_prev and n < prev * RATIO_MIN:
        low = dict(cur.get("low") or {})
        if low.get("date") != today:
            low = {"date": today, "days": int(low.get("days") or 0) + 1, "count": n}
        state[chain] = {**cur, "low": low}
        if low["days"] < RATIO_ACCEPT_DAYS:
            save_json(LIST_STATE_PATH, state, sort_keys=True)
            print(f"[WARNING] {chain}: 一覧が{n}件(前回{prev}件)に急減。{low['days']}日目なので新規は記録しない",
                  file=sys.stderr)
            return False
    state[chain] = {"count": n, "checked_at": today}
    save_json(LIST_STATE_PATH, state, sort_keys=True)
    return True


# 歯止め: 1回で「新規候補」が極端に多いときは、公式のページ構造・ID体系の変更とみなして記録しない。
# 新規候補 = 一覧にあるが、baseline(初回に見えたID)にも、記録済みにも、除外済みにも無いもの。
# 閾値は max(new_burst_min_by_chain, 一覧の件数×new_burst_ratio) を new_burst_abs_by_chain で頭打ち。
# 初回(baselineがまだ無い)は対象外。保留は翌日以降に候補が閾値以下に戻れば自然に再開する。
# 3日連続で保留なら通知が「要判断」に変わる。
BURST_KEY = "_suspected_restructure"
BURST_ESCALATE_DAYS = 3


def burst_threshold(chain: str, list_size: int) -> int:
    lo = int((CONFIG.get("new_burst_min_by_chain") or {}).get(chain, CONFIG.get("new_burst_min", 10)))
    ratio = float(CONFIG.get("new_burst_ratio", 0.95))
    limit = max(lo, int(list_size * ratio))
    cap = (CONFIG.get("new_burst_abs_by_chain") or {}).get(chain)
    return min(limit, int(cap)) if cap else limit


def burst_blocked(chain: str, todo: dict, list_size: int, baseline: set[str] | None, pending: dict,
                  stats: dict, today: str) -> bool:
    """歯止めが働いたら True(この回は新規を記録しない)。"""
    st = stats[chain]
    chain_pending = pending.setdefault(chain, {})
    if baseline is None:
        chain_pending.pop(BURST_KEY, None)
        return False
    candidates = [pid for pid in todo if pid not in baseline]
    limit = burst_threshold(chain, list_size)
    if len(candidates) <= limit:
        chain_pending.pop(BURST_KEY, None)
        return False
    prev = chain_pending.get(BURST_KEY) or {}
    if prev.get("date") == today:
        days = int(prev.get("consecutive_days") or 1)
    else:
        days = int(prev.get("consecutive_days") or 0) + 1
    chain_pending[BURST_KEY] = {
        "reason": "suspected_restructure", "count": len(candidates), "threshold": limit,
        "list_size": list_size, "date": today, "consecutive_days": days,
        "first_date": prev.get("first_date") or today, "sample": sorted(candidates)[:5],
    }
    st["suspected_restructure"] = {"count": len(candidates), "threshold": limit, "consecutive_days": days}
    st["list_ok"] = False
    msg = (f"[WARNING] {chain}: 新規候補が一度に{len(candidates)}件(閾値{limit}件・一覧{list_size}件)。"
           "公式のサイト構造変更の疑い。この回は新規を記録しない")
    if days >= BURST_ESCALATE_DAYS:
        msg += f" [{days}日連続で保留中。運営者の判断が必要]"
    print(msg, file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

class Robots:
    """ホストごとに robots.txt を1回だけ取る。404・HTMLを返すサイトは全許可。取得失敗も全許可(警告のみ)。
    403等のブロックは BlockedError。"""

    def __init__(self, getter, chain: str, stats_st: dict):
        self.getter = getter
        self.chain = chain
        self.st = stats_st
        self.parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, base: str, path: str) -> bool:
        rp = self.parsers.get(base)
        if rp is None:
            text = None
            try:
                text = self.getter(self.chain, base + "/robots.txt")
                self.st["list_requests"] += 1
            except NotFoundError:
                self.st["list_requests"] += 1
            except (BlockedError, RequestLimitReached):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {self.chain} {base}/robots.txt 取得失敗(全許可として続行): {e}", file=sys.stderr)
            if text and re.search(r"<\s*html", text[:500], re.I):
                text = None
            rp = urllib.robotparser.RobotFileParser()
            rp.parse((text or "User-agent: *\nAllow: /").splitlines())
            self.parsers[base] = rp
        return rp.can_fetch(UA, base + path)


# ---------------------------------------------------------------------------
# 共通の巡回の流れ
# ---------------------------------------------------------------------------

class Skip(Exception):
    """判定で記録しない商品(理由つき)。IDを覚えて、毎日取り直さない。"""

    def __init__(self, reason: str, name: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.name = name


class Run:
    """1社の1回の巡回の状態と、共通の後処理(記録・除外の記憶・歯止め)。"""

    def __init__(self, getter, chain: str, pending: dict, stats: dict, today: str | None):
        self.getter = getter
        self.chain = chain
        self.now = now_iso() if today is None else f"{today}T07:00:00+09:00"
        self.today = today or today_jst()
        self.pending = pending
        self.pending.setdefault(chain, {})
        stats.setdefault(chain, new_stats())
        self.stats = stats
        self.st = stats[chain]

    def get_list(self, url: str) -> str:
        text = self.getter(self.chain, url)
        self.st["list_requests"] += 1
        return text

    def get_detail(self, url: str) -> str:
        text = self.getter(self.chain, url)
        self.st["detail_requests"] += 1
        return text

    def block(self, where: str, e: Exception) -> None:
        self.st["blocked"] = True
        print(f"[blocked] {self.chain} {where}: {e}", file=sys.stderr)

    def fail_detail(self, pid: str, url: str, reason: str) -> None:
        self.st["failed"] += 1
        self.pending[self.chain][pid] = {"url": url, "reason": reason, "checked_at": self.now}

    def new_product(self, pid: str, name: str, **fields) -> dict:
        """記録の骨格。**説明文・画像・原材料名は持たない**。"""
        p = {
            "chain": self.chain, "product_id": pid, "name": name,
            "price_excl_tax": None, "price_label": None,
            "launch_date": None, "launch_month": None,
            "net_amount": None, "net_label": None, "sales_area": None, "ec_only": False, "jan": None,
            "nutrition": None, "nutrition_label": None, "allergens": None,
            "discontinued": False, "official_url": None,
            "first_seen_at": self.now, "detail_checked_at": self.today,
        }
        p.update(fields)
        al = p.get("allergens")
        if isinstance(al, list):
            p["allergens"] = {"contains": al}
        return p

    def process(self, entries: dict[str, dict], raw_count: int, list_ok: bool, decide) -> None:
        """entries({商品ID: 一覧の項目})のうち、記録済み・除外済みでないものを decide(entry)->商品dict で処理する。
        decide は Skip を投げて除外できる。上限に達したら残りは翌日に回す。"""
        chain, st, today = self.chain, self.st, self.today
        st["list_ok"] = bool(list_ok and raw_count > 0)
        st["seen"] = len(entries)
        if st["list_ok"] and not ratio_ok(chain, raw_count, today):
            st["list_ok"] = False
            return
        if not st["list_ok"]:
            return
        baseline = baseline_for(chain)
        skip_all = load_json(SKIPPED_PATH, {})
        skipped = skip_all.setdefault(chain, {})
        todo = {pid: e for pid, e in entries.items() if pid not in skipped and load_product(chain, pid) is None}
        if burst_blocked(chain, todo, len(entries), baseline, self.pending, self.stats, today):
            return
        order = sorted(todo, key=lambda pid: ((todo[pid].get("launch_date") or ""), _idkey(pid)), reverse=True)
        skips_changed = False
        for i, pid in enumerate(order):
            e = todo[pid]
            try:
                product = decide(e)
            except Skip as s:
                skipped[pid] = {"reason": s.reason, "name": s.name or e.get("name"), "date": today}
                skips_changed = True
                st["skipped"] += 1
                self.pending[chain].pop(pid, None)
                continue
            except RequestLimitReached as ex:
                st["deferred"] = len(order) - i
                print(f"[info] {chain}: {ex}。残り{st['deferred']}件は次回に回す", file=sys.stderr)
                break
            except BlockedError as ex:
                self.block(f"detail {pid}", ex)
                break
            except NotFoundError:
                skipped[pid] = {"reason": "404", "name": e.get("name"), "date": today}
                skips_changed = True
                st["skipped"] += 1
                continue
            except Exception as ex:  # noqa: BLE001
                self.fail_detail(pid, e.get("url") or "", f"fetch_error: {type(ex).__name__}: {ex}")
                continue
            save_product(chain, product)
            st["new_products"] += 1
            self.pending[chain].pop(pid, None)
        if skips_changed:
            save_json(SKIPPED_PATH, skip_all, sort_keys=True)
        if baseline is None and not st["blocked"]:
            save_baseline(chain, set(entries))


def _idkey(pid: str) -> int:
    m = re.search(r"\d+", pid)
    return int(m.group()) if m else 0


# ---------------------------------------------------------------------------
# 日清食品
# ---------------------------------------------------------------------------

NISSIN_LIST_PATH = "/jp/product/new/"


def crawl_nissin(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "nissin"
    run = Run(getter, chain, pending, stats, today)
    st = run.st
    robots = Robots(getter, chain, st)
    try:
        if not robots.allowed(parsers.NISSIN_BASE, NISSIN_LIST_PATH):
            print("[skip] nissin: robots.txtでDisallow", file=sys.stderr)
            return
        html = run.get_list(parsers.NISSIN_BASE + NISSIN_LIST_PATH)
    except BlockedError as e:
        run.block("list", e)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] nissin 一覧の取得失敗: {e}", file=sys.stderr)
        return
    items = parsers.parse_nissin_list(html, datetime.date.fromisoformat(run.today))
    entries = {e["id"]: e for e in items if e["launch_date"] >= RECORD_FROM}

    def decide(e):
        d = parsers.parse_nissin_detail(run.get_detail(e["url"]))
        if not d.get("name"):
            raise ValueError("商品名が読めない(構造変更の疑い)")
        if not parsers.nissin_is_cup(d):
            raise Skip("not_cup")
        return run.new_product(
            e["id"], e["name"], launch_date=e["launch_date"], price_excl_tax=d["price_excl_tax"],
            price_label=d["price_label"], net_amount=d["net_amount"], net_label=d["net_label"],
            sales_area=d["sales_area"], jan=d["jan"], nutrition=d["nutrition"],
            nutrition_label=d["nutrition_label"], allergens=d["allergens"],
            discontinued=bool(d["discontinued"]), official_url=e["url"])

    run.process(entries, len(items), bool(items), decide)
    if not st["blocked"]:
        recheck_nissin(run)


def due_for_recheck(product: dict, today: str) -> bool:
    last = product.get("detail_checked_at")
    if not last:
        return True
    try:
        return (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(last)).days >= RECHECK_DAYS
    except ValueError:
        return True


def recheck_nissin(run: Run) -> None:
    """日清の記録のうち、詳細を取ってから RECHECK_DAYS 日以上たったものを(古い順に最大 RECHECK_MAX 件)
    取り直し、「生産は終了しています」の表示を確認する。値は discontinued と、空だった栄養・アレルゲンだけ更新する。"""
    due = [p for p in load_chain_products(run.chain) if due_for_recheck(p, run.today)]
    due.sort(key=lambda p: p.get("detail_checked_at") or "")
    for p in due[:RECHECK_MAX]:
        try:
            d = parsers.parse_nissin_detail(run.get_detail(p["official_url"]))
        except RequestLimitReached:
            return
        except BlockedError as ex:
            run.block(f"recheck {p['product_id']}", ex)
            return
        except Exception as ex:  # noqa: BLE001
            print(f"[warn] nissin 再確認の失敗 {p['product_id']}: {ex}", file=sys.stderr)
            continue
        if not d.get("name"):
            continue
        p["discontinued"] = bool(d["discontinued"])
        if p.get("nutrition") is None and d.get("nutrition"):
            p["nutrition"], p["nutrition_label"] = d["nutrition"], d["nutrition_label"]
        if p.get("allergens") is None and d.get("allergens"):
            p["allergens"] = {"contains": d["allergens"]}
        p["detail_checked_at"] = run.today
        save_product(run.chain, p)


# ---------------------------------------------------------------------------
# 明星食品
# ---------------------------------------------------------------------------

MYOJO_MAX_PAGES = 5


def crawl_myojo(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "myojo"
    run = Run(getter, chain, pending, stats, today)
    st = run.st
    robots = Robots(getter, chain, st)
    items: dict[str, dict] = {}
    try:
        if not robots.allowed(parsers.MYOJO_BASE, "/api/v1.1/ja_jp/products/list"):
            print("[skip] myojo: robots.txtでDisallow", file=sys.stderr)
            return
        for page in range(1, MYOJO_MAX_PAGES + 1):
            url = parsers.MYOJO_LIST_URL + ("" if page == 1 else f"&page={page}")
            got = parsers.parse_myojo_list(run.get_list(url))
            for e in got:
                items[e["id"]] = e
            if len(got) < parsers.MYOJO_PAGE_SIZE:
                break
    except BlockedError as e:
        run.block("list", e)
        return
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[warn] myojo 一覧の構造が想定と違う(構造変更の疑い): {e}", file=sys.stderr)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] myojo 一覧の取得失敗: {e}", file=sys.stderr)
        return
    # company=myojofoods が効いていないと日清の商品が返る。名前の「日清」で検知する
    nissin_like = sum(1 for e in items.values() if e["name"].startswith("日清"))
    if items and nissin_like * 3 > len(items):
        print("[warn] myojo 一覧に日清の商品が多い(company指定が効いていない疑い)。記録しない", file=sys.stderr)
        return
    # 発売日が record_from 以降のカップ麺・焼そばだけ(リニューアル品は date が古いので除かれる)
    entries = {pid: e for pid, e in items.items()
               if parsers.myojo_is_cup(e) and e["launch_date"] and e["launch_date"] >= RECORD_FROM}

    def decide(e):
        detail_url = f"{parsers.MYOJO_BASE}/api/v1/ja_jp/products/detail?id={e['id']}"
        d = parsers.parse_myojo_detail(run.get_detail(detail_url))
        if d.get("company_slug") not in (None, "myojofoods"):
            raise ValueError(f"明星の商品ではない応答: {d.get('company_slug')}")
        return run.new_product(
            e["id"], e["name"], launch_date=e["launch_date"], price_excl_tax=e["price_excl_tax"],
            price_label=e["price_label"], net_amount=d["net_amount"] or e["net_amount"],
            net_label=d["net_label"], sales_area=d["sales_area"], ec_only=d["ec_only"], jan=e["jan"],
            nutrition=d["nutrition"], nutrition_label=d["nutrition_label"], allergens=d["allergens"],
            official_url=e["url"])

    run.process(entries, len(items), bool(items), decide)


# ---------------------------------------------------------------------------
# サンヨー食品
# ---------------------------------------------------------------------------

SANYO_SITEMAP_PATH = "/wp-sitemap-posts-products-1.xml"


def crawl_sanyo(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "sanyo"
    run = Run(getter, chain, pending, stats, today)
    st = run.st
    robots = Robots(getter, chain, st)
    try:
        if not robots.allowed(parsers.SANYO_BASE, SANYO_SITEMAP_PATH):
            print("[skip] sanyo: robots.txtでDisallow", file=sys.stderr)
            return
        xml = run.get_list(parsers.SANYO_BASE + SANYO_SITEMAP_PATH)
    except BlockedError as e:
        run.block("list", e)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] sanyo サイトマップの取得失敗: {e}", file=sys.stderr)
        return
    items = parsers.parse_sanyo_sitemap(xml)
    # サイトマップに日付は無い(lastmodなし)。未取得のURLだけ、IDの大きい(新しい)順に詳細を取る
    entries = {e["id"]: e for e in items}
    floor_month = RECORD_FROM[:7]

    def decide(e):
        d = parsers.parse_sanyo_detail(run.get_detail(e["url"]))
        if not d.get("name"):
            raise ValueError("商品名が読めない(構造変更の疑い)")
        name = d["name"]
        if not d.get("launch_month"):
            raise Skip("no_launch_month", name)
        if d["launch_month"] < floor_month:
            raise Skip("before_record_from", name)
        if not parsers.sanyo_is_cup(name, d.get("packing")):
            raise Skip("not_cup", name)
        # 発売日は月までしか無い。launch_date は持たず launch_month で保存する
        return run.new_product(
            e["id"], name, launch_month=d["launch_month"], price_excl_tax=d["price_excl_tax"],
            price_label=d["price_label"], net_amount=d["net_amount"], net_label=d["net_label"],
            sales_area=d["sales_area"], jan=d["jan"], nutrition=d["nutrition"],
            nutrition_label=d["nutrition_label"], allergens=d["allergens"], official_url=e["url"])

    run.process(entries, len(items), bool(items), decide)


# ---------------------------------------------------------------------------
# エースコック
# ---------------------------------------------------------------------------

ACECOOK_LIST_PATH = "/products/arrival/"


def crawl_acecook(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "acecook"
    run = Run(getter, chain, pending, stats, today)
    st = run.st
    robots = Robots(getter, chain, st)
    try:
        if not robots.allowed(parsers.ACECOOK_BASE, ACECOOK_LIST_PATH):
            print("[skip] acecook: robots.txtでDisallow", file=sys.stderr)
            return
        html = run.get_list(parsers.ACECOOK_BASE + ACECOOK_LIST_PATH)
    except BlockedError as e:
        run.block("list", e)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] acecook 一覧の取得失敗: {e}", file=sys.stderr)
        return
    items = parsers.parse_acecook_list(html)
    entries = {e["id"]: e for e in items if not e["launch_date"] or e["launch_date"] >= RECORD_FROM}

    def decide(e):
        d = parsers.parse_acecook_detail(run.get_detail(e["url"]))
        if not d.get("name"):
            raise ValueError("商品名が読めない(構造変更の疑い)")
        launch = d.get("launch_date") or e.get("launch_date")
        if launch and launch < RECORD_FROM:
            raise Skip("before_record_from")
        if not parsers.acecook_is_cup(d["name"], d.get("water")):
            raise Skip("not_cup")
        return run.new_product(
            e["id"], d["name"], launch_date=launch, price_excl_tax=d["price_excl_tax"] or e.get("price_excl_tax"),
            price_label=d["price_label"], net_amount=d["net_amount"], sales_area=d["sales_area"], jan=d["jan"],
            nutrition=d["nutrition"], nutrition_label=d["nutrition_label"], allergens=d["allergens"],
            official_url=e["url"])

    run.process(entries, len(items), bool(items), decide)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def collect_problems(stats: dict[str, dict]) -> dict[str, list[str]]:
    """この実行で問題があった会社を種別ごとに集める(通知用)。ブロック・例外・歯止め(別の保留通知がある)
    と重複しないよう、list_failed はそれらを除く。詳細ページ単発の失敗は翌日に自動で再試行されるので数えない。"""
    return {
        "blocked": [c for c, s in stats.items() if s.get("blocked")],
        "list_failed": [c for c, s in stats.items()
                        if not s.get("list_ok") and not s.get("blocked") and not s.get("error")
                        and not s.get("suspected_restructure")],
        "errors": [c for c, s in stats.items() if s.get("error")],
    }


def update_health(pending: dict, stats: dict[str, dict], today: str) -> dict[str, int]:
    """会社ごとの「連続で問題が出ている日数」を pending["_health"] に持ち、今回問題があった会社の日数を返す。"""
    health = pending.setdefault("_health", {})
    bad = {c for names in collect_problems(stats).values() for c in names}
    yesterday = (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()
    streaks: dict[str, int] = {}
    for chain in stats:
        if chain not in bad:
            health.pop(chain, None)
            continue
        prev = health.get(chain) or {}
        n = int(prev.get("days", 0))
        if prev.get("last_date") != today:
            n = n + 1 if prev.get("last_date") == yesterday else 1
        health[chain] = {"days": n, "last_date": today}
        streaks[chain] = n
    if not health:
        pending.pop("_health", None)
    return streaks


def save_run_summary(stats: dict[str, dict], streaks: dict[str, int] | None = None) -> None:
    """notify.py が読む、この実行の結果。実行ごとに上書きする一時ファイル(gitには入れない)。
    形式: {"new": {...}, "suspected": {...}, "blocked": [...], "list_failed": [...], "errors": [...],
           "streaks": {...}, "detail_failed": {...}}"""
    out = {
        "new": {chain: s.get("new_products", 0) for chain, s in stats.items()},
        "suspected": {chain: s["suspected_restructure"] for chain, s in stats.items()
                      if s.get("suspected_restructure")},
    }
    problems = collect_problems(stats)
    out.update(problems)
    out["streaks"] = streaks or {}
    listed = set(problems["list_failed"]) | set(problems["blocked"]) | set(problems["errors"])
    out["detail_failed"] = {c: s["failed"] for c, s in stats.items() if s.get("failed") and c not in listed}
    RUN_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    RUN_SUMMARY.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


CRAWLERS = {"nissin": crawl_nissin, "myojo": crawl_myojo, "sanyo": crawl_sanyo, "acecook": crawl_acecook}


def main(argv: list[str] | None = None) -> int:
    # 引数で会社を指定すると、その会社だけ巡回する(例: crawl.py nissin)。引数なしは全社
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a not in CRAWLERS]
    if unknown:
        print(f"不明な会社: {unknown} (指定できるのは {list(CRAWLERS)})", file=sys.stderr)
        return 2
    targets = [CRAWLERS[a] for a in argv] if argv else list(CRAWLERS.values())
    pending = load_json(PENDING_PATH, {})
    stats: dict[str, dict] = {}
    getter = Getter()
    started = time.monotonic()
    names = {v: k for k, v in CRAWLERS.items()}

    for fn in targets:
        try:
            fn(getter, pending, stats)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {fn.__name__} で例外: {e}", file=sys.stderr)
            err_chain = names.get(fn)
            if err_chain:
                stats.setdefault(err_chain, new_stats())["error"] = f"{type(e).__name__}: {e}"

    streaks = update_health(pending, stats, today_jst())
    save_json(PENDING_PATH, pending, sort_keys=True)
    save_run_summary(stats, streaks)

    print("=== クロール結果 ===")
    for chain, s in stats.items():
        print(
            f"{chain}: 一覧等{s.get('list_requests', 0)}件 / 詳細{s.get('detail_requests', 0)}件 "
            f"(リクエスト計{getter.counts.get(chain, 0)}) / 一覧の対象{s.get('seen', 0)}件 "
            f"/ 新規{s.get('new_products', 0)}件 / 除外{s.get('skipped', 0)}件 / 持ち越し{s.get('deferred', 0)}件 "
            f"/ 失敗{s.get('failed', 0)}件 / 一覧取得={'成功' if s.get('list_ok') else '失敗・保留'} "
            f"/ ブロック={'あり' if s.get('blocked') else 'なし'}"
            + (f" / 例外={s['error']}" if s.get("error") else "")
        )
    print(f"所要時間: {time.monotonic() - started:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
