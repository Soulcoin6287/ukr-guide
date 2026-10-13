"""Щоденна перевірка офіційних сторінок: чи доступні і чи змінились.

    python monitor.py --report reports/latest.md

Що робить:
  * бере зі всіх data/countries/*.json джерела (поле watch не false);
  * завантажує сторінку, виділяє з неї текст і рахує хеш;
  * порівнює з state/links.json; зміни пише в data/changes.json;
  * якщо є нові зміни, створює markdown-звіт (його workflow відкриває як GitHub Issue).

Монітор НІЧОГО не публікує як факт. Він лише каже: «сторінка змінилась, перевірте».
Залежностей немає.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from build import iter_sources, load_data

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; UkrGuideMonitor/1.0; +https://github.com/)"
MAX_CHANGES = 200
FAILS_BEFORE_REPORT = 3

SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|template)\b.*?</\1>", re.I | re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def normalize(raw_html: str) -> str:
    text = COMMENT_RE.sub(" ", raw_html)
    text = SCRIPT_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()[:300_000]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def http_fetch(url: str, timeout: int = 20, retries: int = 2):
    """Повертає (status, text, error). status 0 означає помилку мережі."""
    last = ""
    for attempt in range(retries + 1):
        wait = 2 * (attempt + 1)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Language": "uk,en;q=0.8"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(2_000_000)
                charset = r.headers.get_content_charset() or "utf-8"
                return r.status, raw.decode(charset, errors="replace"), ""
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429:
                try:
                    wait = min(int(e.headers.get("Retry-After", wait)), 30)
                except (TypeError, ValueError):
                    pass
            elif e.code in (400, 401, 403, 404, 410):
                return e.code, None, last
        except Exception as e:  # мережеві помилки, таймаути, TLS
            last = type(e).__name__
        if attempt < retries:
            time.sleep(wait)
    return 0, None, last


def run(data_dir: Path, state_dir: Path, fetcher=http_fetch, today: str | None = None, delay: float = 1.0):
    today = today or dt.date.today().isoformat()
    data = load_data(data_dir)
    state_path = state_dir / "links.json"
    changes_path = data_dir / "changes.json"
    state: dict = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    changes: list[dict] = []
    if changes_path.exists():
        changes = json.loads(changes_path.read_text(encoding="utf-8"))

    new_changes: list[dict] = []
    seen: set[str] = set()
    for country, src in iter_sources(data["countries"]):
        url = src["url"]
        if src.get("watch") is False or url in seen:
            continue
        seen.add(url)
        entry = state.get(url, {"fail_count": 0})
        status, body, err = fetcher(url)
        entry["status"] = status
        if body is not None and 200 <= status < 300:
            digest = text_hash(normalize(body))
            if entry.get("hash") and entry["hash"] != digest:
                new_changes.append({
                    "date": today, "country": country["name_uk"], "title": src["title"],
                    "url": url, "type": "changed",
                })
            entry.update({"hash": digest, "last_ok": today, "fail_count": 0, "reported_unavailable": False})
        else:
            entry["fail_count"] = entry.get("fail_count", 0) + 1
            entry["last_error"] = err or f"HTTP {status}"
            if entry["fail_count"] >= FAILS_BEFORE_REPORT and not entry.get("reported_unavailable"):
                new_changes.append({
                    "date": today, "country": country["name_uk"], "title": src["title"],
                    "url": url, "type": "unavailable",
                })
                entry["reported_unavailable"] = True
        state[url] = entry
        if delay:
            time.sleep(delay)

    state_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if new_changes:
        changes = (new_changes + changes)[:MAX_CHANGES]
        changes_path.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    return new_changes, state


def make_report(new_changes: list[dict]) -> str:
    lines = ["Зміни в офіційних джерелах. Відкрийте кожне посилання, перевірте, чи потрібно оновити довідник.", ""]
    for ch in new_changes:
        kind = "сторінку змінено" if ch["type"] == "changed" else "джерело недоступне"
        lines.append(f"- [{ch['country']}] {ch['title']}: {kind}. {ch['url']}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--state", default=str(ROOT / "state"))
    ap.add_argument("--report", default=str(ROOT / "reports" / "latest.md"))
    a = ap.parse_args(argv)
    new_changes, state = run(Path(a.data), Path(a.state))
    report = Path(a.report)
    if new_changes:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(make_report(new_changes), encoding="utf-8")
    elif report.exists():
        report.unlink()
    ok = sum(1 for s in state.values() if s.get("fail_count", 0) == 0)
    print(f"Перевірено джерел: {len(state)}, доступні: {ok}, нових змін: {len(new_changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
