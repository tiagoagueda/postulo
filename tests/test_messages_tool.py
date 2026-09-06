"""scripts/messages.py: extraction, the .po round trip, and the .mo it writes."""

import gettext
import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location(
        "messages_tool_unit", REPO / "scripts" / "messages.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["messages_tool_unit"] = module
    spec.loader.exec_module(module)
    return module


PYTHON = """
from django.utils.translation import gettext_lazy as _, ngettext, pgettext_lazy

label = _("Applications")
help_text = _(
    "Two parts, "
    "joined."
)
count = ngettext("%(n)s reminder", "%(n)s reminders", 3)
month = pgettext_lazy("month name", "May")
not_a_message = something._("ignored")
dynamic = _(variable)
"""

TEMPLATE = """{% load i18n %}
<h1>{% translate "Applications" %}</h1>
<p>{% translate "Save" context "button" %}</p>
{% blocktranslate count counter=total with term=query trimmed %}
  {{ counter }} result for “{{ term }}”
{% plural %}
  {{ counter }} results for “{{ term }}”
{% endblocktranslate %}
  {% if x %}
    <span>{% translate "Deeply indented" %}</span>
  {% endif %}
"""


def test_python_sources_yield_their_messages(tool):
    found = {m.key: m for m in tool.extract_python(PYTHON, "x.py")}
    assert (None, "Applications") in found
    assert (None, "Two parts, joined.") in found, "adjacent literals are one string"
    plural = found[(None, "%(n)s reminder")]
    assert plural.plural == "%(n)s reminders"
    assert ("month name", "May") in found
    assert not any("ignored" in key[1] for key in found), "a method called _ is not gettext"
    assert found[(None, "Applications")].references == ["x.py:4"]


def test_templates_yield_theirs_whatever_the_indentation(tool):
    import django
    from django.conf import settings

    if not settings.configured:  # pragma: no cover - configured by pytest-django
        settings.configure()
        django.setup()
    found = {m.key: m for m in tool.extract_template(TEMPLATE, "t.html")}
    assert (None, "Applications") in found
    assert ("button", "Save") in found
    assert (None, "Deeply indented") in found
    plural = found[(None, "%(counter)s result for “%(term)s”")]
    assert plural.plural == "%(counter)s results for “%(term)s”"


def test_a_catalogue_survives_the_round_trip(tool):
    message = tool.Message(
        msgid='Say "hello"\nto %(name)s',
        references=["a.py:1"],
        flags=["python-format", "draft"],
        msgstr=["Dire « bonjour »\nà %(name)s"],
        translator=["reviewed by nobody yet"],
    )
    plural = tool.Message(
        msgid="%(n)s day", plural="%(n)s days", msgstr=["%(n)s jour", "%(n)s jours"]
    )
    context = tool.Message(msgid="May", context="month name", msgstr=["mai"])
    catalogue = tool.Catalogue(
        header=tool.header_for("fr-fr", {}),
        messages={m.key: m for m in (message, plural, context)},
    )
    text = tool.dump(catalogue, "fr-fr")
    again = tool.parse(text)
    assert again.header["Plural-Forms"] == "nplurals=2; plural=(n > 1);"
    back = again.messages[message.key]
    assert back.msgstr == message.msgstr and back.flags == message.flags
    assert back.translator == ["reviewed by nobody yet"]
    assert again.messages[plural.key].msgstr == ["%(n)s jour", "%(n)s jours"]
    assert again.messages[("month name", "May")].msgstr == ["mai"]


def test_merging_keeps_translations_and_drops_what_the_source_lost(tool):
    old = tool.Catalogue(
        header={},
        messages={
            (None, "Keep"): tool.Message(msgid="Keep", msgstr=["Garder"], flags=["draft"]),
            (None, "Gone"): tool.Message(msgid="Gone", msgstr=["Parti"]),
        },
    )
    extracted = {
        (None, "Keep"): tool.Message(msgid="Keep", references=["a.py:2"]),
        (None, "New"): tool.Message(msgid="New", references=["a.py:3"]),
        (None, "%(n)s cat"): tool.Message(msgid="%(n)s cat", plural="%(n)s cats"),
    }
    merged = tool.merge(extracted, old, "pl")
    assert merged.messages[(None, "Keep")].msgstr == ["Garder"]
    assert merged.messages[(None, "Keep")].flags == ["draft"]
    assert merged.messages[(None, "New")].msgstr == [""]
    assert (None, "Gone") not in merged.messages
    assert merged.messages[(None, "%(n)s cat")].msgstr == ["", "", ""], "Polish has three forms"


def test_the_compiled_file_is_read_by_gettext_and_skips_fuzzy_only(tool):
    catalogue = tool.Catalogue(
        header={
            "Content-Type": "text/plain; charset=UTF-8",
            "Plural-Forms": "nplurals=2; plural=(n > 1);",
        },
        messages={
            (None, "Draft"): tool.Message(msgid="Draft", msgstr=["Brouillon"], flags=["draft"]),
            (None, "Fuzzy"): tool.Message(msgid="Fuzzy", msgstr=["Flou"], flags=["fuzzy"]),
            (None, "Empty"): tool.Message(msgid="Empty", msgstr=[""]),
            ("ctx", "May"): tool.Message(msgid="May", context="ctx", msgstr=["mai"]),
            (None, "%(n)s day"): tool.Message(
                msgid="%(n)s day", plural="%(n)s days", msgstr=["%(n)s jour", "%(n)s jours"]
            ),
        },
    )
    translations = gettext.GNUTranslations(io.BytesIO(tool.compile_catalogue(catalogue)))
    assert translations.gettext("Draft") == "Brouillon", "a draft is usable"
    assert translations.gettext("Fuzzy") == "Fuzzy", "a fuzzy entry is not"
    assert translations.gettext("Empty") == "Empty"
    assert translations.pgettext("ctx", "May") == "mai"
    assert translations.ngettext("%(n)s day", "%(n)s days", 1) == "%(n)s jour"
    assert translations.ngettext("%(n)s day", "%(n)s days", 2) == "%(n)s jours"


def test_placeholder_problems_are_named(tool):
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, "Hi %(name)s"): tool.Message(msgid="Hi %(name)s", msgstr=["Salut %(nom)s"]),
            (None, "%(n)s day"): tool.Message(
                msgid="%(n)s day", plural="%(n)s days", msgstr=["un jour", "%(n)s jours"]
            ),
            (None, "Fine %(x)s"): tool.Message(msgid="Fine %(x)s", msgstr=["Bien %(x)s"]),
        },
    )
    problems = tool.problems_in(catalogue, "fr-fr")
    assert len(problems) == 1 and "Hi %(name)s" in problems[0]
    assert any(p.endswith("has 3") for p in tool.problems_in(catalogue, "pl"))


def test_stats_count_drafts_apart_from_reviewed_work(tool):
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, "A"): tool.Message(msgid="A", msgstr=["a"], flags=["draft"]),
            (None, "B"): tool.Message(msgid="B", msgstr=["b"]),
            (None, "C"): tool.Message(msgid="C", msgstr=[""]),
            (None, "D"): tool.Message(msgid="D", msgstr=["d"], flags=["fuzzy"]),
        },
    )
    assert tool.stats_for(catalogue) == {
        "total": 4,
        "translated": 3,
        "drafts": 1,
        "fuzzy": 1,
        "reviewed": 2,
        "percent": 75,
    }
