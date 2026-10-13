import json
import shutil
from pathlib import Path

import monitor

REAL_DATA = Path(__file__).resolve().parent


def setup(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    for f in [REAL_DATA / "site.json", *REAL_DATA.glob("country_*.json")]:
        shutil.copy(f, data / f.name)
    return data, data


class Fake:
    def __init__(self, pages=None, fail=()):
        self.pages = pages or {}
        self.fail = set(fail)

    def __call__(self, url):
        if url in self.fail:
            return 0, None, "TimeoutError"
        return 200, self.pages.get(url, "<html><body>base</body></html>"), ""


def test_normalize_strips_markup_and_scripts():
    raw = "<html><script>var a=1</script><style>x{}</style><!-- c --><p>Привіт&nbsp;світе</p></html>"
    assert monitor.normalize(raw) == "Привіт світе"


def test_first_run_is_baseline_second_run_detects_change(tmp_path):
    data, state = setup(tmp_path)
    ch, st = monitor.run(data, state, Fake(), "2026-10-13", delay=0)
    assert ch == []
    assert all(s["last_ok"] == "2026-10-13" for s in st.values())

    url = "https://www.gov.pl/web/udsc"
    ch, _ = monitor.run(data, state, Fake({url: "<p>нова новина</p>"}), "2026-10-14", delay=0)
    assert len(ch) == 1
    assert ch[0]["type"] == "changed" and ch[0]["url"] == url and ch[0]["country"] == "Польща"
    saved = json.loads((data / "changes.json").read_text(encoding="utf-8"))
    assert saved[0]["date"] == "2026-10-14"


def test_unchanged_page_makes_no_change(tmp_path):
    data, state = setup(tmp_path)
    monitor.run(data, state, Fake(), "2026-10-13", delay=0)
    ch, _ = monitor.run(data, state, Fake(), "2026-10-14", delay=0)
    assert ch == []
    assert not (data / "changes.json").exists()


def test_unavailable_reported_once_after_three_failures_then_recovers(tmp_path):
    data, state = setup(tmp_path)
    url = "https://www.bamf.de/"
    reports = []
    for day in range(1, 6):
        ch, st = monitor.run(data, state, Fake(fail=[url]), f"2026-10-1{day}", delay=0)
        reports.append([c for c in ch if c["url"] == url])
    assert [len(r) for r in reports] == [0, 0, 1, 0, 0]
    assert reports[2][0]["type"] == "unavailable"
    assert st[url]["fail_count"] == 5

    ch, st = monitor.run(data, state, Fake(), "2026-10-16", delay=0)
    assert [c for c in ch if c["url"] == url] == []
    assert st[url]["fail_count"] == 0 and st[url]["reported_unavailable"] is False


def test_watch_false_is_skipped(tmp_path):
    data, state = setup(tmp_path)
    p = data / "country_cz.json"
    c = json.loads(p.read_text(encoding="utf-8"))
    c["sources"][0]["watch"] = False
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    _, st = monitor.run(data, state, Fake(), "2026-10-13", delay=0)
    assert c["sources"][0]["url"] not in st


def test_report_text():
    text = monitor.make_report([
        {"date": "d", "country": "Польща", "title": "UdSC", "url": "https://u", "type": "changed"},
        {"date": "d", "country": "Чехія", "title": "MV", "url": "https://m", "type": "unavailable"},
    ])
    assert "сторінку змінено" in text and "джерело недоступне" in text


def test_http_fetch_handles_unreachable_host():
    status, body, err = monitor.http_fetch("https://127.0.0.1:9/", timeout=2, retries=0)
    assert status == 0 and body is None and err
