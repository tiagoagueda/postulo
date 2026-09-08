"""Things about templates that are wrong on every page at once, caught before a browser is.

Django's ``{# #}`` comment is single-line only; spread over two lines it is not a comment
but text, printed on the page with its braces. A note that mentions a tag in passing then
adds that tag to the document. It happened once, and the accessibility suite found it by
the Columns menu having vanished into a stray ``<details>``. This is the cheaper check.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = sorted((Path(__file__).resolve().parents[1] / "src" / "postulo").rglob("*.html"))


def multiline_hash_comments(text: str) -> list[int]:
    """Line numbers of ``{#`` whose ``#}`` is on a later line."""
    found = []
    for match in re.finditer(r"\{#", text):
        end = text.find("#}", match.end())
        chunk = text[match.end() : end if end != -1 else len(text)]
        if "\n" in chunk:
            found.append(text.count("\n", 0, match.start()) + 1)
    return found


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_comment_spans_lines(path: Path):
    lines = multiline_hash_comments(path.read_text(encoding="utf-8"))
    assert not lines, f"{path.name}: {{# #}} spans lines at {lines}; use {{% comment %}} instead"


def test_the_detector_knows_the_difference():
    assert multiline_hash_comments("{# fine #}\n<p>{# also fine #}</p>") == []
    assert multiline_hash_comments("<p>\n{# not\n   fine #}\n") == [2]


# ---------------------------------------------------- naming a side of the page

#: Physical direction utilities, and what to write instead. `dir="rtl"` flips text and
#: inline layout; it does not touch a class that names a side, so `ml-auto` keeps pushing
#: an action group to the left in Arabic, where the far end is the other one. The logical
#: utilities resolve against the document direction and mean the same thing under `ltr`.
LOGICAL_INSTEAD: dict[str, str] = {
    "ml": "ms",
    "mr": "me",
    "pl": "ps",
    "pr": "pe",
    "text-left": "text-start",
    "text-right": "text-end",
    "left": "start",
    "right": "end",
    "border-l": "border-s",
    "border-r": "border-e",
    "rounded-l": "rounded-s",
    "rounded-r": "rounded-e",
}

PHYSICAL = re.compile(
    r"(?<![-\w])-?(?:"
    r"(?P<spacing>[mp][lr])-(?:auto|px|\d+(?:\.\d+)?)"
    r"|(?P<align>text-(?:left|right))"
    r"|(?P<inset>(?:left|right))-(?:auto|full|px|\d+(?:\.\d+)?)"
    r"|(?P<edge>(?:border|rounded)-[lr])(?![-\w])"
    r")(?![-\w])"
)

#: Physical on purpose, with the reason. Keep it empty if you can.
ALLOWED: dict[str, str] = {}


def physical_sides(text: str) -> list[tuple[int, str]]:
    """Line number and utility for every class that names a left or a right."""
    found = []
    for match in PHYSICAL.finditer(text):
        found.append((text.count("\n", 0, match.start()) + 1, match.group(0)))
    return found


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_names_a_side_of_the_page(path: Path):
    """The lint that keeps right-to-left working after it has been made to work (#67).

    Without it this drifts back one heading row at a time, and nobody notices until an
    Arabic reader opens the page — which, in a project with no Arabic speakers on it, is
    long after it could have been cheap to fix.
    """
    if path.name in ALLOWED:
        pytest.skip(ALLOWED[path.name])
    found = physical_sides(path.read_text(encoding="utf-8"))
    assert not found, (
        f"{path.name}: names a side of the page at "
        + ", ".join(f"line {line} ({utility})" for line, utility in found)
        + ". Use the logical utility instead: "
        + ", ".join(f"{a} -> {b}" for a, b in sorted(LOGICAL_INSTEAD.items()))
    )


def test_the_stylesheet_names_no_side_either():
    """The source stylesheet, which `@apply`s the same utilities the templates use."""
    source = Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css"
    found = physical_sides(source.read_text(encoding="utf-8"))
    assert not found, f"assets/css/app.css: {found}"


def test_the_side_detector_knows_the_difference():
    assert physical_sides('class="ms-auto text-start ps-6"') == []
    # A colour called "right" is not a side, and neither is a word inside a sentence.
    assert physical_sides("<p>Turn left at the lights.</p>") == []
    assert physical_sides('class="border-red-300"') == []
    assert [u for _line, u in physical_sides('class="ml-auto"')] == ["ml-auto"]
    assert [u for _line, u in physical_sides('class="-left-1.5"')] == ["-left-1.5"]
    assert [u for _line, u in physical_sides('class="border-l pl-6 text-right"')] == [
        "border-l",
        "pl-6",
        "text-right",
    ]


# --------------------------------------------------- icons that point sideways

#: Names that would still be vertical, or meaningless, mirrored. Everything else whose
#: name contains a side has to be in the stylesheet's flip rule.
NOT_DIRECTIONAL = frozenset({"align-left", "align-right", "panel-left", "panel-right"})


def horizontal_icons() -> set[str]:
    """The icons Postulo bundles whose name says they point sideways."""
    listed = (Path(__file__).resolve().parents[1] / "assets" / "icons.txt").read_text(
        encoding="utf-8"
    )
    names = {
        line.strip() for line in listed.splitlines() if line.strip() and not line.startswith("#")
    }
    return {
        name
        for name in names
        if re.search(r"(?:^|-)(left|right)$", name) and name not in NOT_DIRECTIONAL
    }


def test_every_sideways_icon_is_flipped_for_right_to_left():
    """A "next" chevron aiming at the left margin in Arabic is worse than no chevron.

    The rule is keyed on the name the ``{% icon %}`` tag stamps, so a horizontal icon
    flips the day somebody uses it. This is what holds that list to the icon set: adding
    ``chevron-left`` to ``assets/icons.txt`` without adding it to the stylesheet fails
    here rather than on somebody's screen.
    """
    css = (Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    missing = sorted(name for name in horizontal_icons() if f'data-icon="{name}"' not in css)
    assert not missing, (
        f"these icons point sideways and are not flipped under dir=rtl: {missing}. "
        'Add [dir="rtl"] [data-icon="<name>"] to the flip rule in assets/css/app.css.'
    )


def test_the_icon_lister_reads_the_committed_set():
    found = horizontal_icons()
    assert "chevron-right" in found, "the bundled set has changed; check this still works"
    assert "arrow-up" not in found and "chevron-down" not in found
