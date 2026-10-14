import json
import shutil
from pathlib import Path

import build

REAL_DATA = Path(__file__).resolve().parent


def make_data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    for f in [REAL_DATA / "site.json", *REAL_DATA.glob("country_*.json")]:
        shutil.copy(f, data / f.name)
    return data


def test_real_data_is_valid(tmp_path):
    assert build.validate_data(build.load_data(make_data(tmp_path))) == []


def test_build_creates_pages(tmp_path):
    out = tmp_path / "dist"
    problems = build.build(make_data(tmp_path), out, "https://example.github.io/guide", "2026-10-13")
    assert problems == []
    for rel in ["index.html", "about.html", "changes.html", "404.html", "eu/index.html",
                "pl/index.html", "de/index.html", "cz/index.html", "sitemap.xml", "feed.xml",
                "robots.txt", "assets/style.css"]:
        assert (out / rel).exists(), rel
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://example.github.io/guide/pl/index.html" in sitemap
    assert "Sitemap: https://example.github.io/guide/sitemap.xml" in (out / "robots.txt").read_text()


def test_relative_links_depth(tmp_path):
    out = tmp_path / "dist"
    build.build(make_data(tmp_path), out, "", "2026-10-13")
    country = (out / "pl" / "index.html").read_text(encoding="utf-8")
    home = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="../assets/style.css"' in country
    assert 'href="assets/style.css"' in home
    assert not (out / "sitemap.xml").exists()


def test_country_without_facts_shows_notice_and_unverified_badge(tmp_path):
    out = tmp_path / "dist"
    build.build(make_data(tmp_path), out, "", "2026-10-13")
    cz = (out / "cz" / "index.html").read_text(encoding="utf-8")
    assert "ще не маємо підтверджених фактів" in cz
    assert "ще не перевірено" in cz
    assert "Що ми перевірили" not in cz


def test_facts_have_source_and_date(tmp_path):
    out = tmp_path / "dist"
    build.build(make_data(tmp_path), out, "", "2026-10-13")
    eu = (out / "eu" / "index.html").read_text(encoding="utf-8")
    assert "2028" in eu
    assert "перевірено 2026-10-13" in eu
    assert "eprs.europarl.europa.eu" in eu


def test_html_is_escaped(tmp_path):
    data = make_data(tmp_path)
    p = data / "country_pl.json"
    c = json.loads(p.read_text(encoding="utf-8"))
    c["facts"][0]["text"] = '<script>alert("x")</script>'
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "dist"
    assert build.build(data, out, "", "2026-10-13") == []
    page = (out / "pl" / "index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_validation_catches_bad_data(tmp_path):
    data = make_data(tmp_path)
    p = data / "country_pl.json"
    c = json.loads(p.read_text(encoding="utf-8"))
    c["facts"][0]["source_url"] = "http://insecure.example"
    c["facts"][1]["checked"] = "вчора"
    c["sources"][0]["url"] = "ftp://x"
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    problems = build.validate_data(build.load_data(data))
    assert len(problems) >= 3
    assert build.build(data, tmp_path / "dist", "", "2026-10-13") == problems
    assert not (tmp_path / "dist" / "index.html").exists()


def test_affiliates_marked_and_adsense_optional(tmp_path):
    data = make_data(tmp_path)
    site = json.loads((data / "site.json").read_text(encoding="utf-8"))
    site["affiliates"] = [{"title": "SIM", "url": "https://partner.example/x", "countries": ["pl"]}]
    site["adsense_client"] = "ca-pub-1234567890123456"
    (data / "site.json").write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "dist"
    assert build.build(data, out, "", "2026-10-13") == []
    pl = (out / "pl" / "index.html").read_text(encoding="utf-8")
    de = (out / "de" / "index.html").read_text(encoding="utf-8")
    assert 'rel="sponsored nofollow noopener"' in pl
    assert "Партнерські посилання" in pl
    assert "partner.example" not in de
    assert "client=ca-pub-1234567890123456" in pl


def test_default_site_has_no_ads(tmp_path):
    out = tmp_path / "dist"
    build.build(make_data(tmp_path), out, "", "2026-10-13")
    assert "adsbygoogle" not in (out / "index.html").read_text(encoding="utf-8")


def test_bad_adsense_id_rejected(tmp_path):
    data = make_data(tmp_path)
    site = json.loads((data / "site.json").read_text(encoding="utf-8"))
    site["adsense_client"] = "<script>"
    (data / "site.json").write_text(json.dumps(site), encoding="utf-8")
    assert any("adsense" in p for p in build.validate_data(build.load_data(data)))


def test_changes_rendered_on_home(tmp_path):
    data = make_data(tmp_path)
    (data / "changes.json").write_text(json.dumps([
        {"date": "2026-10-12", "country": "Польща", "title": "UdSC", "url": "https://www.gov.pl/web/udsc", "type": "changed"}
    ]), encoding="utf-8")
    out = tmp_path / "dist"
    build.build(data, out, "https://x.example/g", "2026-10-13")
    assert "UdSC" in (out / "index.html").read_text(encoding="utf-8")
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert "Польща: UdSC" in feed
    assert "12 Oct 2026" in feed


def test_base_url_host_is_lowercased(tmp_path):
    out = tmp_path / "dist"
    assert build.build(make_data(tmp_path), out, "https://Soulcoin6287.GitHub.io/ukr-guide/", "2026-10-13") == []
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://soulcoin6287.github.io/ukr-guide/index.html" in sitemap
    assert "Soulcoin6287" not in sitemap
    assert build.normalize_base("") == ""


def test_google_verification_file_is_published(tmp_path):
    data = make_data(tmp_path)
    (data / "google0123456789abcdef.html").write_text("google-site-verification: google0123456789abcdef.html", encoding="utf-8")
    (data / "googleevil.html").write_text("не повинен потрапити на сайт", encoding="utf-8")
    out = tmp_path / "dist"
    assert build.build(data, out, "https://x.github.io/g", "2026-10-13") == []
    assert (out / "google0123456789abcdef.html").read_text(encoding="utf-8").startswith("google-site-verification")
    assert not (out / "googleevil.html").exists()
