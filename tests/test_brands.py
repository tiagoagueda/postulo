"""The brand marks: the tag, the agreement between the list and the files, and the rule.

In the shape of ``tests/test_icons.py``, with what a mark adds: it is somebody else's logo,
so every one has an owner line and a mode in its notice. A ``brand`` mark is drawn only in
the colour its owner published and is shipped only where that colour clears 3:1 on both
pages; a ``single-colour`` one only where its line in the list records the owner's
guidelines allowing it, and is drawn black on the light page and white on the dark one
(#654).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError

from postulo.core.brands import BRAND_DIR
from tests.test_contrast import CSS, contrast, tokens

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "src" / "postulo" / "templates"
BRAND_LIST = REPO / "assets" / "brands.txt"
LICENCES = REPO / "assets" / "brand-terms.txt"
NOTICE = BRAND_DIR / "NOTICE.txt"


def render(source: str) -> str:
    return Template("{% load postulo %}" + source).render(Context())


def lines_of(path: Path) -> list[str]:
    """The lines of a list file, a comment being a ``#`` that starts the line or follows a space."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*", "", line).strip()
        if line:
            found.append(line)
    return found


def entries(path: Path) -> list[str]:
    return [line.split()[0] for line in lines_of(path)]


def listed() -> set[str]:
    return set(entries(BRAND_LIST))


def modes() -> dict[str, str]:
    """Each listed mark's mode: the second word of its line, ``brand`` where there is none."""
    return {line.split()[0]: ([*line.split(), "brand"])[1] for line in lines_of(BRAND_LIST)}


def single_colour() -> set[str]:
    return {slug for slug, mode in modes().items() if mode == "single-colour"}


def page_colours() -> dict[str, str]:
    """The two colours the stylesheet gives a one-colour mark, from the rule itself."""
    block = re.search(r'\[data-brand-mode="single-colour"\]\s*\{(.*?)\n  \}', CSS, re.S).group(1)
    light = re.match(r"\s*color:\s*#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", block).group(1)
    dark = re.search(
        r"@variant dark\s*\{\s*color:\s*#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", block
    ).group(1)
    return {"light": _six(light), "dark": _six(dark)}


def _six(digits: str) -> str:
    return "".join(c * 2 for c in digits) if len(digits) == 3 else digits


def notice_blocks() -> dict[str, str]:
    """Each mark's paragraph in NOTICE.txt, by slug: the header says what is not a mark."""
    text = NOTICE.read_text(encoding="utf-8")
    blocks = {}
    for block in re.split(r"\n\n(?=[a-z0-9-]+\n  Owner:)", text):
        match = re.match(r"([a-z0-9-]+)\n  Owner:", block)
        if match:
            blocks[match.group(1)] = block
    return blocks


def luminance_of(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


# ------------------------------------------------------------------------------ the tag


def test_a_brand_is_inline_svg_decorative_by_default_and_marked_with_data_brand():
    html = render('{% brand "mastodon" %}')
    assert html.startswith("<svg ")
    assert 'data-brand="mastodon"' in html
    assert 'aria-hidden="true"' in html
    assert 'class="size-4 shrink-0"' in html
    assert 'viewBox="0 0 24 24"' in html
    assert "<title>" not in html and "role=" not in html
    assert 'data-brand-mode="brand"' in html
    assert 'fill="#6364FF"' in html, "the owner's published colour, and no currentColor"
    assert "currentColor" not in html
    assert "data-icon" not in html, "icons and marks are read by different selectors"
    assert not re.search(r"\s(width|height)=", html)


def test_a_brand_takes_classes_attributes_and_a_label():
    html = render('{% brand "bluesky" class="size-5" data_row="a" %}')
    assert 'class="size-5 shrink-0"' in html and 'data-row="a"' in html
    named = render('{% brand "bluesky" label="Bluesky" %}')
    assert 'role="img" aria-label="Bluesky"' in named and "aria-hidden" not in named


def test_attribute_values_are_escaped():
    assert 'aria-label="a&quot;b&lt;c"' in render('{% brand "youtube" label=\'a"b<c\' %}')


def test_an_unknown_brand_is_a_template_error_that_says_what_to_do():
    with pytest.raises(TemplateSyntaxError, match=re.escape("assets/brands.txt")):
        render('{% brand "no-such-mark" %}')


def test_a_single_colour_mark_is_drawn_in_currentcolor_and_says_so():
    html = render('{% brand "github" %}')
    assert 'data-brand="github"' in html and 'data-brand-mode="single-colour"' in html
    assert 'fill="currentColor"' in html and 'aria-hidden="true"' in html
    assert not re.search(r'fill="#', html), "one colour, and the page chooses it"


def test_a_mark_that_was_not_shipped_is_not_a_brand():
    """LinkedIn has no mark in Simple Icons and none is taken from elsewhere; X, Threads,
    Xing, Bitbucket and GitLab fail 3:1 on a page and no guidelines were found allowing
    them in one colour. Each is the Lucide icon, so each is an error here."""
    for name in ("linkedin", "x", "threads", "xing", "bitbucket", "gitlab"):
        with pytest.raises(TemplateSyntaxError):
            render(f'{{% brand "{name}" %}}')


@pytest.mark.parametrize("name", ["../css/app", "..\\icons\\user", "mastodon/../x", "", "A B"])
def test_a_path_is_not_a_brand_name(name):
    with pytest.raises(TemplateSyntaxError):
        Template("{% load postulo %}{% brand name %}").render(Context({"name": name}))


# ------------------------------------------------------------------- the list and the files


def test_committed_marks_are_exactly_the_listed_slugs():
    committed = {path.stem for path in BRAND_DIR.glob("*.svg")}
    assert committed == listed(), "run `npm run sync:brands` and commit the result"


def test_every_brand_used_in_a_template_is_listed():
    used = set()
    pattern = re.compile(r"{%\s*brand\s+[\"']([a-z0-9-]+)[\"']")
    for path in TEMPLATES.rglob("*.html"):
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert used <= listed(), f"marks used and not listed: {sorted(used - listed())}"


def test_every_mark_has_an_owner_line_a_mode_and_a_source_in_its_notice():
    blocks = notice_blocks()
    assert set(blocks) == listed()
    for slug, block in blocks.items():
        assert re.search(r"^  Owner: \S", block, re.M), f"{slug} has no owner"
        assert re.search(r"^  Mode: (brand|single-colour)\b", block, re.M), f"{slug} has no mode"
        assert re.search(r"^  Licence: \S", block, re.M), f"{slug} has no licence line"
        assert re.search(r"^  Guidelines: \S", block, re.M), f"{slug} has no guidelines"
    text = NOTICE.read_text(encoding="utf-8")
    assert "claims no endorsement" in " ".join(text.split()) or "no endorsement" in text
    assert "NOT covered by Postulo's AGPL" in text


def test_a_mark_is_in_one_of_two_modes_and_its_file_and_notice_agree():
    """Rule 2 of TRADEMARKS.md: unmodified. A brand mark's file carries its owner's colour as
    its fill and no currentColor; a single-colour one is currentColor and nothing else."""
    assert set(modes().values()) <= {"brand", "single-colour"}
    for slug, mode in modes().items():
        svg = (BRAND_DIR / f"{slug}.svg").read_text(encoding="utf-8")
        block = notice_blocks()[slug]
        if mode == "brand":
            assert re.match(r'<svg fill="#[0-9A-F]{6}"', svg), f"{slug} has no published colour"
            assert "currentColor" not in svg
            colour = re.match(r'<svg fill="(#[0-9A-F]{6})"', svg).group(1)
            assert f"Mode: brand (the published colour {colour}" in block
        else:
            assert svg.startswith('<svg fill="currentColor"'), slug
            assert "Mode: single-colour" in block
            assert re.search(r"^  Allowed by: https://\S+", block, re.M), f"{slug}: no page"
            assert re.search(r"^  They say: \S", block, re.M), f"{slug}: no words"


def test_the_marks_that_are_single_colour_are_the_ones_whose_guidelines_were_read():
    """The decision per candidate, recorded in the list: GitHub, SourceHut, Forgejo and
    ORCID are published by their owners in black or white (or as a monochrome file), and X,
    Threads, Xing, Bitbucket and GitLab are not here, for want of a page showing it."""
    assert single_colour() == {"github", "sourcehut", "forgejo", "orcid"}
    assert not listed() & {"x", "threads", "xing", "bitbucket", "gitlab", "linkedin"}


def test_every_brand_mark_clears_three_to_one_on_both_pages():
    """Shipped in its owner's colour or not at all; the same code tests/test_contrast.py
    holds the field boundary to, from the colour in the file."""
    light, dark = tokens()
    white = 1.0
    for slug in listed() - single_colour():
        svg = (BRAND_DIR / f"{slug}.svg").read_text(encoding="utf-8")
        hex_colour = re.match(r'<svg fill="#([0-9A-F]{6})"', svg).group(1)
        own = luminance_of(hex_colour)
        assert contrast(own, white) >= 3, f"{slug} {hex_colour} on the light page"
        assert contrast(own, dark["ink-950"]) >= 3, f"{slug} {hex_colour} on the dark page"
    assert light["ink-50"] and dark["ink-950"] < 0.01, "the dark page is the dark page"


def test_a_single_colour_mark_clears_three_to_one_on_each_page_in_its_own_colour():
    """Black on the light page and white on the dark one, the colours the stylesheet gives
    it, each held to 3:1 on the page it is drawn on (and each fails on the other one)."""
    _, dark = tokens()
    colours = page_colours()
    black, white = luminance_of(colours["light"]), luminance_of(colours["dark"])
    assert contrast(black, 1.0) >= 3, "the light page"
    assert contrast(white, dark["ink-950"]) >= 3, "the dark page"
    assert contrast(white, 1.0) < 3 and contrast(black, dark["ink-950"]) < 3
    assert {colours["light"].lower(), colours["dark"].lower()} == {"000000", "ffffff"}, (
        "the owners' guidelines allow black and white and no other colour"
    )


def test_the_contrast_check_fails_what_it_should():
    """GitHub's and X's published colours are near-black: invisible on the dark page."""
    _, dark = tokens()
    assert contrast(luminance_of("181717"), dark["ink-950"]) < 3
    assert contrast(luminance_of("A6CE39"), 1.0) < 3, "ORCID's green on white"


# -------------------------------------------------------------------------- the licences


def test_the_licence_list_is_one_file_and_names_what_653_allows():
    allowed = set(entries(LICENCES))
    assert {"MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Apache-2.0"} <= allowed
    assert {"MPL-2.0", "LGPL-3.0", "CC0-1.0", "AGPL-3.0", "GPL-3.0"} <= allowed
    assert {"CC-BY-4.0", "CC-BY-SA-4.0"} <= allowed
    assert not allowed & {"GPL-2.0-only", "BSD-4-Clause", "CC-BY-NC-4.0", "custom"}
    script = (REPO / "scripts" / "sync-brands.mjs").read_text(encoding="utf-8")
    assert "assets/brand-terms.txt" in script
    assert not any(f'"{name}"' in script for name in allowed), "the list is written in one place"


def test_every_licence_a_notice_names_is_on_the_list():
    allowed = set(entries(LICENCES))
    for slug, block in notice_blocks().items():
        licence = re.search(r"^  Licence: (\S+)", block, re.M).group(1)
        assert licence in allowed or licence == "none", f"{slug}: {licence}"


# --------------------------------------------------------------------- the sync script

NODE = shutil.which("node")


def fake_package(root: Path, marks: dict[str, dict]) -> None:
    package = root / "node_modules" / "simple-icons"
    (package / "icons").mkdir(parents=True)
    (package / "data").mkdir()
    (package / "package.json").write_text('{"version": "0.0.1"}', encoding="utf-8")
    data = []
    for slug, extra in marks.items():
        data.append({"title": slug.title(), "slug": slug, "hex": "6364FF", "source": "s", **extra})
        (package / "icons" / f"{slug}.svg").write_text(
            f'<svg role="img" viewBox="0 0 24 24"><title>{slug}</title><path d="M0 0"/></svg>',
            encoding="utf-8",
        )
    (package / "data" / "simple-icons.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "assets").mkdir()
    shutil.copy(LICENCES, root / "assets" / "brand-terms.txt")


def sync(root: Path, slugs: list[str]) -> subprocess.CompletedProcess:
    """`slugs` are lines of the list: a slug, or a slug with its mode and evidence."""
    (root / "assets" / "brands.txt").write_text("\n".join(slugs) + "\n", encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [NODE, str(REPO / "scripts" / "sync-brands.mjs")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(NODE is None, reason="Node is only needed to change the set")
def test_sync_copies_the_listed_marks_with_their_colour_and_notice(tmp_path):
    fake_package(tmp_path, {"alpha": {"license": {"type": "CC-BY-SA-4.0"}}, "beta": {}})
    result = sync(tmp_path, ["alpha"])
    assert result.returncode == 0, result.stderr
    out = tmp_path / "src" / "postulo" / "static" / "brands"
    assert (out / "alpha.svg").read_text().startswith('<svg fill="#6364FF" role="img"')
    assert not (out / "beta.svg").exists()
    notice = (out / "NOTICE.txt").read_text()
    assert "Licence: CC-BY-SA-4.0" in notice and "Mode: brand" in notice


GUIDELINES = 'single-colour https://example.org/brand "A one-colour version is allowed."'


@pytest.mark.skipif(NODE is None, reason="Node is only needed to change the set")
def test_sync_draws_a_single_colour_mark_in_currentcolor_and_records_the_page(tmp_path):
    fake_package(tmp_path, {"alpha": {}})
    result = sync(tmp_path, [f"alpha {GUIDELINES}"])
    assert result.returncode == 0, result.stderr
    out = tmp_path / "src" / "postulo" / "static" / "brands"
    assert (out / "alpha.svg").read_text().startswith('<svg fill="currentColor" role="img"')
    notice = (out / "NOTICE.txt").read_text()
    assert "Mode: single-colour" in notice
    assert "Allowed by: https://example.org/brand" in notice
    assert 'They say: "A one-colour version is allowed."' in notice


@pytest.mark.skipif(NODE is None, reason="Node is only needed to change the set")
@pytest.mark.parametrize(
    "line",
    [
        "alpha single-colour",
        "alpha single-colour https://example.org/brand",
        "alpha single-colour http://example.org/brand it is allowed",
        "alpha single-colour not-a-link it is allowed",
        "alpha monochrome https://example.org/brand it is allowed",
    ],
)
def test_sync_refuses_a_single_colour_mark_without_its_guidelines_recorded(tmp_path, line):
    fake_package(tmp_path, {"alpha": {}})
    result = sync(tmp_path, [line])
    assert result.returncode != 0 and "alpha" in result.stderr
    assert not (tmp_path / "src" / "postulo" / "static" / "brands" / "alpha.svg").exists()


@pytest.mark.skipif(NODE is None, reason="Node is only needed to change the set")
def test_sync_fails_on_a_slug_the_package_no_longer_has(tmp_path):
    fake_package(tmp_path, {"alpha": {}})
    result = sync(tmp_path, ["alpha", "linkedin"])
    assert result.returncode != 0
    assert "linkedin" in result.stderr and "removed upstream" in result.stderr


@pytest.mark.skipif(NODE is None, reason="Node is only needed to change the set")
def test_sync_refuses_a_licence_outside_the_list(tmp_path):
    fake_package(
        tmp_path,
        {"alpha": {"license": {"type": "custom", "url": "https://example.org"}}},
    )
    result = sync(tmp_path, ["alpha"])
    assert result.returncode != 0 and "custom" in result.stderr
    assert not (tmp_path / "src" / "postulo" / "static" / "brands" / "alpha.svg").exists()


# --------------------------------------------------------------------- the link services


def test_shipped_link_services_name_only_marks_that_exist_and_linkedin_names_none():
    from postulo.plugins.link_services.services import SERVICES

    named = {key: service.brand for key, service in SERVICES.items() if service.brand}
    assert named, "no service names a mark"
    for key, brand in named.items():
        assert brand in listed(), f"{key} names {brand}, which is not shipped"
        assert SERVICES[key].brand_name == brand
    assert SERVICES["linkedin"].brand == "", "LinkedIn has no mark (#654)"
    for key in ("github", "forgejo", "sourcehut"):
        assert SERVICES[key].brand_name == key, f"{key} is drawn in one colour"
    for key in ("x", "threads", "xing", "bitbucket", "gitlab"):
        assert SERVICES[key].brand_name == "", f"{key} has no mark and keeps its icon"
        assert SERVICES[key].icon_name, "and the Lucide icon is its fallback"


def test_a_mark_that_is_not_shipped_falls_back_to_the_icon_without_failing():
    from postulo.core.link_services import Service
    from postulo.plugins.link_services.services import SERVICES

    base = SERVICES["mastodon"]
    odd = Service(
        key="odd",
        label="Odd",
        kind=base.kind,
        pattern=base.pattern,
        hosts=("odd.example",),
        icon="user",
        brand="../static/css/app",
    )
    assert odd.brand_name == "" and odd.icon_name == "user"
    assert Service("odd", "Odd", base.kind, base.pattern, ("odd.example",)).brand == ""


@pytest.mark.django_db
def test_a_web_link_on_a_service_with_a_mark_says_which():
    from postulo.core.models import WebLink

    assert WebLink(service="mastodon", kind="social").brand == "mastodon"
    assert WebLink(service="github", kind="repository").brand == "github"
    assert WebLink(service="gitlab", kind="repository").brand == ""
    assert WebLink(service="", kind="social").brand == ""
