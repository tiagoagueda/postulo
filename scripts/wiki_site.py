"""The pre-install pages of the wiki, as a static site in every language that has one (#352).

    python scripts/wiki_site.py build  ../postulo.wiki site/        # the whole site
    python scripts/wiki_site.py stale  ../postulo.wiki [--strict]   # which source moved
    python scripts/wiki_site.py stamp  ../postulo.wiki fr [PAGE...] # fr read against English
    python scripts/wiki_site.py components ../postulo.wiki          # what Weblate needs, as JSON

Only the pages a reader meets before they have an instance (``PUBLIC_PAGES``) are built; the
rest of the wiki stays English on Forgejo, and what a person reads while using Postulo is the
in-app help, translated through the gettext catalogues (#302). The layout of the wiki
repository this reads:

    Getting-started.md              the English source, which Forgejo shows as it is
    site.json                       the site's own few words (banners, the language list)
    lang/<tag>/Getting-started.md   a translation, <tag> a BCP 47 tag in canonical form
    lang/<tag>/site.json            the same words in that language, keys it lacks fall back
    lang/stamps.json                {"fr": {"Getting-started": "<hash>"}}

A page with no translation is built from the English, with a banner saying so. A translation
whose English has changed since it was stamped is built too, with a banner saying it may
describe an older Postulo; ``stale`` lists them and, with ``--strict``, fails. The hash is of the
English file's text, so it does not depend on git and survives whatever a translation platform
does to the translated file. No script and no third-party request in the output: it is HTML,
one stylesheet and the wiki's images, in both colour schemes and right to left where the
language is.

Needs ``markdown-it-py``, which Postulo already depends on, and nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import re
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent

#: The pages a reader meets before they have an instance, in the order the site lists them.
PUBLIC_PAGES = ("Home", "Installing-Postulo", "Getting-started")

#: Where the pages that are not built live, and so where a link to one goes.
WIKI_URL = "https://source.tiagoagueda.com/postulo/postulo/wiki"

SOURCE_LANGUAGE = "en-GB"

#: The site's own words, as the wiki's ``site.json`` holds them; the fallback when it is missing.
SITE_DEFAULTS = {
    "untranslated": "This page has not been translated yet, so it is shown in English.",
    "outdated": (
        "The English page has changed since this translation was read, "
        "so it may describe an older Postulo."
    ),
    "languages": "Languages",
    "pages": "Pages",
    "choose": "Choose a language",
    "more": "The rest of the documentation is in English on the wiki.",
}

# What the wiki's Markdown may carry as raw HTML: a picture, a link, a few inline tags.
ALLOWED_TAGS = frozenset({"img", "a", "br", "kbd", "sup", "sub", "details", "summary", "p"})
ALLOWED_ATTRS = {
    "img": {"src", "alt", "width", "height", "title"},
    "a": {"href", "title"},
}
SAFE_SCHEMES = {"", "http", "https", "mailto"}


def _languages_module():
    """``postulo.core.languages`` without importing the Django project around it."""
    path = ROOT / "src" / "postulo" / "core" / "languages.py"
    spec = importlib.util.spec_from_file_location("_postulo_languages", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_postulo_languages"] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------- sanitising


class _Sanitiser(HTMLParser):
    """Raw HTML in a page, reduced to ``ALLOWED_TAGS`` and their safe attributes.

    A translation arrives from a platform anybody with an account can write to, so what it
    may smuggle into a page is held to a list and not left to the wiki's good faith.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        kept = []
        for name, value in attrs:
            if name not in ALLOWED_ATTRS.get(tag, ()) or value is None:
                continue
            if name in {"src", "href"} and urlsplit(value).scheme.lower() not in SAFE_SCHEMES:
                continue
            if tag == "img" and name == "src" and not urlsplit(value).scheme:
                value = value if value.startswith("/") else "../" + value
            kept.append(f' {name}="{html.escape(value, quote=True)}"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_endtag(self, tag):
        if tag in ALLOWED_TAGS and tag not in {"img", "br"}:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))


def sanitise(raw: str) -> str:
    parser = _Sanitiser()
    parser.feed(raw)
    parser.close()
    return "".join(parser.out)


# ---------------------------------------------------------------------- reading


def source_hash(text: str) -> str:
    """Twelve hex digits of the English text, line endings and trailing space ignored."""
    normal = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:12]


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


class Wiki:
    """The wiki repository, as far as the site is concerned."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.languages = _languages_module()

    def source(self, page: str) -> str:
        return (self.root / f"{page}.md").read_text(encoding="utf-8")

    def translation_path(self, tag: str, page: str) -> Path:
        return self.root / "lang" / tag / f"{page}.md"

    def tags(self) -> list[str]:
        """Every language with a directory, sorted; a directory that is not a tag is ignored."""
        base = self.root / "lang"
        if not base.is_dir():
            return []
        found = [p.name for p in base.iterdir() if p.is_dir() and _canonical(p.name) == p.name]
        return sorted(found)

    def stamps(self) -> dict:
        return read_json(self.root / "lang" / "stamps.json")

    def words(self, tag: str | None) -> dict[str, str]:
        words = dict(SITE_DEFAULTS)
        words.update(read_json(self.root / "site.json"))
        if tag:
            words.update(read_json(self.root / "lang" / tag / "site.json"))
        return {key: str(value) for key, value in words.items()}

    def name_of(self, tag: str) -> str:
        return self.languages.native_name(tag, tag)

    def is_rtl(self, tag: str) -> bool:
        return self.languages.is_rtl(tag)

    def state(self, tag: str, page: str) -> str:
        """``current``, ``outdated`` (source moved or never stamped) or ``missing``."""
        if not self.translation_path(tag, page).is_file():
            return "missing"
        stamped = self.stamps().get(tag, {}).get(page)
        return "current" if stamped == source_hash(self.source(page)) else "outdated"


def _canonical(tag: str) -> str:
    """BCP 47 canonical case: ``pt-br`` is ``pt-BR``, ``sr-latn`` is ``sr-Latn``."""
    parts = tag.replace("_", "-").split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4:
            out.append(part.title())
        elif (len(part) <= 3 and part.isalpha()) or part.isdigit():
            out.append(part.upper())
        else:
            out.append(part.lower())
    return "-".join(out)


# ----------------------------------------------------------------------- render


def markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    md.add_render_rule("html_block", lambda _self, tokens, i, *_: sanitise(tokens[i].content))
    md.add_render_rule("html_inline", lambda _self, tokens, i, *_: sanitise(tokens[i].content))
    return md


def title_of(text: str, page: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*#*\s*$", text, re.MULTILINE)
    return match.group(1) if match else page.replace("-", " ")


def rewrite_links(md: MarkdownIt, tokens, built: set[str], depth_prefix: str) -> None:
    """Links between pages stay inside the language; the rest go to the English wiki."""
    for token in tokens:
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                child.attrSet("href", _link(href, built))
            elif child.type == "image":
                src = child.attrGet("src") or ""
                if urlsplit(src).scheme == "" and not src.startswith("/"):
                    child.attrSet("src", depth_prefix + src)


def _link(href: str, built: set[str]) -> str:
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or href.startswith(("#", "/")):
        return href if parts.scheme in SAFE_SCHEMES or not parts.scheme else "#"
    page = parts.path
    frag = f"#{parts.fragment}" if parts.fragment else ""
    if page in built:
        return _name(page) + ".html" + frag
    return f"{WIKI_URL}/{quote(page)}{frag}"


def page_html(
    wiki: Wiki,
    tag: str,
    page: str,
    tags: list[str],
    text: str,
    notice: str,
    md: MarkdownIt,
    english: bool = False,
) -> str:
    words = wiki.words(tag)
    built = set(PUBLIC_PAGES)
    tokens = md.parse(text)
    rewrite_links(md, tokens, built, "../")
    body = md.renderer.render(tokens, md.options, {})
    if english:
        # English text under another language's page: say so to screen readers, and keep it
        # left to right even where the page is right to left.
        body = f'<div lang="{SOURCE_LANGUAGE}" dir="ltr">\n{body}</div>\n'
    here = ' aria-current="page"'
    nav = "".join(
        f'<li><a href="{_name(p)}.html"{here if p == page else ""}>'
        f"{html.escape(title_of(_text_for(wiki, tag, p), p))}</a></li>"
        for p in PUBLIC_PAGES
    )
    switch = "".join(
        f'<li><a href="../{t}/{_name(page)}.html" lang="{t}" hreflang="{t}"'
        f"{here if t == tag else ''}>{html.escape(wiki.name_of(t))}</a></li>"
        for t in tags
    )
    banner = f'<p class="notice" role="note">{html.escape(notice)}</p>' if notice else ""
    direction = "rtl" if wiki.is_rtl(tag) else "ltr"
    return (
        f'<!doctype html>\n<html lang="{tag}" dir="{direction}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title_of(text, page))} · Postulo</title>\n"
        '<link rel="stylesheet" href="../style.css">\n</head>\n<body>\n'
        f'<nav aria-label="{html.escape(words["pages"])}"><ul>{nav}</ul>'
        f"<p>{html.escape(words['more'])}</p></nav>\n"
        f'<nav aria-label="{html.escape(words["languages"])}">'
        f'<ul class="languages">{switch}</ul></nav>\n'
        f"<main>\n{banner}\n{body}</main>\n</body>\n</html>\n"
    )


def _name(page: str) -> str:
    return "index" if page == "Home" else page


def _text_for(wiki: Wiki, tag: str, page: str) -> str:
    path = wiki.translation_path(tag, page)
    return path.read_text(encoding="utf-8") if path.is_file() else wiki.source(page)


STYLE = """\
:root { color-scheme: light dark; --bg: #fff; --fg: #1b1b1f; --muted: #52525b;
  --link: #1d4ed8; --rule: #d4d4d8; --notice: #fef3c7; --notice-fg: #451a03; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #131316; --fg: #ececf1; --muted: #a1a1aa; --link: #93c5fd;
    --rule: #3f3f46; --notice: #422006; --notice-fg: #fef3c7; } }
body { margin: 0 auto; max-inline-size: 48rem; padding: 1rem; background: var(--bg);
  color: var(--fg); font: 1rem/1.6 system-ui, sans-serif; }
a { color: var(--link); }
nav ul { display: flex; flex-wrap: wrap; gap: .25rem 1rem; padding: 0; list-style: none; }
nav [aria-current] { color: var(--fg); font-weight: 600; }
.languages { border-block-end: 1px solid var(--rule); padding-block-end: .5rem; }
.notice { padding: .75rem 1rem; background: var(--notice); color: var(--notice-fg);
  border-radius: .25rem; }
img { max-inline-size: 100%; block-size: auto; }
pre, code { font-family: ui-monospace, monospace; }
pre { overflow-x: auto; padding: .75rem; border: 1px solid var(--rule); }
blockquote { margin-inline: 0; padding-inline-start: 1rem;
  border-inline-start: 3px solid var(--rule); color: var(--muted); }
table { border-collapse: collapse; }
td, th { border: 1px solid var(--rule); padding: .25rem .5rem; }
"""


def build(wiki: Wiki, out: Path) -> list[str]:
    """Write the site; return the files written, relative to ``out``."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    md = markdown()
    tags = [SOURCE_LANGUAGE, *wiki.tags()]
    written: list[str] = []

    def write(rel: str, content: str) -> None:
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)

    write("style.css", STYLE)
    if (wiki.root / "images").is_dir():
        shutil.copytree(wiki.root / "images", out / "images")
    for tag in tags:
        words = wiki.words(None if tag == SOURCE_LANGUAGE else tag)
        for page in PUBLIC_PAGES:
            state = "current"
            if tag == SOURCE_LANGUAGE:
                text, notice = wiki.source(page), ""
            else:
                state = wiki.state(tag, page)
                if state == "missing":
                    text, notice = wiki.source(page), words["untranslated"]
                else:
                    text = wiki.translation_path(tag, page).read_text(encoding="utf-8")
                    notice = words["outdated"] if state == "outdated" else ""
            fallback = tag != SOURCE_LANGUAGE and state == "missing"
            write(
                f"{tag}/{_name(page)}.html",
                page_html(wiki, tag, page, tags, text, notice, md, english=fallback),
            )
    write("index.html", landing(wiki, tags))
    return written


def landing(wiki: Wiki, tags: list[str]) -> str:
    items = "".join(
        f'<li><a href="{t}/index.html" lang="{t}" hreflang="{t}">'
        f"{html.escape(wiki.name_of(t))}</a></li>"
        for t in tags
    )
    return (
        '<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Postulo</title>\n"
        '<link rel="stylesheet" href="style.css">\n</head>\n<body>\n<main>\n'
        f"<h1>Postulo</h1>\n<h2>{html.escape(wiki.words(None)['choose'])}</h2>\n"
        f'<ul class="languages">{items}</ul>\n</main>\n</body>\n</html>\n'
    )


# -------------------------------------------------------------------- staleness


def stale(wiki: Wiki) -> list[tuple[str, str, str]]:
    """``(tag, page, state)`` for every translation that is not current."""
    rows = []
    for tag in wiki.tags():
        for page in PUBLIC_PAGES:
            state = wiki.state(tag, page)
            if state == "outdated":
                rows.append((tag, page, state))
    return rows


def stamp(wiki: Wiki, tag: str, pages: list[str]) -> list[str]:
    pages = pages or [p for p in PUBLIC_PAGES if wiki.translation_path(tag, p).is_file()]
    unknown = [
        p for p in pages if p not in PUBLIC_PAGES or not wiki.translation_path(tag, p).is_file()
    ]
    if unknown:
        raise SystemExit(f"No {tag} translation of: {', '.join(unknown)}")
    stamps = wiki.stamps()
    for page in pages:
        stamps.setdefault(tag, {})[page] = source_hash(wiki.source(page))
    path = wiki.root / "lang" / "stamps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pages


def components(wiki: Wiki) -> list[dict]:
    """One Weblate component per page, plus one for the site's words.

    Weblate reads the English file as the template and writes ``lang/<code>/<page>.md``; the
    component is made with the *BCP* language code style so ``pt_PT`` is written ``pt-PT``.
    """
    rows = [
        {
            "name": f"Wiki: {page.replace('-', ' ')}",
            "slug": f"wiki-{page.lower()}",
            "file_format": "markdown",
            "filemask": f"lang/*/{page}.md",
            "new_base": f"{page}.md",
            "template": f"{page}.md",
            "language_code_style": "bcp",
        }
        for page in PUBLIC_PAGES
    ]
    rows.append(
        {
            "name": "Wiki: site words",
            "slug": "wiki-site",
            "file_format": "json",
            "filemask": "lang/*/site.json",
            "new_base": "site.json",
            "template": "site.json",
            "language_code_style": "bcp",
        }
    )
    return rows


# -------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "stale", "stamp", "components"):
        cmd = sub.add_parser(name)
        cmd.add_argument("wiki", type=Path, help="a clone of postulo.wiki")
        if name == "build":
            cmd.add_argument("out", type=Path)
        if name == "stale":
            cmd.add_argument("--strict", action="store_true", help="exit 1 if any is stale")
        if name == "stamp":
            cmd.add_argument("tag")
            cmd.add_argument("pages", nargs="*")
    args = parser.parse_args(argv)
    wiki = Wiki(args.wiki)
    if args.command == "build":
        files = build(wiki, args.out)
        print(f"{len(files)} files in {args.out}")
    elif args.command == "stale":
        rows = stale(wiki)
        for tag, page, _ in rows:
            print(f"{tag}\t{page}\tsource changed since it was read")
        if not rows:
            print("every translation is current")
        return 1 if rows and args.strict else 0
    elif args.command == "stamp":
        for page in stamp(wiki, _canonical(args.tag), args.pages):
            print(f"stamped {args.tag} {page}")
    else:
        print(json.dumps(components(wiki), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
