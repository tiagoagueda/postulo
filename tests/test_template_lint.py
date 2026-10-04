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


STYLESHEETS = sorted((Path(__file__).resolve().parents[1] / "assets" / "css").glob("*.css"))


@pytest.mark.parametrize("source", STYLESHEETS, ids=lambda p: p.name)
def test_the_stylesheet_names_no_side_either(source: Path):
    """The source stylesheets, which `@apply` the same utilities the templates use -- the
    project's own and the style pack that paints Basecoat's components (#262)."""
    found = physical_sides(source.read_text(encoding="utf-8"))
    assert not found, f"assets/css/{source.name}: {found}"


def test_the_style_pack_is_among_the_stylesheets_checked():
    assert [p.name for p in STYLESHEETS] == ["app.css", "basecoat.css"]


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


# ------------------------------------------------------ a button by its old name

#: The five classes the templates used before the button became Basecoat's `btn` with a
#: `data-variant` and a `data-size` (#262). Gone from the stylesheet, so a template that
#: still says one draws an unstyled button; this says so before a page does.
RETIRED_BUTTON = re.compile(
    r"(?<![-\w])btn-(?:primary|secondary|ghost|danger|danger-ghost)(?![-\w])"
)


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_names_a_retired_button_class(path: Path):
    text = path.read_text(encoding="utf-8")
    found = [text.count("\n", 0, m.start()) + 1 for m in RETIRED_BUTTON.finditer(text)]
    assert not found, (
        f"{path.name}: a retired button class at line(s) {found}. Write "
        '`class="btn" data-variant="outline|ghost|destructive|destructive-ghost"` and a '
        "`data-size` of sm, xs, icon, icon-sm or icon-xs where the padding used to say it."
    )


def test_the_script_names_no_retired_button_class_either():
    script = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "js" / "app.js"
    assert not RETIRED_BUTTON.search(script.read_text(encoding="utf-8"))


# ------------------------------------------------------- a field row by its old name

#: The four classes a form row was drawn with before it became Basecoat's `.field` (#290):
#: a wrapper holding a bare label and a bare control, the help a paragraph and the errors
#: an alert, keyed on the elements rather than on a class each. Gone from the stylesheet,
#: so a template that still says one draws an unlabelled box; this says so before a page
#: does. A legend or a heading that wants a label's look says `label`, Basecoat's own.
RETIRED_FIELD = re.compile(r"(?<![-\w])field-(?:input|label|help|error)(?![-\w])")


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_names_a_retired_field_class(path: Path):
    text = path.read_text(encoding="utf-8")
    found = [text.count("\n", 0, m.start()) + 1 for m in RETIRED_FIELD.finditer(text)]
    assert not found, (
        f"{path.name}: a retired field class at line(s) {found}. Wrap the row in "
        '`<div class="field">` with a bare <label> and a bare control -- `<c-field>` does -- '
        "and write `label` on a legend that wants the look."
    )


def test_nothing_else_names_a_retired_field_class_either():
    root = Path(__file__).resolve().parents[1]
    for path in (
        root / "src" / "postulo" / "static" / "js" / "app.js",
        root / "assets" / "css" / "app.css",
        root / "assets" / "css" / "basecoat.css",
    ):
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        assert not RETIRED_FIELD.search(text), path.name


#: A figure written by hand: the size, the weight and the lining figures, on a paragraph
#: or a <dd> (#292). `<c-stat>` is the one way. Comments are not markup, and the page
#: title's own comment quotes the string it replaced.
HAND_FIGURE = re.compile(r"text-[23]xl font-semibold tabular-nums")
COMMENT = re.compile(r"\{% comment %\}.*?\{% endcomment %\}", re.DOTALL)


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_writes_a_figure_by_hand(path: Path):
    text = COMMENT.sub("", path.read_text(encoding="utf-8"))
    assert not HAND_FIGURE.search(text), (
        f"{path.name}: a figure written by hand. Write "
        '`<c-stat label="…">{{ the figure }}</c-stat>`, with a `tone` if it means something.'
    )


#: A section heading written by hand, with the margin that stood for whether a sentence
#: followed (#292). `<c-section-title>` is the one way; a heading that shares a row with
#: its actions keeps a bare `font-medium`, which this does not match. With its id first
#: or last, a size larger, or keeping a distance from what came before: all were written.
HAND_HEADING = re.compile(
    r'<h2(?: id="[^"]*")? class="(?:mt-\d+ )?mb-[1-4] (?:text-lg )?font-medium"'
)


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_writes_a_section_heading_by_hand(path: Path):
    if "allauth" in path.parts:
        pytest.skip("allauth's elements are its own vocabulary, drawn once each")
    text = path.read_text(encoding="utf-8")
    found = [text.count("\n", 0, m.start()) + 1 for m in HAND_HEADING.finditer(text)]
    assert not found, (
        f"{path.name}: a section heading written by hand at line(s) {found}. Write "
        "`<c-section-title>…</c-section-title>`, with the sentence under it in a "
        '`<c-slot name="subtitle">`.'
    )


def test_the_heading_detector_knows_the_difference():
    assert HAND_HEADING.search('<h2 class="mb-3 font-medium">Your name</h2>')
    assert HAND_HEADING.search('<h2 id="cadence-heading" class="mb-1 text-lg font-medium">')
    assert HAND_HEADING.search('<h2 class="mt-8 mb-3 font-medium">Notes</h2>')
    # A heading that shares a row with its actions, and one that says something of its own.
    assert not HAND_HEADING.search('<h2 class="font-medium">Documents</h2>')
    assert not HAND_HEADING.search('<h2 class="mb-2 font-medium text-red-700 dark:text-red-400">')
    assert not HAND_HEADING.search('<c-section-title id="section-name">')


#: The header of a data table, as it was copied into thirteen templates before the table
#: said `table` and the stylesheet drew it (#291).
COPIED_HEADER = re.compile(r'<thead class="border-b border-ink-200')


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_copies_the_table_header(path: Path):
    text = path.read_text(encoding="utf-8")
    assert not COPIED_HEADER.search(text), (
        f'{path.name}: a table header written by hand. Say `<table class="table">` and '
        "leave the <thead>, the rows and the cells bare; the stylesheet draws them."
    )


#: The two names a pill used to go by (#291): `tag` with its seven tones, `chip` with its
#: three parts. `badge` with a `data-tone` or `data-variant="chip"` is the one now. The
#: picker's own classes -- `tag-picker`, `tag-preview`, `tag-appearance` -- are not pills.
RETIRED_PILLS = {
    # The four alert classes, which were the box and the tone in one word (#291):
    # `class="alert" data-variant="warning"` is the spelling now.
    "alert-info",
    "alert-success",
    "alert-warning",
    "alert-error",
    "tag",
    *(f"tag-{tone}" for tone in ("grey", "blue", "amber", "violet", "teal", "green", "rose")),
    "chip",
    "chip-new",
    "chip-text",
    "chip-remove",
}

#: A class attribute in a template, or a `className` a script assigns. The words are looked
#: for among the classes and nowhere else: "a chip" is still a word a comment may use, and
#: `data-variant="chip"` is the new spelling, not the old one.
CLASSES = re.compile(r"""class(?:Name)?\s*=\s*["']([^"']*)["']""")


def retired_pills(text: str) -> list[tuple[int, str]]:
    """Line number and class for every retired pill class among a text's class lists."""
    found = []
    for match in CLASSES.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        found += [(line, name) for name in match.group(1).split() if name in RETIRED_PILLS]
    return found


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_names_a_retired_pill_class(path: Path):
    found = retired_pills(path.read_text(encoding="utf-8"))
    assert not found, (
        f"{path.name}: a retired pill class at {found}. Write "
        '`class="badge" data-tone="grey"`, or `data-variant="chip"` for a token in a field.'
    )


def test_the_script_names_no_retired_pill_class_either():
    script = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "js" / "app.js"
    assert not retired_pills(script.read_text(encoding="utf-8"))


#: What a pill is made of when it is written out instead of named: fully rounded, with the
#: badge's own padding. #291 looked for the retired *names* and so missed the three on
#: *Server settings -> Plugins* that never had one -- `rounded-full bg-amber-100 px-2
#: py-0.5 text-xs ...` (#311). A dot, an avatar or a switch is rounded and has no such
#: padding; a `badge` gets both from the stylesheet and says neither.
PILL_BY_HAND = {"rounded-full", "px-2", "py-0.5"}


def pills_by_hand(text: str) -> list[int]:
    """Line numbers of class lists that spell a pill out."""
    return [
        text.count("\n", 0, match.start()) + 1
        for match in CLASSES.finditer(text)
        if PILL_BY_HAND <= set(match.group(1).split())
    ]


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_writes_a_pill_by_hand(path: Path):
    found = pills_by_hand(path.read_text(encoding="utf-8"))
    assert not found, (
        f"{path.name}: a pill written out at line {found}. Write "
        '`class="badge" data-tone="amber"`, with the tone that says what it means.'
    )


def test_the_pill_detector_knows_the_difference():
    by_hand = '<span class="ms-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">'
    assert pills_by_hand(by_hand) == [1]
    assert pills_by_hand('<span class="ms-1 badge" data-tone="amber">') == []
    assert pills_by_hand('<span class="size-2 rounded-full bg-emerald-500">') == []
    assert pills_by_hand('<button class="rounded-md px-2 py-0.5 text-xs">') == []
    assert retired_pills('<span class="tag tag-grey">') == [(1, "tag"), (1, "tag-grey")]
    assert retired_pills('chip.className = "chip chip-new";') == [(1, "chip"), (1, "chip-new")]
    assert retired_pills('<span class="badge tag-preview" data-tone="grey">') == []
    assert retired_pills('<span class="badge" data-variant="chip">') == []
    assert retired_pills('<div class="alert-error mb-4" role="alert">') == [(1, "alert-error")]
    assert retired_pills('<div class="alert mb-4" data-variant="error">') == []
    assert retired_pills('<div class="page-alert">') == []
    assert retired_pills("{# the same chip the form draws #}") == []


def test_the_field_detector_knows_the_difference():
    assert RETIRED_FIELD.search('class="field-input w-64"')
    assert RETIRED_FIELD.search('<legend class="field-label">')
    assert not RETIRED_FIELD.search('class="field mb-4"')
    assert not RETIRED_FIELD.search('data-field-inputs="3"')
    assert not RETIRED_FIELD.search("field-separator")


# ------------------------------------------------------- a select is Basecoat's

#: An opening `<select>` tag, whatever it is spread over. A template tag inside it may hold
#: a `>` of its own, so the tag's end is the first `>` outside `{% %}` and `{{ }}`.
SELECT_TAG = re.compile(r"<select\b(?:\{%.*?%\}|\{\{.*?\}\}|[^>])*>", re.DOTALL)

#: The selects that stay the browser's own where scripts run, each with its reason (#301).
#:
#: **The rule is that there are none.** Every `<select>` is drawn by the server as a native
#: select, which is the control with scripts off, and `app.js` builds Basecoat's select
#: beside it: a button and a list whose options can hold a flag or an icon. A page may say
#: otherwise for one select by writing `data-native` on it, and then it is listed here, by
#: the template it is in and the `id` or `name` it carries, with why. A `data-native` that
#: is not listed fails, and so does an entry here that no template bears out any more.
#:
#: A `<select multiple>` or one with a `size` is a list box on the page, not this control,
#: and the script leaves it alone without being told. One written in a template is listed
#: here all the same, because it is a select somebody will see as the browser draws it.
#: (The one the application has is a widget's, not a template's: the tags of an
#: application, a `<select multiple>` that the label chips are layered over, #139.)
NATIVE_ON_PURPOSE: dict[tuple[str, str], str] = {}

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "postulo"


def native_selects(text: str) -> list[tuple[int, str]]:
    """Line number and `id` or `name` of every select a template keeps native."""
    found = []
    for match in SELECT_TAG.finditer(text):
        tag = match.group(0)
        if not re.search(r"\s(?:data-native|multiple|size)(?=[\s=>{])", tag):
            continue
        named = re.search(r"""\s(?:id|name)\s*=\s*["']([^"']*)["']""", tag)
        found.append((text.count("\n", 0, match.start()) + 1, named.group(1) if named else ""))
    return found


def template_name(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_a_select_is_basecoats_unless_the_list_says_why_not(path: Path):
    """Decided on #301: everywhere, not only where an option has a flag or an icon to show.

    So a template that keeps a select native has to say so in the list above, where the
    reason is written beside it and somebody reviewing the change reads it.
    """
    found = native_selects(path.read_text(encoding="utf-8"))
    unlisted = [
        (line, name) for line, name in found if (template_name(path), name) not in NATIVE_ON_PURPOSE
    ]
    assert not unlisted, (
        f"{path.name}: a select kept native at {unlisted}. Every select is Basecoat's "
        "(#301): take `data-native` off, or add the select to NATIVE_ON_PURPOSE in "
        "tests/test_template_lint.py with the reason."
    )


def test_every_select_listed_as_native_is_still_one():
    """An entry that is no longer true fails: the select went, or stopped saying
    `data-native`, and the list would otherwise go on excusing something that is not there."""
    by_name = {template_name(path): path for path in TEMPLATES}
    stale = []
    for (template, name), reason in NATIVE_ON_PURPOSE.items():
        assert reason.strip(), f"{template}: {name} is listed without a reason"
        path = by_name.get(template)
        kept = native_selects(path.read_text(encoding="utf-8")) if path else []
        if name not in [found for _line, found in kept]:
            stale.append((template, name))
    assert not stale, f"listed as native and not in the templates any more: {stale}"


def test_nothing_else_keeps_a_select_native():
    """The attribute is a template's to write, where the lint reads it. A widget that set it
    from Python, or a script that added it, would be a select kept native that no list
    names; and the script's own rule has to be the one this file describes."""
    for path in PYTHON_SOURCES:
        assert "data-native" not in path.read_text(encoding="utf-8"), (
            f"{path.name}: `data-native` set from Python. Write it on the select in its "
            "template, and list it in NATIVE_ON_PURPOSE."
        )
    script = (SOURCE_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert (
        'return !select.multiple && select.size <= 1 && !select.hasAttribute("data-native");'
        in script
    ), "app.js no longer builds its control beside every select but the ones this lint lists"


#: An opening tag that carries a class list, with the element it is and the classes.
CLASSED_TAG = re.compile(
    r"""<(?P<tag>[a-z][\w-]*)\b[^<>]*?\sclass\s*=\s*["'](?P<classes>[^"']*)["']"""
)


def test_no_template_writes_basecoats_select_out():
    """Basecoat's own markup for a select -- `<div class="select">` round a button and a
    list -- does nothing until a script has run, and every page here works with scripts off.
    The server draws a `<select>`; `app.js` builds the rest beside it."""
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        found = [
            text.count("\n", 0, match.start()) + 1
            for match in CLASSED_TAG.finditer(text)
            if match.group("tag") != "select" and "select" in match.group("classes").split()
        ]
        assert not found, (
            f"{path.name}: Basecoat's select written out at line(s) {found}. Write a "
            "native <select>; the script builds its button and its list (#301)."
        )


def test_the_select_detector_knows_the_difference():
    assert native_selects('<select name="status" class="w-32" data-autosubmit>') == []
    assert native_selects('<select id="zone" name="zone" data-native>') == [(1, "zone")]
    assert native_selects('<p>\n<select name="tags" multiple>') == [(2, "tags")]
    assert native_selects('<select name="rows" size="4">') == [(1, "rows")]
    # A `>` inside a template tag does not end the select's own tag.
    spread = '<select name="kind"{% if count > 1 %} data-native{% endif %}\n        id="kind">'
    assert native_selects(spread) == [(1, "kind")]
    # An attribute that only looks like one of the three.
    assert native_selects('<select name="x" data-native-like data-size="xs">') == []
    by_hand = CLASSED_TAG.search('<div id="kind" class="select w-40">')
    assert by_hand and by_hand.group("tag") == "div" and "select" in by_hand.group("classes")
    native = CLASSED_TAG.search('<select name="kind" class="select w-40">')
    assert native and native.group("tag") == "select"


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


# ------------------------------------------- a label, lowercased in Python (#392)

PYTHON_SOURCES = sorted((Path(__file__).resolve().parents[1] / "src" / "postulo").rglob("*.py"))

#: Where a label is lower-cased on purpose: the one name here and why.
LOWERCASED_ON_PURPOSE: dict[tuple[str, str], str] = {}


def lowercased_in_python(text: str) -> list[int]:
    """Line numbers where a choice's display or a ``label`` is lower-cased in Python.

    `get_kind_display().lower()` and `str(column.label).lower()` are the same mistake as
    `|lower` in a template: English typography applied to a word a translator wrote.
    """
    pattern = re.compile(r"(?:get_\w+_display\(\)|\blabel\b)\s*\)?\s*\.lower\(\)")
    return [text.count("\n", 0, match.start()) + 1 for match in pattern.finditer(text)]


@pytest.mark.parametrize("path", PYTHON_SOURCES, ids=lambda p: p.name)
def test_no_python_lowercases_a_translated_label(path: Path):
    """Let the catalogue decide the case: a whole sentence per value, or a pattern with the
    in-sentence form, never `.lower()` on the label (#168, #392)."""
    lines = lowercased_in_python(path.read_text(encoding="utf-8"))
    rel = path.relative_to(PYTHON_SOURCES[0].parents[2]).as_posix()
    lines = [line for line in lines if (rel, str(line)) not in LOWERCASED_ON_PURPOSE]
    assert not lines, f"{rel}: a translated label is lowercased at line(s) {lines}"


def test_the_python_detector_knows_the_difference():
    assert lowercased_in_python("x = sent.get_kind_display().lower()") == [1]
    assert lowercased_in_python("a\nb = str(self.label).lower()") == [2]
    assert lowercased_in_python("b = str(column.label).lower()") == [1]
    assert lowercased_in_python("host = request.POST.get('host').lower()") == []
    assert lowercased_in_python("label.strip().lower()") == []
