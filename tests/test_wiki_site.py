"""The wiki's pre-install pages as a static site in several languages (#352)."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wiki_site", ROOT / "scripts" / "wiki_site.py")
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)

ENGLISH = {
    "Home": "# Postulo\n\nSee [Getting started](Getting-started) and [Hardening](Hardening).\n\n"
    '<img src="images/logo.png" alt="logo"> <script>alert(1)</script>\n',
    "Installing-Postulo": "# Installing Postulo\n\nRun it.\n",
    "Getting-started": "# Getting started\n\n![The dashboard](images/dashboard.png)\n\nSign in.\n",
}


@pytest.fixture
def wiki(tmp_path):
    for page, text in ENGLISH.items():
        (tmp_path / f"{page}.md").write_text(text, encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "dashboard.png").write_bytes(b"png")
    fr = tmp_path / "lang" / "fr"
    fr.mkdir(parents=True)
    (fr / "Getting-started.md").write_text(
        "# Premiers pas\n\n![Le tableau](images/dashboard.png)\n\nConnectez-vous.\n",
        encoding="utf-8",
    )
    (fr / "site.json").write_text(json.dumps({"untranslated": "Pas encore traduite."}))
    (fr / "Home.md").write_text("# Postulo\n\nBonjour <script>x()</script>.\n", encoding="utf-8")
    (tmp_path / "lang" / "stamps.json").write_text(
        json.dumps({"fr": {"Getting-started": site.source_hash(ENGLISH["Getting-started"])}})
    )
    return site.Wiki(tmp_path)


def read(out, rel):
    return (out / rel).read_text(encoding="utf-8")


def test_each_language_gets_every_page_and_a_landing_lists_them(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    for tag in ("en-GB", "fr"):
        for name in ("index", "Installing-Postulo", "Getting-started"):
            assert (out / tag / f"{name}.html").is_file()
    landing = read(out, "index.html")
    assert 'href="fr/index.html"' in landing and 'href="en-GB/index.html"' in landing
    assert (out / "images" / "dashboard.png").is_file()


def test_a_page_without_a_translation_is_english_and_says_so(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    page = read(out, "fr/Installing-Postulo.html")
    assert "Pas encore traduite." in page and "Run it." in page
    assert 'lang="fr"' in page
    assert '<div lang="en-GB" dir="ltr">' in page
    assert "<div lang" not in read(out, "fr/Getting-started.html")
    assert "notice" not in read(out, "en-GB/Installing-Postulo.html").split("<main>")[1]


def test_a_current_translation_has_no_banner(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    page = read(out, "fr/Getting-started.html")
    assert "Connectez-vous." in page and 'class="notice"' not in page
    assert "../images/dashboard.png" in page


def test_a_moved_source_makes_the_translation_stale_and_banners_it(wiki, tmp_path):
    assert site.stale(wiki) == [("fr", "Home", "outdated")]
    (wiki.root / "Getting-started.md").write_text("# Getting started\n\nChanged.\n")
    assert ("fr", "Getting-started", "outdated") in site.stale(wiki)
    out = tmp_path / "site"
    site.build(wiki, out)
    assert 'class="notice"' in read(out, "fr/Getting-started.html")
    assert site.main(["stale", str(wiki.root), "--strict"]) == 1
    site.stamp(wiki, "fr", [])
    assert site.stale(wiki) == []
    assert site.main(["stale", str(wiki.root), "--strict"]) == 0


def test_the_hash_ignores_line_endings_and_trailing_space():
    assert site.source_hash("a  \r\nb\r\n") == site.source_hash("a\nb")
    assert site.source_hash("a") != site.source_hash("b")


def test_links_stay_in_the_language_and_the_rest_go_to_the_wiki(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    home = read(out, "en-GB/index.html")
    assert 'href="Getting-started.html"' in home
    assert f'href="{site.WIKI_URL}/Hardening"' in home


def test_raw_html_is_reduced_to_what_a_page_may_carry(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    for tag in ("en-GB", "fr"):
        home = read(out, f"{tag}/index.html")
        assert "<script" not in home
    assert '<img src="../images/logo.png" alt="logo">' in read(out, "en-GB/index.html")
    assert site.sanitise('<a href="javascript:x()">y</a>') == "<a>y</a>"
    assert site.sanitise('<img src="x" onerror="y()">') == '<img src="../x">'


def test_a_right_to_left_language_is_laid_out_so(tmp_path):
    for page, text in ENGLISH.items():
        (tmp_path / f"{page}.md").write_text(text, encoding="utf-8")
    (tmp_path / "lang" / "ar").mkdir(parents=True)
    wiki = site.Wiki(tmp_path)
    site.build(wiki, tmp_path / "out")
    assert 'dir="rtl"' in read(tmp_path / "out", "ar/index.html")
    assert 'dir="ltr"' in read(tmp_path / "out", "en-GB/index.html")


def test_the_output_asks_for_nothing_from_anyone_else(wiki, tmp_path):
    out = tmp_path / "site"
    site.build(wiki, out)
    for path in out.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "<script" not in text and "http://" not in text
        assert 'src="http' not in text and "@import" not in text


def test_a_directory_that_is_not_a_tag_is_not_a_language(wiki):
    (wiki.root / "lang" / "pt_pt").mkdir()
    (wiki.root / "lang" / "pt-PT").mkdir()
    assert wiki.tags() == ["fr", "pt-PT"]
    assert site._canonical("pt_pt") == "pt-PT" and site._canonical("sr-latn") == "sr-Latn"


def test_weblate_gets_one_component_a_page_and_one_for_the_words(wiki):
    rows = site.components(wiki)
    assert [r["filemask"] for r in rows] == [
        "lang/*/Home.md",
        "lang/*/Installing-Postulo.md",
        "lang/*/Getting-started.md",
        "lang/*/site.json",
    ]
    assert all(r["language_code_style"] == "bcp" for r in rows)
