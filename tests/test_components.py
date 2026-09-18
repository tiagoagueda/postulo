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
    form = NameForm(data={})  # bound and empty: the required field has an error
    html = render('{% cotton field :field="form.name" / %}', form=form)

    assert '<label class="field-label" for="id_name">' in html
    assert '<span aria-hidden="true" class="text-red-600">*</span>' in html, "required"
    assert 'name="name"' in html and 'class="field-input"' in html
    assert '<p class="field-help" id="id_name_helptext">As it appears on your passport.</p>' in html
    assert '<div id="id_name_error" role="alert">' in html
    assert '<p class="field-error">This field is required.</p>' in html


def test_an_optional_field_has_no_asterisk_and_no_error_block():
    html = render('{% cotton field :field="form.note" / %}', form=NameForm())
    assert "*" not in html and 'role="alert"' not in html and "field-help" not in html


def test_feedback_can_leave_the_help_to_the_caller():
    """``errors-only`` is for a caller that drew the help above the controls itself (#139)."""
    form = NameForm(data={})
    with_help = render('{% cotton field-feedback :field="form.name" / %}', form=form)
    without = render('{% cotton field-feedback :field="form.name" errors-only / %}', form=form)

    assert "field-help" in with_help
    assert "field-help" not in without and 'id="id_name_error"' in without


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


def test_isolation_would_run_every_context_processor_once_per_component(db, monkeypatch):
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
        from django.test import override_settings

        # The processor list holds import paths, so the engine is rebuilt to find the
        # patched function; a settings change does exactly that.
        with override_settings(COTTON_ENABLE_CONTEXT_ISOLATION=isolation):
            calls.clear()
            engine = engines["django"].engine
            engine.from_string(source).render(RequestContext(request, {"form": NameForm()}))
            return len(calls)

    assert rendered_with(False) == 1, "the page's own pass, and nothing per component"
    # Three fields, each drawing its feedback as a component of its own: six more passes.
    assert rendered_with(True) == 7, "the page's pass plus one per component, nested ones too"
