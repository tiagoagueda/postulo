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


# ----------------------------------------------------- a box that scrolls on purpose

#: A scroll utility on its own. `.scroll-x` in the stylesheet is the same thing made safe.
BARE_SCROLL = re.compile(r"(?<![-\w:])overflow-(?:x-)?(?:auto|scroll)(?![-\w])")


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_a_table_scrolls_in_the_box_made_for_it(path: Path):
    """`.scroll-x` is `relative overflow-x-auto`, and `relative` is the half that is easy to
    leave out: without it an absolutely positioned `.sr-only` label is confined by the page
    rather than by the box, escapes it, and scrolls the page sideways (#113). The report and
    the recovery page were both written with the bare utility after that was learned, and in
    Greek the recovery page scrolled 12 pixels for a label nobody can see (#165).
    """
    text = path.read_text(encoding="utf-8")
    found = [text.count("\n", 0, m.start()) + 1 for m in BARE_SCROLL.finditer(text)]
    assert not found, (
        f"{path.name}: a bare scroll utility at line(s) {found}. Use `scroll-x`, which is "
        "positioned, so nothing absolutely placed inside it can escape it."
    )


def test_the_scroll_detector_knows_the_difference():
    assert BARE_SCROLL.search('class="overflow-x-auto"')
    assert BARE_SCROLL.search('class="rounded overflow-auto p-2"')
    assert not BARE_SCROLL.search('class="scroll-x"')
    assert not BARE_SCROLL.search('class="overflow-hidden truncate"')
    assert not BARE_SCROLL.search('class="md:overflow-x-auto"'), "a variant is its own case"


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


# ------------------------------------------------------- a label, lowercased


def lowercased_labels(text: str) -> list[int]:
    """Line numbers where a choice's translated label is lowercased by the template."""
    return [
        text.count("\n", 0, match.start()) + 1
        for match in re.finditer(r"get_\w+_display\s*\|\s*lower\b", text)
    ]


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_lowercases_a_translated_label(path: Path):
    """`|lower` is English typography. Applied to a label a translator wrote, it flattens an
    acronym in French and Portuguese and misspells every noun in German, and no catalogue
    can undo it. A CV's page said "What is on this cv" for a release because of it (#168).
    A label goes on the page as the catalogue wrote it; a sentence that needs another form
    of it is another string.
    """
    lines = lowercased_labels(path.read_text(encoding="utf-8"))
    assert not lines, f"{path.name}: a translated label is lowercased at line(s) {lines}"


def test_the_label_detector_knows_the_difference():
    assert lowercased_labels("{{ copy.get_status_display|lower }}") == [1]
    assert lowercased_labels("x\n{% blocktranslate with kind=cv.get_kind_display | lower %}") == [2]
    assert lowercased_labels("{{ copy.get_status_display }}\n{{ code|lower }}") == []


# ------------------------------------------------ a date written by hand

#: The format names Django defines for every locale it ships and falls back to from
#: settings for the rest, so one of these always resolves to a real format string. An
#: invented name does not: ``get_format`` hands an unrecognised name straight back, and the
#: page then prints the word SOME_FORMAT to whoever reads in the language that forgot it.
NAMED_FORMATS = frozenset(
    {
        "DATE_FORMAT",
        "DATETIME_FORMAT",
        "SHORT_DATE_FORMAT",
        "SHORT_DATETIME_FORMAT",
        "TIME_FORMAT",
        "YEAR_MONTH_FORMAT",
        "MONTH_DAY_FORMAT",
    }
)

#: Single format characters that mean the same thing in every language, so asking for one
#: settles nothing on the reader's behalf. A year is a number, a day of the month is a
#: number, and a weekday's name is one word that Django translates itself -- none of them
#: has an order to get wrong or a clock to assume. Anything with two of them in it does.
ATOMS = frozenset({"Y", "j", "D", "l"})

#: Literal formats kept on purpose, with the reason. These are not read by anybody: they
#: are the wire format an ``<input type="date">`` parses and the DOM ids built to match.
DATES_ON_PURPOSE: dict[str, str] = {
    "Y-m-d": 'ISO 8601 for an <input type="date"> value and the ids that pair with it',
}

#: ``{{ value|date:"..." }}`` and ``{{ value|time:"..." }}``, either kind of quote.
TEMPLATE_DATE = re.compile(r"\|\s*(?:date|time):(?P<quote>[\"'])(?P<format>.*?)(?P=quote)")

#: ``formats.date_format(moment, "...")`` and its time and number siblings.
PYTHON_DATE = re.compile(
    r"\b(?:date_format|time_format)\([^()]*?,\s*(?P<quote>[\"'])(?P<format>.*?)(?P=quote)"
)


def spelled_out_dates(text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """Line number and format for every date format written out rather than named."""
    found = []
    for match in pattern.finditer(text):
        spelling = match.group("format")
        if spelling in NAMED_FORMATS or spelling in ATOMS or spelling in DATES_ON_PURPOSE:
            continue
        found.append((text.count("\n", 0, match.start()) + 1, spelling))
    return found


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_writes_a_date_format_out(path: Path):
    """``|date:"j M Y"`` is a British sentence about a date, in every language at once.

    It fixes day before month before year and the hour at 14 rather than 2 p.m., which is
    wrong for Hungarian and Lithuanian today and for most of Asia at 0.4.0 (#225). There
    were seventy-three of them. The named formats resolve against the reader's language, so
    the template says *which* date it means and the locale says how to write it.
    """
    found = spelled_out_dates(path.read_text(encoding="utf-8"), TEMPLATE_DATE)
    assert not found, (
        f"{path.name}: a date format spelled out at "
        + ", ".join(f"line {line} ({spelling!r})" for line, spelling in found)
        + ". Ask for one of "
        + ", ".join(sorted(NAMED_FORMATS))
        + " instead, and compose it with a second filter where you need a weekday as well."
    )


PYTHON_SOURCES = sorted(
    (Path(__file__).resolve().parents[1] / "src" / "postulo").rglob("*.py"),
)


@pytest.mark.parametrize(
    "path", PYTHON_SOURCES, ids=lambda p: str(p.relative_to(PYTHON_SOURCES[0].parents[3]))
)
def test_no_view_writes_one_out_either(path: Path):
    """The same rule where the string is built in Python: a calendar heading, a letter's date.

    ``django.utils.formats.date_format`` takes the same names, so this is the same fix in
    the same words, and leaving Python out of the lint is how the rule comes back.
    """
    found = spelled_out_dates(path.read_text(encoding="utf-8"), PYTHON_DATE)
    assert not found, (
        f"{path.name}: a date format spelled out at "
        + ", ".join(f"line {line} ({spelling!r})" for line, spelling in found)
        + f". Ask for one of {', '.join(sorted(NAMED_FORMATS))} instead."
    )


def test_the_date_detector_knows_the_difference():
    assert spelled_out_dates('{{ x|date:"DATE_FORMAT" }}', TEMPLATE_DATE) == []
    assert spelled_out_dates("{{ x|date:'Y-m-d' }}", TEMPLATE_DATE) == [], "a machine-read date"
    assert spelled_out_dates('{{ x|date:"Y" }}{{ x|date:"D" }}', TEMPLATE_DATE) == []
    assert spelled_out_dates('{{ x|date:"j M Y" }}', TEMPLATE_DATE) == [(1, "j M Y")]
    assert spelled_out_dates('{{ x|date:"H:i" }}', TEMPLATE_DATE) == [(1, "H:i")]
    assert spelled_out_dates('{{ x | date:"j F" }}', TEMPLATE_DATE) == [(1, "j F")], "spaced"
    assert spelled_out_dates('date_format(day, "DATE_FORMAT")', PYTHON_DATE) == []
    assert spelled_out_dates('date_format(day, "l j F Y")', PYTHON_DATE) == [(1, "l j F Y")]
    assert spelled_out_dates('formats.time_format(x, "H:i")', PYTHON_DATE) == [(1, "H:i")]
    # An invented name would sail past a check that only looked for format characters.
    assert spelled_out_dates('{{ x|date:"SHORT_MONTH_DATE_FORMAT" }}', TEMPLATE_DATE) == [
        (1, "SHORT_MONTH_DATE_FORMAT")
    ]
