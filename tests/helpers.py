"""テスト共通: 合成フィクスチャを返す偽の取得器と、一時データディレクトリの差し替え。
本物の docs/ や data/ には書き込まない(TempData が crawl.py の保存先を一時ディレクトリへ差し替える)。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import crawl  # noqa: E402
from fetch import BlockedError, NotFoundError  # noqa: E402,F401

NISSIN = "https://www.nissin.com"
MYOJO = "https://www.myojofoods.co.jp"
SANYO = "https://www.sanyofoods.co.jp"
ACECOOK = "https://www.acecook.co.jp"


class FakeSite:
    """URL(完全一致)→応答を返す偽の取得器。応答は文字列・例外(raise される)・関数(url→文字列)。
    登録の無いURLは404(NotFoundError)。呼ばれたURLを calls に記録する(クローラの取得器と同じ呼び方 getter(chain, url))。
    max_requests を渡すと、実際の取得器と同じくその回数を超えたときに RequestLimitReached を投げる。"""

    def __init__(self, routes: dict | None = None, max_requests: int | None = None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []
        self.blocked_hosts: set[str] = set()
        self.max_requests = max_requests

    def set(self, url: str, resp) -> None:
        self.routes[url] = resp

    def __call__(self, chain: str, url: str) -> str:
        if self.max_requests is not None and len(self.calls) >= self.max_requests:
            raise crawl.RequestLimitReached("limit")
        self.calls.append(url)
        if any(h in url for h in self.blocked_hosts):
            raise BlockedError(f"HTTP 403: {url}")
        if url not in self.routes:
            raise NotFoundError(f"HTTP 404: {url}")
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(url)
        return r

    def count(self, part: str) -> int:
        return sum(1 for u in self.calls if part in u)


class TempData:
    """crawl.py の保存先を一時ディレクトリへ差し替える(with で使う)。"""

    ATTRS = ("DATA", "PRODUCTS", "PENDING_PATH", "BASELINE_PATH", "SKIPPED_PATH", "RUN_SUMMARY", "LIST_STATE_PATH")

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = tuple(getattr(crawl, a) for a in self.ATTRS)
        crawl.DATA = base
        crawl.PRODUCTS = base / "products"
        crawl.PENDING_PATH = base / "pending.json"
        crawl.BASELINE_PATH = base / "baseline_ids.json"
        crawl.SKIPPED_PATH = base / "skipped.json"
        crawl.RUN_SUMMARY = base / "run_summary.json"
        crawl.LIST_STATE_PATH = base / "list_state.json"
        return base

    def __exit__(self, *exc):
        for a, v in zip(self.ATTRS, self.saved):
            setattr(crawl, a, v)
        self.tmp.cleanup()


def run_chain(fn, getter, day: str, pending: dict | None = None):
    pending = {} if pending is None else pending
    stats: dict = {}
    fn(getter, pending, stats, day)
    return pending, stats


def prod(chain: str, pid: str) -> dict | None:
    return crawl.load_product(chain, pid)
