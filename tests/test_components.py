"""Shared markup is a component, not an include (#263): what the first ones promise.

``partials/field.html`` was included 117 times with a ``with field=…`` chain, which is the
right instinct -- write it once -- carried out with the only mechanism Django gives a
template. An include declares no parameters and has no defaults, and it sees whatever the
caller's context happens to hold. django-cotton turns the partial into a tag with a
declared contract: ``<c-field :field="form.company_name" />``.

The tests here pin what the components render, and answer the four questions the spike had
to answer before anything shipped: where the compiled form lives, what a broken component
reports, whether the form renderer still composes, and why context isolation is off.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django import forms
from django.template import Context, RequestContext, TemplateSyntaxError, engines
from django.test import RequestFactory

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "postulo" / "templates"


class NameForm(forms.Form):
    name = forms.CharField(help_text="As it appears on your passport.")
    note = forms.CharField(required=False)
    agreed = forms.BooleanField(required=False, help_text="Tick to agree.")


def render(source: str, **context) -> str:
    """A template from a string, through the project's engine.

    A string never passes through the loader that compiles ``<c-…>`` tags, so the native
    ``{% cotton %}`` tag is used here; every page in the suite exercises the compiled form.
    """
    return engines["django"].engine.from_string(source).render(Context(context))


def with_templates_in(settings, directory: Path) -> None:
    """The engine, rebuilt with ``directory`` as one more place templates come from, and
    debugging on so a syntax error carries the name and line it was found at."""
    options = {**settings.TEMPLATES[0]["OPTIONS"], "debug": True}
    settings.TEMPLATES = [
        {
            **settings.TEMPLATES[0],
            "DIRS": [*settings.TEMPLATES[0]["DIRS"], directory],
            "OPTIONS": options,
        }
    ]


def test_a_field_is_its_label_widget_help_and_errors():
    """Basecoat's shape (#290): a `.field` holding a bare label and a bare control, the help
    a paragraph and the errors an alert, and `data-invalid` on the row when it has any."""
    form = NameForm(data={})  # bound and empty: the required field has an error
    html = render('{% cotton field :field="form.name" / %}', form=form)

    assert '<div class="field mb-4 wrap-anywhere" data-invalid="true">' in html
    assert '<label for="id_name">' in html
    assert '<span aria-hidden="true" class="text-red-600 dark:text-red-400">*</span>' in html, (
        "required"
    )
    assert (
        'name="name"' in html
        and "field-input" not in html
        and 'class="' not in html.split("<input", 1)[1].split(">", 1)[0]
    ), "the control carries no class; the stylesheet keys on the wrapper"
    assert '<p id="id_name_helptext">As it appears on your passport.</p>' in html
    assert '<div id="id_name_error" role="alert">' in html
    assert "<p>This field is required.</p>" in html


def test_an_optional_field_has_no_asterisk_and_no_error_block():
    html = render('{% cotton field :field="form.note" / %}', form=NameForm())
    assert "*" not in html and 'role="alert"' not in html and "helptext" not in html
    assert "data-invalid" not in html


def test_a_checkbox_lies_the_other_way():
    """Box first, then the label beside it with the help under the label: what the settings
    pages used to build by hand, and Basecoat's `horizontal` orientation (#290)."""
    html = render('{% cotton field :field="form.agreed" / %}', form=NameForm())

    assert '<div class="field mb-4 wrap-anywhere" data-orientation="horizontal">' in html
    box = html.index('type="checkbox"')
    label = html.index('<label for="id_agreed">')
    assert box < label, "the box comes first"
    assert "<section>" in html and '<p id="id_agreed_helptext">Tick to agree.</p>' in html
    assert "*" not in html, "a checkbox is never marked required with an asterisk"


class GroupForm(forms.Form):
    files = forms.MultipleChoiceField(
        choices=[("a", "A"), ("b", "B")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Files you have.",
    )
    kind = forms.ChoiceField(
        choices=[("x", "X"), ("y", "Y")], widget=forms.RadioSelect, help_text="Pick one."
    )
    nothing = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False, help_text="Nothing yet."
    )
    nobody = forms.ChoiceField(
        choices=[], widget=forms.RadioSelect, required=False, help_text="No one."
    )


@pytest.mark.parametrize("name", ["files", "kind", "nothing", "nobody"])
def test_a_group_of_choices_is_a_fieldset_named_by_its_legend(name):
    """Django gives a group no `id_for_label`; a `<label for="">` named nothing (#624)."""
    form = GroupForm()
    html = render(f'{{% cotton field :field="form.{name}" / %}}', form=form)

    assert (
        'for=""' not in html
        and "<label" not in html.split("<fieldset", 1)[1].split("<legend", 1)[0]
    )
    assert html.count("<fieldset") == 1 and "<legend>" in html
    assert form[name].label in html.split("<legend>", 1)[1].split("</legend>", 1)[0]
    assert (
        f'aria-describedby="id_{name}_helptext"' in html.split("<fieldset", 1)[1].split(">", 1)[0]
    )
    assert f'<p id="id_{name}_helptext">' in html


def test_feedback_can_leave_the_help_to_the_caller():
    """``errors-only`` is for a caller that drew the help above the controls itself (#139)."""
    form = NameForm(data={})
    with_help = render('{% cotton field-feedback :field="form.name" / %}', form=form)
    without = render('{% cotton field-feedback :field="form.name" errors-only / %}', form=form)

    assert 'id="id_name_helptext"' in with_help
    assert "helptext" not in without and 'id="id_name_error"' in without


def test_a_figure_is_the_number_and_what_it_counts():
    """One way to draw a statistic (#292): the figure, its label, and a tone where the
    figure means something. A tile of its own when it says `card`, a link when it has
    somewhere to go."""
    plain = render('{% cotton stat label="Sent" %}24{% endcotton %}')
    assert '<div class="stat">' in plain and "data-tone" not in plain
    assert '<p class="stat-value">24</p>' in plain and '<p class="stat-label">Sent</p>' in plain

    toned = render('{% cotton stat label="Offers" tone="green" card %}2{% endcotton %}')
    assert '<div class="stat card" data-tone="green">' in toned

    linked = render('{% cotton stat label="Still live" href="/applications/" %}3{% endcotton %}')
    assert '<a href="/applications/" class="stat card hover:border-brand-400">' in linked


def test_a_section_title_is_a_header_with_the_heading_and_the_sentence():
    """One way to write the heading at the top of a section (#292), and Basecoat's card
    header when it is a card's first child (#291)."""
    html = render(
        '{% cotton section-title id="section-name" %}Your name'
        "{% cotton:slot subtitle %}As it appears on a CV.{% endcotton:slot %}{% endcotton %}"
    )

    assert '<header class="section-title">' in html
    assert '<h2 id="section-name">Your name</h2>' in html
    assert "<p>As it appears on a CV.</p>" in html

    bare = render("{% cotton section-title %}Your name{% endcotton %}")
    assert "<h2>Your name</h2>" in bare and "<p>" not in bare

    # The one thing a call site may add: the distance from what came before.
    spaced = render('{% cotton section-title class="mt-8" %}Notes{% endcotton %}')
    assert '<header class="section-title mt-8">' in spaced


def test_no_template_includes_a_retired_partial():
    """The include and the component must not coexist, or the two drift apart."""
    retired = re.compile(r"""include\s+["']partials/(field|field_feedback|table/head)\.html""")
    found = [
        (str(path.relative_to(TEMPLATES)), match.group(0))
        for path in sorted(TEMPLATES.rglob("*.html"))
        for match in retired.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not found, f"still included as a partial: {found}"


def test_every_component_declares_what_it_takes():
    """The ``<c-vars>`` line is the contract, and it is the first thing in the file."""
    for path in sorted((TEMPLATES / "cotton").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        head = re.sub(r"\{%\s*load\b[^%]*%\}", "", text, count=1).lstrip()
        assert head.startswith("<c-vars "), f"{path.name} does not open with <c-vars>"


# ------------------------------------------------------------- the spike's questions


def test_the_compiled_form_lives_in_memory_and_the_engine_still_caches():
    """Cotton compiles ``<c-…>`` tags to template tags when a file is loaded and keeps the
    result in a dictionary keyed on the file's path and mtime; Django's cached loader wraps
    it. Nothing is written anywhere, so the container needs nothing writable for it.
    """
    from django_cotton.cotton_loader import CottonTemplateCacheHandler

    engine = engines["django"].engine
    (cached,) = engine.template_loaders
    assert cached.__class__.__module__ == "django.template.loaders.cached"
    first, *_rest = cached.loaders
    assert first.__class__.__module__ == "django_cotton.cotton_loader"
    assert isinstance(first.cache_handler.template_cache, dict)
    assert not [
        name for name in dir(CottonTemplateCacheHandler) if "file" in name or "path" in name
    ]


def test_a_broken_component_is_reported_against_its_own_file(tmp_path, settings):
    """A component is loaded as the template it is, so a syntax error in one names the file
    that was written, at the line it is on -- not some generated output.
    """
    (tmp_path / "cotton").mkdir()
    (tmp_path / "cotton" / "broken.html").write_text("<p>one</p>\n<p>two</p>\n{% if %}\n")
    with_templates_in(settings, tmp_path)

    with pytest.raises(TemplateSyntaxError) as caught:
        render("{% cotton broken / %}")

    debug = caught.value.template_debug
    assert debug["name"].endswith("broken.html")
    assert debug["line"] == 3


@pytest.mark.xfail(
    strict=True,
    reason="django-cotton 2.7.2 tokenizes the text around a component tag in pieces and "
    "corrects each token's line number but not its character position, which is what "
    "Django's debug page reads; the fault is shown on the tag's line (2), not its own (4)",
)
def test_a_broken_page_is_reported_at_the_right_line_after_a_component_tag(tmp_path, settings):
    """The other half of the error question: a page whose ``<c-…>`` tag was compiled away.

    Cotton replaces the tag in place, so the line count does not change; and its tokenizer
    hands Django the text around the tag in pieces, so the line a fault is reported on has
    to be checked rather than assumed. It is wrong today -- the one cost the spike found --
    and the file name is right, so the fault is still findable. Strict, so the day the
    upstream fix lands this fails and the marker comes off.
    """
    (tmp_path / "cotton").mkdir()
    (tmp_path / "cotton" / "fine.html").write_text("<c-vars text />\n<p>{{ text }}</p>\n")
    (tmp_path / "page.html").write_text(
        '<p>one</p>\n<c-fine text="two" />\n<p>three</p>\n{% if %}\n<p>five</p>\n'
    )
    with_templates_in(settings, tmp_path)
    from django.template.loader import get_template

    with pytest.raises(TemplateSyntaxError) as caught:
        get_template("page.html")

    debug = caught.value.template_debug
    assert debug["name"].endswith("page.html")
    assert debug["line"] == 4


def test_the_form_renderer_composes_with_the_component_loader(db):
    """``FORM_RENDERER = TemplatesSetting`` renders widgets through the same engine, and the
    phone widget is a template in it; a loader that fought the renderer would break every
    form. The cotton loader passes a template without ``<c-`` tags through untouched.
    """
    from django.forms.renderers import get_default_renderer

    renderer = get_default_renderer()
    html = renderer.render("django/forms/widgets/text.html", {"widget": {"name": "q", "attrs": {}}})
    assert "<input" in html


def test_isolation_would_run_every_context_processor_once_per_component(db, settings, monkeypatch):
    """Why ``COTTON_ENABLE_CONTEXT_ISOLATION`` is off.

    With it on, cotton renders each component in a fresh ``RequestContext``, which runs
    every context processor again: the interface processor asks for the instance name, the
    navigation and the installed version, and a form page draws twenty fields. Off, a
    component sees the caller's context and reads only what its tag was given; the contract
    is kept by convention and by the ``<c-vars>`` line, not by the engine.
    """
    from postulo.core import context_processors

    calls = []
    real = context_processors.ui

    def counting(request):
        calls.append(request)
        return real(request)

    monkeypatch.setattr(context_processors, "ui", counting)
    request = RequestFactory().get("/")
    request.user = None
    source = '{% cotton field :field="form.name" / %}' * 3

    def rendered_with(isolation: bool) -> int:
        settings.COTTON_ENABLE_CONTEXT_ISOLATION = isolation
        # The engine imports its processors on the first request-bound render and keeps
        # them, so a page rendered by an earlier test has already pinned the real function.
        # Assigning TEMPLATES makes Django throw the engine away and build one that finds
        # the patched one.
        settings.TEMPLATES = [dict(settings.TEMPLATES[0])]
        calls.clear()
        engine = engines["django"].engine
        engine.from_string(source).render(RequestContext(request, {"form": NameForm()}))
        return len(calls)

    assert rendered_with(False) == 1, "the page's own pass, and nothing per component"
    # Three fields, each drawing its feedback as a component of its own: six more passes.
    assert rendered_with(True) == 7, "the page's pass plus one per component, nested ones too"


ONE_MENU = (
    '{% cotton dropdown-menu label="Actions for Jo" %}'
    "{% cotton:slot trigger %}...{% endcotton:slot %}"
    '<a href="/x" role="menuitem">Edit</a>'
    "{% endcotton %}"
)


def test_a_dropdown_menu_is_a_popover_that_says_it_is_a_menu():
    """Basecoat's dropdown menu on the browser's popover (#262, #310): the trigger is a
    `<button popovertarget>` naming the panel, so the browser opens it with no script and
    draws it in the top layer, where the box a table scrolls in cannot cut it off. The
    trigger and the menu share the name, and the items are the caller's."""
    html = render(ONE_MENU)

    panel = re.search(r'<div popover id="([^"]+)" data-popover data-align="end">', html)
    assert panel, html
    assert '<div class="dropdown-menu" data-menu>' in html
    # `type="button"`, because a submit button inside a form does not open a popover at all.
    assert (
        f'<button type="button" popovertarget="{panel.group(1)}" class="btn cursor-pointer" '
        'data-variant="ghost" data-size="icon-xs" aria-haspopup="menu" '
        'aria-label="Actions for Jo">...</button>'
    ) in html
    assert '<div role="menu" aria-label="Actions for Jo">' in html
    assert '<a href="/x" role="menuitem">Edit</a>' in html
    assert "<details" not in html and "<summary" not in html


def test_every_menu_on_a_page_opens_its_own_panel():
    """The trigger finds its panel by id, so two menus sharing one would open each other's
    panel -- the header's and a row's, say. Random rather than counted: a fragment htmx
    swaps in is another request, and a counter would start again at one (#310)."""
    html = render(ONE_MENU * 5)
    panels = re.findall(r'<div popover id="([^"]+)"', html)
    targets = re.findall(r'popovertarget="([^"]+)"', html)

    assert len(panels) == 5 and len(set(panels)) == 5, panels
    assert targets == panels, "each trigger names the panel beside it"

    again = re.findall(r'<div popover id="([^"]+)"', render(ONE_MENU * 5))
    assert not set(again) & set(panels), "and the next request takes none of them again"


def test_a_navigation_dropdown_is_a_disclosure_of_links_rather_than_a_menu():
    """The account menu and *More* are navigation (#262): the same popover, with no
    `role="menu"` and no `aria-haspopup="menu"`, so a screen reader is not promised arrow
    keys the rows do not answer to. Any other attribute goes on the root (#310)."""
    html = render(
        '{% cotton dropdown-menu kind="navigation" label="More" data-nav-more '
        'trigger_class="nav-link" trigger_variant="" trigger_size="" %}'
        "{% cotton:slot trigger %}More{% endcotton:slot %}"
        '<a href="/y" class="menu-item">Companies</a>'
        "{% endcotton %}"
    )

    panel = re.search(r'<div popover id="([^"]+)" data-popover data-align="end">', html)
    assert panel, html
    assert '<div class="dropdown-menu" data-menu data-nav-more>' in html
    assert (
        f'<button type="button" popovertarget="{panel.group(1)}" class="nav-link" '
        'aria-label="More">More</button>'
    ) in html
    assert 'role="menu"' not in html and "aria-haspopup" not in html
    assert '<a href="/y" class="menu-item">Companies</a>' in html


ONE_DIALOG = (
    '{% cotton dialog id="ask" action="/remove/1/" data-remove-row %}'
    "{% cotton:slot title %}Remove <bdi>Jo</bdi>?{% endcotton:slot %}"
    "{% cotton:slot description %}It goes at once.{% endcotton:slot %}"
    '{% cotton:slot footer %}<button type="submit">Remove</button>{% endcotton:slot %}'
    "{% endcotton %}"
)


def test_a_dialog_is_a_popover_named_by_its_heading_and_described_by_its_sentence():
    """Basecoat's alert dialog on the browser's own `<dialog>` and popover (#303): a
    `popovertarget` opens it with no script, and `app.js` opens the same one as a modal.
    The heading and the sentence are slots, so a typed value can sit in a `<bdi>`, and they
    are the dialog's name and description. Any other attribute goes on the dialog."""
    html = render(ONE_DIALOG)

    assert (
        '<dialog popover id="ask" class="alert-dialog" role="alertdialog" '
        'aria-labelledby="ask-title" aria-describedby="ask-description" data-dialog '
        "data-remove-row>"
    ) in html
    assert '<h2 id="ask-title">Remove <bdi>Jo</bdi>?</h2>' in html
    assert '<p id="ask-description">It goes at once.</p>' in html
    assert '<form method="post" action="/remove/1/">' in html
    assert "hx-post" not in html and "data-dialog-said" not in html


def test_cancel_comes_first_changes_nothing_and_has_the_focus():
    """*Cancel* is drawn by the component, before the caller's answer, so Tab reaches the
    answer that changes nothing first; it closes a popover by itself, and it is where focus
    lands when the dialog opens."""
    html = render(ONE_DIALOG)

    cancel = re.search(r"<button[^>]*data-dialog-close[^>]*>Cancel</button>", html)
    assert cancel, html
    assert 'type="button"' in cancel.group(0), "a submit here would answer the question"
    assert 'popovertarget="ask" popovertargetaction="hide"' in cancel.group(0)
    assert "autofocus" in cancel.group(0)
    assert html.index("Cancel</button>") < html.index("Remove</button>")


def test_a_live_dialog_is_sent_by_htmx_and_has_somewhere_to_say_no():
    """Where the page's script writes a refusal or a failed request: behind a modal the
    page is inert, so the alert at its foot is neither seen nor heard. It is the
    vocabulary's alert, in the error tone, still announced as one, and drawn with nothing
    in it -- `:empty` is what hides it, so not even a space."""
    html = render(ONE_DIALOG.replace('action="/remove/1/"', 'action="/remove/1/" live'))

    assert (
        '<form method="post" action="/remove/1/" hx-post="/remove/1/" hx-swap="none" '
        'hx-sync="this:drop">'
    ) in html
    assert (
        '<p role="alert" class="alert empty:hidden" data-variant="error" data-dialog-said></p>'
    ) in html


def test_a_dialog_without_an_action_holds_no_form():
    """The gallery's, and any dialog whose buttons do not post."""
    html = render(
        '{% cotton dialog id="look" tone="plain" %}'
        "{% cotton:slot title %}Look{% endcotton:slot %}"
        "{% endcotton %}"
    )

    assert (
        '<dialog popover id="look" class="dialog" aria-labelledby="look-title" data-dialog>' in html
    )
    assert "<form" not in html and 'role="alertdialog"' not in html
    assert "aria-describedby" not in html, "no sentence, so nothing to point at"


# ------------------------------------------------------ help where it is asked (#302)


def test_a_fields_help_can_be_a_tooltip_and_is_the_same_paragraph():
    """`tip` marks the help for the script and changes nothing else: the same paragraph,
    the same id, still what the control is described by. With scripts off it is the help
    under the box. A tick box takes no tip, and a form that was refused keeps its help in
    sight."""
    plain = render('{% cotton field :field="form.name" / %}', form=NameForm())
    tipped = render('{% cotton field :field="form.name" tip / %}', form=NameForm())

    assert '<p id="id_name_helptext" data-tooltip>As it appears on your passport.</p>' in tipped
    assert tipped.replace(" data-tooltip", "") == plain
    assert 'aria-describedby="id_name_helptext"' in tipped

    box = render('{% cotton field :field="form.agreed" tip / %}', form=NameForm())
    assert '<p id="id_agreed_helptext">Tick to agree.</p>' in box

    refused = render('{% cotton field :field="form.name" tip / %}', form=NameForm(data={}))
    assert '<p id="id_name_helptext">As it appears on your passport.</p>' in refused
    assert 'id="id_name_error"' in refused


def test_a_section_title_can_carry_its_cards_question_mark():
    """`help` names a topic: the heading, then a link to the topic's page that is described
    by the topic's one sentence, the sentence itself, and the drawer the link opens where a
    script runs. `anchor` goes along as the way back, and `tip` is the sentence's id where
    something else on the page names it."""
    html = render(
        '{% cotton section-title help="your-name" anchor="section-name" tip="the-sentence" %}'
        "Your name{% endcotton %}",
        request=RequestFactory().get("/accounts/profile/"),
    )

    heading = html.index("<h2>Your name</h2>")
    mark = re.search(r"<a\b[^>]*data-help-mark[^>]*>", html)
    assert mark and heading < mark.start()
    assert 'href="/help/your-name/?next=%2Faccounts%2Fprofile%2F%23section-name"' in mark.group(0)
    assert 'class="btn" data-variant="ghost" data-size="icon-xs"' in mark.group(0)
    assert 'data-opens-dialog="help-your-name"' in mark.group(0)
    assert 'aria-describedby="the-sentence"' in mark.group(0)
    assert 'data-icon="circle-question-mark"' in html
    assert re.search(
        r'<p id="the-sentence" data-tooltip data-help-summary>A form of address is written', html
    )
    assert html.count('<dialog id="help-your-name"') == 1

    without = render("{% cotton section-title %}Your name{% endcotton %}")
    assert "data-help-mark" not in without and "<dialog" not in without


def test_a_question_mark_opens_its_page_in_a_new_tab_and_says_so():
    """With scripts off the question mark is a link away from a form, so it opens the help
    in a new tab, and its name says so: what was typed on the card is never left behind
    (#302). The name is the link's own text, so that `app.js`, taking the link over for
    the drawer, can take the words about a new tab off with the `target`."""
    html = render(
        '{% cotton help-mark topic="telephone-numbers" anchor="section-phones" / %}',
        request=RequestFactory().get("/accounts/profile/"),
    )

    mark = re.search(r"<a\b[^>]*data-help-mark[^>]*>(.*?)</a>", html, re.S)
    assert 'target="_blank"' in mark.group(0) and 'rel="noopener"' in mark.group(0)
    assert "aria-label" not in mark.group(0), "the name is the link's text"
    named = re.sub(r"<svg.*?</svg>", "", mark.group(1), flags=re.S)
    assert named == (
        '<span class="sr-only">Help: Telephone numbers</span>'
        '<span class="sr-only" data-new-tab> (opens in a new tab)</span>'
    )


def test_a_question_marks_sentence_stays_in_sight_when_the_page_says_so():
    """`in_sight` is the page saying something on it was refused (#302): the card's
    sentence is then the paragraph under its title with scripts on too, and is still what
    the question mark is described by."""
    request = RequestFactory().get("/accounts/profile/")
    html = render(
        '{% cotton section-title help="your-name" tip="the-sentence" :in_sight="True" %}'
        "Your name{% endcotton %}",
        request=request,
    )

    assert '<p id="the-sentence" data-help-summary>' in html
    assert "data-tooltip" not in html
    assert 'aria-describedby="the-sentence"' in html


def test_a_section_title_without_help_is_drawn_as_it_was():
    """A heading that asks for no help draws what it drew before #302, byte for byte: the
    switch for the question mark leaves no line behind it on the forty pages that never
    pass `help`."""
    plain = render("{% cotton section-title %}Your name{% endcotton %}")
    with_subtitle = render(
        "{% cotton section-title %}Your name"
        "{% cotton:slot subtitle %}Two lines.{% endcotton:slot %}{% endcotton %}"
    )

    assert plain.strip() == '<header class="section-title">\n  <h2>Your name</h2>\n  \n</header>'
    assert with_subtitle.strip() == (
        '<header class="section-title">\n  <h2>Your name</h2>\n  <p>Two lines.</p>\n</header>'
    )


@pytest.mark.parametrize(
    ("language", "turned"), [("ar", True), ("en-GB", False), ("he", False), ("fr-FR", False)]
)
def test_the_question_mark_turns_where_the_script_writes_it_turned(language, turned):
    """The Arabic script writes a question mark turned round and Hebrew does not, though
    both are read right to left (#302). Which script a language is written in is
    `core.languages`' to say, so the server marks the question mark and the stylesheet
    turns it by that mark: no second list of languages in the stylesheet."""
    from django.utils import translation

    with translation.override(language):
        html = render(
            '{% cotton help-mark topic="links" no_drawer / %}',
            request=RequestFactory().get("/accounts/profile/"),
        )
    mark = re.search(r"<a\b[^>]*data-help-mark[^>]*>", html).group(0)

    assert ("data-turned" in mark) is turned
    css = (TEMPLATES.parents[2] / "assets" / "css" / "app.css").read_text("utf-8")
    assert "[data-help-mark][data-turned]" in css
    assert '[data-icon="circle-question-mark"]:is(' not in css


def test_a_question_mark_can_leave_its_drawer_to_the_page():
    """Where several cards share a topic, each says `no_drawer` and the page draws the one
    drawer; a card says which card it is and shows its own sentence."""
    request = RequestFactory().get("/accounts/profile/")
    html = render(
        '{% cotton help-mark topic="links" subject="Websites" summary="A blog." no_drawer / %}',
        request=request,
    )

    assert '<span class="sr-only">Help: Websites</span>' in html and 'href="/help/links/"' in html
    assert re.search(r'<p id="help-tip-[0-9a-f]+" data-tooltip data-help-summary>A blog.</p>', html)
    assert "<dialog" not in html

    drawer = render('{% cotton help-drawer topic="links" / %}', request=request)
    assert '<dialog id="help-links" class="drawer" data-side="inline-end"' in drawer
    assert 'data-help-drawer="links"' in drawer


def test_a_drawer_is_a_dialog_at_the_inline_end_named_by_its_heading():
    """Basecoat's drawer on the browser's own `<dialog>` (#302): no `popover`, because only
    a script ever opens it -- what opens it is a link to a page holding the same thing --
    and at the inline end, which is a side Basecoat does not have and no side by name. The
    heading names it, *Close* is a button that submits nothing, and the body can take the
    focus so that the keyboard can scroll it."""
    html = render(
        '{% cotton drawer id="about" data-about="x" %}'
        "{% cotton:slot title %}About <bdi>this</bdi>{% endcotton:slot %}"
        "<p>Words.</p>{% endcotton %}"
    )

    assert (
        '<dialog id="about" class="drawer" data-side="inline-end" aria-labelledby="about-title" '
        'data-dialog data-drawer data-about="x">'
    ) in html
    assert "popover" not in html
    assert '<h2 id="about-title">About <bdi>this</bdi></h2>' in html
    close = re.search(r"<button[^>]*data-drawer-close[^>]*>", html).group(0)
    assert 'type="button"' in close and 'aria-label="Close"' in close
    assert 'class="btn shrink-0"' in close, "Close keeps its 24 pixels beside a long heading"
    assert '<section tabindex="0" aria-labelledby="about-title"><p>Words.</p></section>' in html
