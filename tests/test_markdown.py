"""Basic Markdown for a listing's description: what it renders, and what it never will (#665)."""

import re

import pytest

from postulo.core import markdown as md

#: What the allowlist lets a rendered block hold. A test asserts it of every output.
TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)")


def tags_in(html: str) -> set[str]:
    return set(TAG.findall(html))


# ------------------------------------------------------------ what the subset renders


def test_a_paragraph_and_a_line_break():
    assert md.render("one\ntwo\n\nthree") == "<p>one<br>\ntwo</p>\n<p>three</p>\n"


def test_emphasis_strong_emphasis_and_inline_code():
    html = md.render("*a* **b** `c`")
    assert "<em>a</em>" in html and "<strong>b</strong>" in html and "<code>c</code>" in html


def test_unordered_and_ordered_lists():
    html = md.render("- one\n- two\n\n1. first\n2. second")
    assert "<ul>" in html and "<ol>" in html
    assert html.count("<li>") == 4


def test_a_block_quote():
    assert "<blockquote>" in md.render("> said")


def test_headings_are_shifted_down_and_stop_at_the_sixth_level():
    html = md.render("# a\n## b\n### c\n#### d\n##### e\n###### f")
    assert "<h3>a</h3>" in html and "<h4>b</h4>" in html
    assert "<h5>c</h5>" in html and "<h6>d</h6>" in html
    assert "<h6>e</h6>" in html and "<h6>f</h6>" in html
    assert "<h1" not in html and "<h2" not in html


def test_a_text_whose_shallowest_heading_is_the_second_level_still_starts_at_the_third():
    html = md.render("## a\n### b")
    assert "<h3>a</h3>" in html and "<h4>b</h4>" in html


@pytest.mark.parametrize(
    "source",
    [
        "[x](https://example.com/a)",
        '[x](https://example.com/a "a title")',
        "[x][ref]\n\n[ref]: https://example.com/a",
        "<https://example.com/a>",
    ],
)
def test_links_inline_by_reference_and_in_angle_brackets(source):
    html = md.render(source)
    assert 'href="https://example.com/a"' in html


def test_a_mail_link_is_allowed():
    assert 'href="mailto:me@example.com"' in md.render("[me](mailto:me@example.com)")


def test_a_link_title_is_not_kept():
    assert "title=" not in md.render('[x](https://example.com "t")')


def test_every_link_carries_the_forced_rel_and_target():
    html = md.render("[a](https://a.example) <https://b.example> [c](mailto:c@example.com)")
    anchors = re.findall(r"<a [^>]*>", html)
    assert len(anchors) == 3
    for anchor in anchors:
        assert 'rel="noopener noreferrer external"' in anchor
        assert 'target="_blank"' in anchor


# --------------------------------------------------------------- what is never rendered


@pytest.mark.parametrize(
    "source",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<b onclick=alert(1)>x</b>",
        '<a href="javascript:alert(1)">x</a>',
        "<iframe src=//evil></iframe>",
        "<!-- comment -->",
        "<style>*{}</style>",
    ],
)
def test_raw_html_comes_out_as_text(source):
    html = md.render(source)
    assert "<script" not in html and "<img" not in html and "<iframe" not in html
    assert "<b " not in html and "<style" not in html and "<!--" not in html
    assert tags_in(html) <= md.ALLOWED_TAGS


def test_an_image_is_never_an_image():
    html = md.render("![alt](https://tracker.example/pixel.png)")
    assert "<img" not in html and "src=" not in html


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "&#x6A;avascript:alert(1)",
        "&#106;avascript:alert(1)",
        "%6Aavascript:alert(1)",
        "java%0ascript:alert(1)",
        "javascript&colon;alert(1)",
        "//evil.example/x",
        "/relative/path",
    ],
)
def test_a_link_to_anything_but_http_https_or_mail_carries_no_address(link):
    html = md.render(f"[x]({link})")
    for href in re.findall(r'href="([^"]*)"', html):
        assert re.match(r"(https?:|mailto:)", href), href
    assert "javascript" not in re.sub(r"<[^>]*>", "", html).lower() or "<a" not in html


def test_the_other_constructs_are_left_as_text():
    source = (
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] task\n\n```\ncode\n```\n\nnote[^1]\n\n[^1]: x"
    )
    html = md.render(source)
    assert tags_in(html) <= md.ALLOWED_TAGS
    for tag in ("table", "input", "pre", "sup", "section"):
        assert f"<{tag}" not in html


def test_a_bare_address_is_not_made_a_link():
    assert "<a" not in md.render("see https://example.com and www.example.com")


def test_what_is_rendered_is_only_what_the_allowlist_names():
    html = md.render("# h\n\n- a **b** [c](https://x.example)\n\n> q `r`\n\n1. s\n<b>t</b>")
    assert tags_in(html) <= md.ALLOWED_TAGS


# ----------------------------------------------------------------------- the allowlist


def test_the_allowlist_alone_cleans_what_a_parser_with_html_on_would_let_through():
    dirty = (
        '<p onclick="x()">a</p><script>x()</script><img src="http://h/i.png">'
        '<a href="javascript:x()" title="t">b</a><a href="https://ok.example">c</a>'
        "<table><tr><td>d</td></tr></table>"
    )
    clean = md.sanitize(dirty)
    assert "onclick" not in clean and "<script" not in clean and "<img" not in clean
    assert "javascript" not in clean and "title=" not in clean and "<table" not in clean
    assert 'href="https://ok.example"' in clean
    assert 'rel="noopener noreferrer external"' in clean and 'target="_blank"' in clean


def test_the_second_pass_changes_nothing_in_what_the_parser_wrote():
    source = "# h\n\n- a **b** [c](https://x.example)\n\n> q `r`\n\n1. s\n2. t\n"
    parser = md.parser()
    tokens = parser.parse(source)
    md._shift_headings(tokens)
    written = parser.renderer.render(tokens, parser.options, {})
    assert md.sanitize(written) == str(md.render(source))


# ------------------------------------------------------------------- the configuration


def test_the_parser_is_configured_as_the_policy_says():
    parser = md.parser()
    assert parser.options["html"] is False
    assert parser.options["linkify"] is False
    assert parser.options["breaks"] is True

    active = parser.get_active_rules()
    inline, block = set(active["inline"]), set(active["block"])
    assert inline == set(md.INLINE_RULES) | {"text"}
    assert block == set(md.BLOCK_RULES)
    for name in ("image", "html_inline", "linkify", "strikethrough"):
        assert name not in inline
    for name in ("table", "html_block", "fence", "code", "hr", "lheading"):
        assert name not in block


def test_nothing_in_the_configuration_names_html_images_or_tables():
    assert not {"html_block", "html_inline", "image", "table"} & {
        *md.BLOCK_RULES,
        *md.INLINE_RULES,
    }


# ------------------------------------------------------------------------------ plain()


def test_plain_leaves_out_the_markers():
    text = (
        "# Role\n\nWe want **bold** people, *keen* ones and `code`.\n\n"
        "- [Apply](https://x.example)\n- two"
    )
    assert md.plain(text) == "Role\nWe want bold people, keen ones and code.\nApply\ntwo"


def test_plain_keeps_text_that_is_not_markup_and_never_html():
    assert md.plain("a <b>b</b>\nc") == "a <b>b</b> c"
    assert md.plain("") == ""
    assert md.plain(None) == ""


def test_plain_does_not_show_a_link_address_or_an_image_marker_pair():
    assert md.plain("[x](https://y.example)") == "x"
