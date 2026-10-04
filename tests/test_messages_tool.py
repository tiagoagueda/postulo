"""scripts/messages.py: extraction, the .po round trip, and the .mo it writes."""

import gettext
import io
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    from postulo.core import messages_tool

    messages_tool.use(REPO)
    return messages_tool


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
        header=tool.header_for("fr-FR", {}),
        messages={m.key: m for m in (message, plural, context)},
    )
    text = tool.dump(catalogue, "fr-FR")
    again = tool.parse(text)
    assert again.header["Plural-Forms"] == "nplurals=2; plural=(n > 1);"
    back = again.messages[message.key]
    assert back.msgstr == message.msgstr and back.flags == message.flags
    assert back.translator == ["reviewed by nobody yet"]
    assert again.messages[plural.key].msgstr == ["%(n)s jour", "%(n)s jours"]
    assert again.messages[("month name", "May")].msgstr == ["mai"]


def test_backslashes_and_carriage_returns_survive_the_round_trip(tool):
    ids = [
        "Type C:\\new",
        "tab\\t here",
        'quote \\" and "',
        "two\\\\n",
        "carriage\rreturn",
        "mixed \\n\n\\t\t\r\\",
    ]
    catalogue = tool.Catalogue(
        header=tool.header_for("fr-FR", {}),
        messages={m.key: m for m in (tool.Message(msgid=i, msgstr=[i + " fr"]) for i in ids)},
    )
    again = tool.parse(tool.dump(catalogue, "fr-FR"))
    assert set(again.messages) == set(catalogue.messages)
    for message in catalogue.messages.values():
        assert again.messages[message.key].msgstr == message.msgstr


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
    problems = tool.problems_in(catalogue, "fr-FR")
    assert len(problems) == 1 and "Hi %(name)s" in problems[0]
    assert any(p.endswith("has 3") for p in tool.problems_in(catalogue, "pl"))


def test_a_form_that_also_counts_twenty_one_cannot_spell_out_one(tool):
    """Slavic, Baltic and Icelandic first forms cover 1, 21, 31 and so on, and Slovene's
    covers 101. A form that only ever says *one* may drop the count; one that also counts
    higher may not, or the page says "one application" at twenty-one, which nobody sees
    until somebody has twenty-one of something (#250).
    """

    def catalogue(*forms):
        return tool.Catalogue(
            header={},
            messages={
                (None, "One row ticked."): tool.Message(
                    msgid="One row ticked.", plural="%(count)s rows ticked.", msgstr=list(forms)
                )
            },
        )

    assert tool.counts_beyond_one("fr-FR") == (False, True), "0 and 1, then everything else"
    assert tool.counts_beyond_one("bs") == (True, True, True), "the first form counts 21"
    assert tool.counts_beyond_one("sl") == (True, True, True, True), "and Slovene's 101"

    french = catalogue("Une ligne cochée.", "%(count)s lignes cochées.")
    assert tool.problems_in(french, "fr-FR") == []

    spelt_out = catalogue(
        "Jedan red označen.", "%(count)s reda označena.", "%(count)s redova označeno."
    )
    problems = tool.problems_in(spelt_out, "bs")
    assert len(problems) == 1 and "form 0" in problems[0] and "%(count)s" in problems[0]

    counted = catalogue(
        "%(count)s red označen.", "%(count)s reda označena.", "%(count)s redova označeno."
    )
    assert tool.problems_in(counted, "bs") == []

    dual = catalogue("Ena vrstica.", "Dve vrstici.", "%(count)s vrstice.", "%(count)s vrstic.")
    problems = tool.problems_in(dual, "sl")
    assert [p for p in problems if "form 0" in p] and [p for p in problems if "form 1" in p]


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
        # B alone. D has text in it and is not a draft, but nobody has settled it and
        # nobody reads it, so it is not reviewed either; it used to be counted as if it
        # were (#312).
        "reviewed": 1,
        "percent": 75,
    }


def test_a_fuzzy_draft_is_counted_once(tool):
    """Flagged both ways, it is a draft and it is fuzzy, and it is still not reviewed: the
    parts the translation bar draws come out of these counts and must add up (#312)."""
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, "A"): tool.Message(msgid="A", msgstr=["a"], flags=["draft", "fuzzy"]),
            (None, "B"): tool.Message(msgid="B", msgstr=[""], flags=["fuzzy"]),
        },
    )
    stats = tool.stats_for(catalogue)

    assert (stats["translated"], stats["drafts"], stats["fuzzy"], stats["reviewed"]) == (
        1,
        1,
        2,
        0,
    )


# ------------------------------------------- the tool in a plugin's repository (#187)


@pytest.fixture
def plugin_repo(tmp_path, tool):
    """A repository shaped like every official plugin: pyproject, src/<package>, a string."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "postulo-example"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    package = tmp_path / "src" / "postulo_example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        'from django.utils.translation import gettext_lazy as _\n\nLABEL = _("Hello, world")\n',
        encoding="utf-8",
    )
    yield tmp_path
    tool.use(REPO)


def test_the_tool_points_at_whichever_project_it_is_given(tool, plugin_repo):
    project = tool.use(plugin_repo)
    assert project.name == "postulo-example"
    assert project.package == plugin_repo / "src" / "postulo_example"
    assert not project.is_postulo

    sets = tool.catalogue_sets()
    assert [s.name for s in sets] == ["postulo-example"], "one set, the plugin's own"
    assert sets[0].is_core, "the project's own set, whatever the project"


def test_a_plugin_gets_every_slot_postulo_has_and_the_same_four_commands(tool, plugin_repo, capsys):
    tool.use(plugin_repo)
    locale = plugin_repo / "src" / "postulo_example" / "locale"

    assert tool.cmd_extract(check=False) == 0
    catalogues = sorted(locale.glob("*/LC_MESSAGES/django.po"))
    assert len(catalogues) == len(tool.translated_languages()) == 68
    french = tool.parse(
        (locale / "fr_FR" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
    )
    assert [m.msgid for m in french.messages.values()] == ["Hello, world"]
    assert "postulo-example" in (locale / "fr_FR" / "LC_MESSAGES" / "django.po").read_text(
        encoding="utf-8"
    ), "the header names the project, not Postulo"

    assert tool.cmd_extract(check=True) == 0, "current the moment it is written"
    assert tool.cmd_check() == 0
    assert tool.cmd_compile() == 0
    assert len(list(locale.glob("*/LC_MESSAGES/django.mo"))) == 68
    out = capsys.readouterr().out
    assert "68 catalogues compiled" in out


def test_the_tool_refuses_a_directory_that_is_not_a_project(tool, tmp_path):
    with pytest.raises(SystemExit, match="pyproject"):
        tool.use(tmp_path)
    tool.use(REPO)


@pytest.mark.parametrize(
    ("msgid", "translation"),
    [
        ("%(share)s%%", "%(share)s %"),
        ("%(share)s%%", "% %(share)s"),
        ("%(n)s done", "%(n)s % d"),
        ("Delete %s?", "Supprimer ?"),
    ],
)
def test_a_translation_that_would_raise_when_formatted_is_refused(tool, msgid, translation):
    """The placeholder sets cannot see a stray `%`; formatting the form can (#497)."""
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, msgid): tool.Message(msgid=msgid, msgstr=[translation], flags=["python-format"])
        },
    )
    problems = tool.problems_in(catalogue, "fr-FR")
    assert len(problems) == 1 and msgid in problems[0]


def test_a_translation_that_formats_cleanly_is_accepted(tool):
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, "%(share)s%%"): tool.Message(
                msgid="%(share)s%%", msgstr=["%(share)s %%"], flags=["python-format"]
            ),
            (None, "Delete %s?"): tool.Message(
                msgid="Delete %s?", msgstr=["Supprimer %s ?"], flags=["python-format"]
            ),
        },
    )
    assert tool.problems_in(catalogue, "fr-FR") == []


# ------------------------------------------- a plugin's catalogues state its licence (#417)


@pytest.fixture
def mit_repo(tmp_path, tool):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "postulo-example"\nversion = "0.3.0"\nlicense = "MIT"\n'
        '[project.urls]\nIssues = "https://example.org/example/issues"\n',
        encoding="utf-8",
    )
    package = tmp_path / "src" / "postulo_example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        'from django.utils.translation import gettext_lazy as _\n\nLABEL = _("Hello, world")\n',
        encoding="utf-8",
    )
    yield tmp_path
    tool.use(REPO)


def test_a_plugins_catalogue_names_its_own_licence_not_postulos(tool, mit_repo):
    tool.use(mit_repo)
    text = tool.dump(tool.parse(""), "fr-FR", tool.catalogue_sets()[0])
    comments = text.split('msgid ""')[0]
    assert "postulo-example" in comments
    assert "MIT" in comments
    assert "AGPL" not in comments


def test_a_plugin_reports_bugs_to_itself_and_postulo_keeps_its_own_header(tool, mit_repo):
    tool.use(mit_repo)
    header = tool.header_for("fr-FR", {})
    assert header["Report-Msgid-Bugs-To"] == "https://example.org/example/issues"
    assert header["Project-Id-Version"] == "postulo-example"
    assert header["X-Generator"] == "postulo-messages"
    tool.use(REPO)
    core = tool.header_for("fr-FR", {})
    assert core["Report-Msgid-Bugs-To"].startswith("https://source.tiagoagueda.com/postulo/")
    assert core["Project-Id-Version"] == "Postulo"
    assert "AGPL" in tool.dump(tool.parse(""), "fr-FR").split('msgid ""')[0]


def test_a_plugin_without_a_url_or_licence_claims_neither(tool, plugin_repo):
    tool.use(plugin_repo)
    header = tool.header_for("fr-FR", {})
    assert "Report-Msgid-Bugs-To" not in header
    comments = tool.dump(tool.parse(""), "fr-FR", tool.catalogue_sets()[0]).split('msgid ""')[0]
    assert "AGPL" not in comments
    assert tool.cmd_extract(check=False) == 0
    assert tool.cmd_extract(check=True) == 0


# `scripts/messages.py stats --check` in CI's Checks job, once per push (#725).
@pytest.mark.step
def test_the_committed_status_report_is_what_the_catalogues_give(tool):
    # The picker and *Defaults* read locale/status.json and nothing else, so a file nobody
    # refreshed shows languages as complete that are not (#495). CI runs `stats --check`.
    stored = (tool.project().locale / "status.json").read_text(encoding="utf-8")
    assert stored == tool._status_text(tool.build_report()), "run `stats --write`"
