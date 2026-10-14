"""Генератор статичного сайту «Довідник для українців за кордоном».

Використання:
    python build.py --out dist --base-url https://USER.github.io/REPO

Усі дані лежать поруч зі скриптом: site.json, country_*.json, changes.json і links.json (останні два пише monitor.py).
Залежностей немає, потрібен лише Python 3.10+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ADSENSE_RE = re.compile(r"^ca-pub-\d{10,20}$")
GOOGLE_FILE_RE = re.compile(r"^google[0-9a-f]{8,}\.html$")

CHANGE_LABELS = {
    "changed": "Сторінку офіційного джерела змінено. Ми перевіряємо, чи потрібно оновити довідник.",
    "unavailable": "Офіційне джерело недоступне кілька перевірок поспіль.",
}

CSS = """
:root{--bg:#fafaf7;--fg:#1b1f23;--muted:#5b6670;--card:#fff;--line:#e2e5e8;--accent:#0b57d0;--ok:#146c2e;--warn:#8a5a00;--bad:#b3261e;--hero:#e8f0fe}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e8eaed;--muted:#a0a8b0;--card:#1c2024;--line:#2f353b;--accent:#8ab4f8;--ok:#7fd39a;--warn:#f0c36a;--bad:#f2a199;--hero:#1c2a44}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:var(--accent)}
.wrap{max-width:860px;margin:0 auto;padding:0 16px}
header.site{border-bottom:1px solid var(--line);background:var(--card)}
header.site .wrap{padding-top:12px;padding-bottom:12px}
.brand{font-weight:700;font-size:1.1rem;text-decoration:none;color:var(--fg)}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:6px}
nav a{font-size:.95rem;text-decoration:none;color:var(--muted)}
nav a:hover,nav a[aria-current=page]{color:var(--accent);text-decoration:underline}
main{padding:24px 0 40px}
h1{font-size:1.7rem;line-height:1.25;margin:0 0 8px}
h2{font-size:1.25rem;margin:28px 0 10px}
.lead{color:var(--muted);margin:0 0 18px}
.hero{background:var(--hero);border-radius:12px;padding:16px 18px;margin:0 0 20px}
.hero h2{margin:0 0 6px;font-size:1.05rem}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;padding:0;list-style:none}
.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;text-decoration:none;color:var(--fg)}
.card:hover{border-color:var(--accent)}
.card strong{display:block;font-size:1.05rem}
.card span{color:var(--muted);font-size:.9rem}
ul.facts,ul.src,ul.log{padding-left:0;list-style:none;margin:0}
ul.facts li,ul.src li,ul.log li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 10px}
.meta{display:block;color:var(--muted);font-size:.88rem;margin-top:4px}
.badge{display:inline-block;font-size:.78rem;border-radius:999px;padding:1px 8px;border:1px solid currentColor;margin-left:6px;white-space:nowrap}
.badge.ok{color:var(--ok)}.badge.warn{color:var(--warn)}.badge.bad{color:var(--bad)}
.notice{border-left:4px solid var(--warn);background:var(--card);padding:10px 14px;border-radius:6px;margin:16px 0}
.ad{font-size:.78rem;color:var(--muted)}
footer.site{border-top:1px solid var(--line);color:var(--muted);font-size:.88rem;padding:18px 0 30px}
"""


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def is_https(url: str) -> bool:
    p = urlparse(url or "")
    return p.scheme == "https" and bool(p.netloc)


def normalize_base(url: str) -> str:
    """Прибирає зайвий «/» і переводить назву хоста в нижній регістр (так вимагає однаковість адрес у Google)."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    p = urlparse(url)
    return p._replace(netloc=p.netloc.lower()).geturl()


def load_data(data_dir: Path) -> dict:
    site = load_json(data_dir / "site.json", {}) or {}
    countries = []
    for p in sorted(data_dir.glob("country_*.json")):
        c = load_json(p)
        c["_file"] = p.name
        countries.append(c)
    order = site.get("order", [])
    rank = {code: i for i, code in enumerate(order)}
    countries.sort(key=lambda c: (rank.get(c.get("code"), len(order)), c.get("name_uk", "")))
    changes = load_json(data_dir / "changes.json", []) or []
    links = load_json(data_dir / "links.json", {}) or {}
    return {"site": site, "countries": countries, "changes": changes, "links": links}


def iter_sources(countries):
    for c in countries:
        for s in c.get("sources", []):
            yield c, s


def validate_data(data: dict) -> list[str]:
    problems: list[str] = []
    site = data["site"]
    if not site.get("title"):
        problems.append("site.json: нет title")
    client = site.get("adsense_client", "")
    if client and not ADSENSE_RE.match(client):
        problems.append("site.json: adsense_client должен иметь вид ca-pub-1234567890123456")
    for a in site.get("affiliates", []):
        if not a.get("title") or not is_https(a.get("url", "")):
            problems.append(f"site.json: партнёрская ссылка без title или с не-https url: {a}")
    codes = set()
    for c in data["countries"]:
        where = c.get("_file", "?")
        code = c.get("code", "")
        if not re.fullmatch(r"[a-z]{2,3}", code):
            problems.append(f"{where}: code должен быть из 2-3 латинских букв")
        if code in codes:
            problems.append(f"{where}: повторяется code {code}")
        codes.add(code)
        for key in ("name_uk", "summary", "updated"):
            if not c.get(key):
                problems.append(f"{where}: нет поля {key}")
        if c.get("updated") and not DATE_RE.match(c["updated"]):
            problems.append(f"{where}: updated должен быть YYYY-MM-DD")
        for f in c.get("facts", []):
            if not f.get("text"):
                problems.append(f"{where}: факт без текста")
            if not is_https(f.get("source_url", "")):
                problems.append(f"{where}: факт без https-источника: {f.get('text', '')[:40]}")
            if not DATE_RE.match(f.get("checked", "")):
                problems.append(f"{where}: факт без даты проверки: {f.get('text', '')[:40]}")
        for s in c.get("sources", []):
            if not s.get("title") or not s.get("topic"):
                problems.append(f"{where}: источник без title/topic: {s}")
            if not is_https(s.get("url", "")):
                problems.append(f"{where}: источник с не-https url: {s.get('url')}")
            if s.get("verified") and not DATE_RE.match(s.get("checked", "")):
                problems.append(f"{where}: проверенный источник без даты checked: {s.get('url')}")
    return problems


def source_status(src: dict, links: dict) -> tuple[str, str]:
    st = links.get(src["url"], {})
    if st.get("fail_count", 0) >= 3:
        return "bad", "недоступне"
    if st.get("last_ok"):
        return "ok", f"доступне станом на {st['last_ok']}"
    if src.get("verified"):
        return "ok", f"перевірено {src.get('checked', '')}"
    return "warn", "ще не перевірено"


class Ctx:
    def __init__(self, data: dict, base_url: str, today: str):
        self.site = data["site"]
        self.countries = data["countries"]
        self.changes = data["changes"]
        self.links = data["links"]
        self.base_url = normalize_base(base_url)
        self.today = today


def layout(ctx: Ctx, title: str, desc: str, body: str, depth: int, path: str, current: str = "") -> str:
    site = ctx.site
    prefix = "../" * depth
    full = site["title"] if title == site["title"] else f"{title} — {site['title']}"
    canon = f'<link rel="canonical" href="{esc(ctx.base_url + "/" + path)}">' if ctx.base_url else ""
    adsense = ""
    if site.get("adsense_client"):
        adsense = (
            '<script async crossorigin="anonymous" '
            f'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={esc(site["adsense_client"])}"></script>'
        )
    rss = (
        f'<link rel="alternate" type="application/rss+xml" title="Зміни" href="{prefix}feed.xml">'
        if ctx.base_url else ""
    )
    nav_items = [("home", f"{prefix}index.html", "Головна")]
    for c in ctx.countries:
        nav_items.append((c["code"], f"{prefix}{c['code']}/index.html", c["name_uk"]))
    nav_items += [("changes", f"{prefix}changes.html", "Що змінилося"), ("about", f"{prefix}about.html", "Про сайт")]
    nav = "".join(
        f'<a href="{esc(href)}"{" aria-current=\"page\"" if key == current else ""}>{esc(label)}</a>'
        for key, href, label in nav_items
    )
    return f"""<!doctype html>
<html lang="{esc(site.get('lang', 'uk'))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(full)}">
<meta property="og:description" content="{esc(desc)}">
{canon}
{rss}
<link rel="stylesheet" href="{prefix}assets/style.css">
{adsense}
</head>
<body>
<header class="site"><div class="wrap">
<a class="brand" href="{prefix}index.html">{esc(site['title'])}</a>
<nav aria-label="Розділи">{nav}</nav>
</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer class="site"><div class="wrap">
Довідкова інформація, не є юридичною консультацією. Правила змінюються, тому перед рішенням перевіряйте їх на офіційних сайтах за посиланнями.
Сайт оновлено {esc(ctx.today)}.
</div></footer>
</body>
</html>
"""


def render_facts(facts: list[dict]) -> str:
    if not facts:
        return ""
    items = []
    for f in facts:
        note = f'<span class="meta">{esc(f["note"])}</span>' if f.get("note") else ""
        items.append(
            f'<li>{esc(f["text"])}'
            f'<span class="meta">Джерело: <a href="{esc(f["source_url"])}" rel="noopener">{esc(f.get("source_title") or f["source_url"])}</a>'
            f', перевірено {esc(f["checked"])}</span>{note}</li>'
        )
    return '<ul class="facts">' + "".join(items) + "</ul>"


def render_sources(sources: list[dict], links: dict) -> str:
    if not sources:
        return "<p>Офіційні джерела для цієї країни ще не додано.</p>"
    topics: dict[str, list[dict]] = {}
    for s in sources:
        topics.setdefault(s["topic"], []).append(s)
    out = []
    for topic, items in topics.items():
        out.append(f"<h3>{esc(topic)}</h3><ul class=\"src\">")
        for s in items:
            kind, label = source_status(s, links)
            lang = f' <span class="meta">Мова: {esc(s["lang"])}</span>' if s.get("lang") else ""
            note = f'<span class="meta">{esc(s["note"])}</span>' if s.get("note") else ""
            out.append(
                f'<li><a href="{esc(s["url"])}" rel="noopener">{esc(s["title"])}</a>'
                f'<span class="badge {kind}">{esc(label)}</span>{lang}{note}</li>'
            )
        out.append("</ul>")
    return "".join(out)


def render_offers(ctx: Ctx, code: str) -> str:
    offers = [
        a for a in ctx.site.get("affiliates", [])
        if not a.get("countries") or code in a["countries"]
    ]
    if not offers:
        return ""
    items = "".join(
        f'<li><a href="{esc(a["url"])}" rel="sponsored nofollow noopener">{esc(a["title"])}</a>'
        f'<span class="meta">{esc(a.get("description", ""))}</span></li>'
        for a in offers
    )
    return (
        '<h2>Корисні сервіси</h2>'
        '<p class="ad">Партнерські посилання: якщо ви скористаєтесь ними, сайт може отримати комісію. Ціна для вас не змінюється.</p>'
        f'<ul class="src">{items}</ul>'
    )


def render_country(ctx: Ctx, c: dict) -> str:
    facts = c.get("facts", [])
    notice = ""
    if not facts:
        notice = (
            '<div class="notice">Для цієї країни ми ще не маємо підтверджених фактів. '
            "Ми не публікуємо правила, які не перевірили на офіційному сайті. Скористайтеся джерелами нижче.</div>"
        )
    body = (
        f"<h1>{esc(c['name_uk'])}</h1>"
        f'<p class="lead">{esc(c["summary"])} <span class="meta">Оновлено: {esc(c["updated"])}</span></p>'
        f"{notice}"
        + ("<h2>Що ми перевірили</h2>" + render_facts(facts) if facts else "")
        + "<h2>Офіційні джерела</h2>"
        + render_sources(c.get("sources", []), ctx.links)
        + render_offers(ctx, c["code"])
    )
    return layout(ctx, c["name_uk"], c["summary"], body, 1, f"{c['code']}/index.html", c["code"])


def render_changes_list(changes: list[dict], limit: int | None = None) -> str:
    if not changes:
        return "<p>Поки що змін у джерелах не зафіксовано. Перевірка виконується щодня.</p>"
    rows = changes[:limit] if limit else changes
    items = []
    for ch in rows:
        label = CHANGE_LABELS.get(ch.get("type", ""), "Зміна у джерелі.")
        items.append(
            f'<li><strong>{esc(ch["date"])}</strong> · {esc(ch.get("country", ""))}: '
            f'<a href="{esc(ch["url"])}" rel="noopener">{esc(ch["title"])}</a>'
            f'<span class="meta">{esc(label)}</span></li>'
        )
    return '<ul class="log">' + "".join(items) + "</ul>"


def render_home(ctx: Ctx) -> str:
    eu = next((c for c in ctx.countries if c["code"] == "eu"), None)
    hero = ""
    if eu and eu.get("facts"):
        points = "".join(f"<li>{esc(f['text'])}</li>" for f in eu["facts"][:2])
        hero = (
            '<div class="hero"><h2>Головне зараз</h2>'
            f'<ul style="margin:0;padding-left:20px">{points}</ul>'
            f'<span class="meta">Детальніше і джерела: <a href="eu/index.html">{esc(eu["name_uk"])}</a></span></div>'
        )
    cards = "".join(
        f'<li><a class="card" href="{esc(c["code"])}/index.html"><strong>{esc(c["name_uk"])}</strong>'
        f'<span>Перевірених фактів: {len(c.get("facts", []))}, джерел: {len(c.get("sources", []))}</span></a></li>'
        for c in ctx.countries
    )
    body = (
        f"<h1>{esc(ctx.site['title'])}</h1>"
        f'<p class="lead">{esc(ctx.site.get("tagline", ""))}</p>'
        f"{hero}"
        f'<h2>Країни</h2><ul class="cards">{cards}</ul>'
        f"<h2>Останні зміни в офіційних джерелах</h2>{render_changes_list(ctx.changes, 5)}"
        '<p><a href="changes.html">Весь журнал змін</a></p>'
    )
    return layout(ctx, ctx.site["title"], ctx.site.get("tagline", ""), body, 0, "index.html", "home")


def render_changes(ctx: Ctx) -> str:
    body = (
        "<h1>Що змінилося в офіційних джерелах</h1>"
        '<p class="lead">Програма щодня перевіряє офіційні сторінки з нашого списку. '
        "Запис означає лише те, що сторінка змінилась. Зміст ми перевіряємо вручну перед оновленням довідника.</p>"
        f"{render_changes_list(ctx.changes)}"
    )
    return layout(ctx, "Що змінилося", "Журнал змін в офіційних джерелах", body, 0, "changes.html", "changes")


def render_about(ctx: Ctx) -> str:
    email = ctx.site.get("contact_email", "")
    contact = f"<p>Помітили помилку? Напишіть: <a href=\"mailto:{esc(email)}\">{esc(email)}</a>.</p>" if email else ""
    body = (
        "<h1>Про сайт</h1>"
        "<p>Це довідник з офіційними посиланнями для українців, які мають тимчасовий захист у країнах ЄС. "
        "Ми не є державним органом і не надаємо юридичних консультацій.</p>"
        "<h2>Як ми працюємо</h2>"
        "<ul><li>Факти публікуємо лише з посиланням на офіційне джерело і датою перевірки.</li>"
        "<li>Якщо джерело не перевірене, поруч стоїть позначка «ще не перевірено».</li>"
        "<li>Програма щодня відстежує зміни на офіційних сторінках і фіксує їх у журналі.</li>"
        "<li>Тексти довідника не оновлюються автоматично: після зміни джерела людина перевіряє і виправляє.</li></ul>"
        "<h2>Реклама і партнерські посилання</h2>"
        "<p>Сайт може містити рекламу та партнерські посилання. Вони позначені окремо і не впливають на фактичну частину довідника.</p>"
        f"{contact}"
    )
    return layout(ctx, "Про сайт", "Як працює довідник і чому йому можна довіряти", body, 0, "about.html", "about")


def render_404(ctx: Ctx) -> str:
    base = urlparse(ctx.base_url).path.rstrip("/") if ctx.base_url else ""
    home = f"{base}/index.html" if ctx.base_url else "index.html"
    body = f'<h1>Сторінку не знайдено</h1><p><a href="{esc(home)}">На головну</a></p>'
    page = layout(ctx, "Сторінку не знайдено", "Такої сторінки немає", body, 0, "404.html")
    if ctx.base_url:
        page = page.replace('href="index.html"', f'href="{esc(home)}"').replace(
            'href="assets/style.css"', f'href="{esc(base)}/assets/style.css"'
        )
    return page


def render_sitemap(ctx: Ctx) -> str:
    paths = ["index.html", "changes.html", "about.html"] + [f"{c['code']}/index.html" for c in ctx.countries]
    rows = "".join(
        f"<url><loc>{esc(ctx.base_url + '/' + p)}</loc><lastmod>{esc(ctx.today)}</lastmod></url>" for p in paths
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'
    )


def render_feed(ctx: Ctx) -> str:
    items = []
    for ch in ctx.changes[:20]:
        label = CHANGE_LABELS.get(ch.get("type", ""), "Зміна у джерелі.")
        guid = f"{ch['date']}|{ch['url']}|{ch.get('type', '')}"
        items.append(
            f"<item><title>{esc(ch.get('country', ''))}: {esc(ch['title'])}</title>"
            f"<link>{esc(ctx.base_url + '/changes.html')}</link>"
            f'<guid isPermaLink="false">{esc(guid)}</guid>'
            f"<description>{esc(label)}</description><pubDate>{esc(rfc822(ch['date']))}</pubDate></item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{esc(ctx.site['title'])}: зміни</title><link>{esc(ctx.base_url)}</link>"
        f"<description>Зміни в офіційних джерелах</description>{''.join(items)}</channel></rss>\n"
    )


def rfc822(date_str: str) -> str:
    d = dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(data_dir: Path, out_dir: Path, base_url: str = "", today: str | None = None) -> list[str]:
    data = load_data(data_dir)
    problems = validate_data(data)
    if problems:
        return problems
    today = today or dt.date.today().isoformat()
    ctx = Ctx(data, base_url, today)
    write(out_dir / "assets" / "style.css", CSS)
    write(out_dir / "index.html", render_home(ctx))
    write(out_dir / "changes.html", render_changes(ctx))
    write(out_dir / "about.html", render_about(ctx))
    write(out_dir / "404.html", render_404(ctx))
    for c in ctx.countries:
        write(out_dir / c["code"] / "index.html", render_country(ctx, c))
    write(out_dir / ".nojekyll", "")
    # файл підтвердження Google Search Console (googleXXXXXXXXXXXXXXXX.html) копіюється на сайт без змін
    for f in data_dir.glob("google*.html"):
        if GOOGLE_FILE_RE.match(f.name):
            shutil.copy(f, out_dir / f.name)
    if ctx.base_url:
        write(out_dir / "sitemap.xml", render_sitemap(ctx))
        write(out_dir / "feed.xml", render_feed(ctx))
        write(out_dir / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {ctx.base_url}/sitemap.xml\n")
    else:
        write(out_dir / "robots.txt", "User-agent: *\nAllow: /\n")
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", default=str(ROOT))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--base-url", default=os.environ.get("SITE_URL", ""))
    a = ap.parse_args(argv)
    problems = build(Path(a.data), Path(a.out), a.base_url)
    if problems:
        print("Ошибки в данных, сайт не собран:", file=sys.stderr)
        for p in problems:
            print(" -", p, file=sys.stderr)
        return 1
    n = sum(1 for _ in Path(a.out).rglob("*.html"))
    print(f"Готово: {n} HTML-страниц в {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
