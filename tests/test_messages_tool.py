"""scripts/messages.py: extraction, the .po round trip, and the .mo it writes."""

import gettext
import io
import json
import os
import struct
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
    assert found[(None, "Applications")].references == ["x.py"], "the file, never the line"


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
        references=["a.py"],
        flags=["python-format", "fuzzy"],
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
    assert back.msgstr == message.msgstr and back.flags == ["fuzzy", "python-format"], "sorted"
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
    assert merged.messages[(None, "Keep")].flags == ["fuzzy"], "draft is called fuzzy now"
    assert merged.messages[(None, "New")].msgstr == [""]
    assert (None, "Gone") not in merged.messages
    assert merged.messages[(None, "%(n)s cat")].msgstr == ["", "", ""], "Polish has three forms"


def test_the_compiled_file_is_read_by_gettext_and_carries_the_drafts(tool):
    """A draft is usable on the day it is written; one that would fail to format is not."""
    catalogue = tool.Catalogue(
        header={
            "Content-Type": "text/plain; charset=UTF-8",
            "Plural-Forms": "nplurals=2; plural=(n > 1);",
        },
        messages={
            (None, "Draft"): tool.Message(msgid="Draft", msgstr=["Brouillon"], flags=["fuzzy"]),
            (None, "Old"): tool.Message(msgid="Old", msgstr=["Ancien"], flags=["draft"]),
            (None, "Hi %(name)s"): tool.Message(
                msgid="Hi %(name)s", msgstr=["Salut %(nom)s"], flags=["fuzzy", "python-format"]
            ),
            (None, "Empty"): tool.Message(msgid="Empty", msgstr=[""]),
            ("ctx", "May"): tool.Message(msgid="May", context="ctx", msgstr=["mai"]),
            (None, "%(n)s day"): tool.Message(
                msgid="%(n)s day", plural="%(n)s days", msgstr=["%(n)s jour", "%(n)s jours"]
            ),
        },
    )
    compiled = tool.compile_catalogue(catalogue, "fr-FR")
    translations = gettext.GNUTranslations(io.BytesIO(compiled))
    assert translations.gettext("Draft") == "Brouillon", "a draft is usable"
    assert translations.gettext("Old") == "Ancien", "and so is one still flagged the old way"
    assert translations.gettext("Hi %(name)s") == "Hi %(name)s", "unless it would raise"
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
    """A draft is flagged ``fuzzy`` -- Weblate's *needs editing* -- and a reviewed string is
    one a speaker saved there, which cleared it. The two add up to what is translated, which
    is what the translation bar draws (#312, #706)."""
    catalogue = tool.Catalogue(
        header={},
        messages={
            (None, "A"): tool.Message(msgid="A", msgstr=["a"], flags=["fuzzy"]),
            (None, "B"): tool.Message(msgid="B", msgstr=["b"]),
            (None, "C"): tool.Message(msgid="C", msgstr=[""]),
            (None, "D"): tool.Message(msgid="D", msgstr=["d"], flags=["draft"]),
            (None, "E"): tool.Message(msgid="E", msgstr=[""], flags=["fuzzy"]),
        },
    )
    assert tool.stats_for(catalogue) == {
        "total": 5,
        "translated": 3,
        "drafts": 2,
        "reviewed": 1,
        "percent": 60,
    }


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
    assert len(catalogues) == len(tool.translated_languages()) == 84
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
    assert len(list(locale.glob("*/LC_MESSAGES/django.mo"))) == 84
    out = capsys.readouterr().out
    assert "84 catalogues compiled" in out
    assert not (locale / "status.json").exists(), "the status report is Postulo's alone"


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


# --------------------------------------------- a translation is text, not markup (#983)

HOSTILE = [
    # (the English, what a translator wrote)
    ("Written by someone", "<script>alert(1)</script>"),
    ("Written by someone", "<img src=x onerror=alert(1)>"),
    ("Written by someone", "Written by <a href='https://evil.example/'>someone</a>"),
    ("Written by someone", "Written by <!-- someone"),
    ("Written by someone", "Written by <b someone"),
    (
        'Sign in under <a href="{url}">Settings</a>',
        'Sign in under <a href="//evil.example">Settings</a>',
    ),
    (
        'Sign in under <a href="{url}">Settings</a>',
        'Sign in under <a href="{url}" onclick="x()">Settings</a>',
    ),
    ("Written by <bdi>Ana</bdi>", "Written by <bdi>Ana</bdi><script>"),
]


@pytest.mark.parametrize(("source", "form"), HOSTILE)
def test_markup_the_english_does_not_carry_is_a_problem(tool, source, form):
    message = tool.Message(msgid=source, msgstr=[form])
    assert tool.message_problems(message, "fr-FR"), form


@pytest.mark.parametrize(
    ("source", "form"),
    [
        ("Written by <bdi>Ana</bdi>", "Écrit par <bdi>Ana</bdi>"),
        (
            'Under <a href="{url}" class="underline">Settings</a>',
            'Sous <a href="{url}" class="underline">Réglages</a>',
        ),
        ("Press <kbd>d</kbd>", "Appuyez sur <kbd>d</kbd>"),
        ("Written by someone", "Écrit par quelqu'un"),
        ("1 < 2", "1 < 2"),
    ],
)
def test_the_markup_the_english_carries_may_be_kept(tool, source, form):
    assert not tool.message_problems(tool.Message(msgid=source, msgstr=[form]), "fr-FR")


def test_a_draft_with_markup_is_left_out_of_the_file_and_a_reviewed_one_fails_check(tool, tmp_path):
    """Drafts are compiled, but not one that adds markup; a reviewed one is a failure."""
    header = {
        "Content-Type": "text/plain; charset=UTF-8",
        "Plural-Forms": "nplurals=2; plural=(n > 1);",
    }
    draft = tool.Message(msgid="Written by someone", msgstr=["<script>x</script>"], flags=["fuzzy"])
    catalogue = tool.Catalogue(header=header, messages={(None, "Written by someone"): draft})
    compiled = tool.compile_catalogue(catalogue, "fr-FR")
    translations = gettext.GNUTranslations(io.BytesIO(compiled))
    assert translations.gettext("Written by someone") == "Written by someone"

    reviewed = tool.Message(msgid="Written by someone", msgstr=["<script>x</script>"])
    assert tool.problems_in(
        tool.Catalogue(header=header, messages={(None, "Written by someone"): reviewed}), "fr-FR"
    )


# ------------------------------------------ the ratchet on drafts left out (#910)


def _break_a_draft(tool, locale, folder, code):
    """Give a language a draft that invents a placeholder, as a machine draft used to."""
    path = locale / folder / "LC_MESSAGES" / "django.po"
    catalogue = tool.parse(path.read_text(encoding="utf-8"))
    message = catalogue.messages[(None, "Hello, world")]
    message.msgstr = ["Bonjour, %(monde)s"]
    message.flags = ["fuzzy"]
    path.write_text(tool.dump(catalogue, code), encoding="utf-8", newline="\n")


def test_a_new_draft_left_out_fails_check_and_names_the_language(tool, plugin_repo, capsys):
    tool.use(plugin_repo)
    assert tool.cmd_extract(check=False) == 0
    locale = plugin_repo / "src" / "postulo_example" / "locale"
    _break_a_draft(tool, locale, "fr_FR", "fr-FR")
    capsys.readouterr()

    assert tool.cmd_check() == 1, "no file is zero recorded, so one left out is one too many"
    out = capsys.readouterr().out
    assert "fr-FR: 1 draft(s) left out of the .mo files, more than the 0 recorded" in out
    assert "src/postulo_example/locale/excluded-drafts.txt" in out

    (locale / "excluded-drafts.txt").write_text(
        "# recorded\npostulo-example fr-FR 1\n", encoding="utf-8"
    )
    assert tool.cmd_check() == 0, "at the recorded number it passes"
    assert "more than" not in capsys.readouterr().out

    _break_a_draft(tool, locale, "pt_PT", "pt-PT")
    assert tool.cmd_check() == 1, "a draft in another language is counted for that language"
    out = capsys.readouterr().out
    assert "pt-PT: 1 draft(s) left out of the .mo files, more than the 0 recorded" in out
    assert "fr-FR: 1 draft(s) left out of the .mo files, more than" not in out


def test_fewer_drafts_left_out_than_recorded_asks_for_the_number_to_be_lowered(
    tool, plugin_repo, capsys
):
    tool.use(plugin_repo)
    assert tool.cmd_extract(check=False) == 0
    locale = plugin_repo / "src" / "postulo_example" / "locale"
    _break_a_draft(tool, locale, "fr_FR", "fr-FR")
    (locale / "excluded-drafts.txt").write_text(
        "postulo-example fr-FR 3\npostulo-example de 2\n", encoding="utf-8"
    )
    capsys.readouterr()

    assert tool.cmd_check() == 0, "a fix arrives in Weblate's pull request, which must pass"
    out = capsys.readouterr().out
    assert "fr-FR: 1 draft(s) left out of the .mo files, fewer than the 3 recorded" in out
    assert "to 'postulo-example fr-FR 1'" in out
    assert "de: 0 draft(s) left out of the .mo files, fewer than the 2 recorded" in out
    assert "by removing its line" in out


def test_a_malformed_line_in_the_record_is_refused(tool, plugin_repo):
    tool.use(plugin_repo)
    assert tool.cmd_extract(check=False) == 0
    locale = plugin_repo / "src" / "postulo_example" / "locale"
    (locale / "excluded-drafts.txt").write_text("postulo-example fr-FR many\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="expected '<set> <language> <count>'"):
        tool.cmd_check()


def test_postulos_record_names_only_sets_and_languages_it_has(tool):
    """A typo in the record would hold a number nothing ever counts against."""
    tool.use(REPO)
    sets = {subject.name for subject in tool.catalogue_sets()}
    languages = set(tool.translated_languages())
    for name, code in tool.recorded_excluded_drafts():
        assert name in sets, name
        assert code in languages, code


# ------------------------------- the walks, and what extract leaves alone (#1169)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _says(text):
    return f'from django.utils.translation import gettext_lazy as _\n\nLABEL = _("{text}")\n'


@pytest.fixture
def nested_repo(tmp_path, tool):
    """A project with a set of its own, one nested in it, and directories no walk enters."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "postulo-example"\nversion = "0.3.0"\n')
    package = tmp_path / "src" / "postulo_example"
    _write(package / "__init__.py", _says("Hello, world"))
    _write(package / "templates" / "page.html", '{% load i18n %}{% translate "A page" %}\n')
    _write(package / "templates" / "page.css", "/* not a source */\n")
    _write(package / "migrations" / "0001_initial.py", _says("In a migration"))
    _write(package / "static" / "vendor" / "tool.py", _says("In a static file"))
    _write(package / "static" / "locale" / "notes.txt", "Under static, so not a set.\n")
    _write(package / "__pycache__" / "old.py", _says("In a cache"))
    _write(package / "locale" / "notes.txt", "Beside the catalogues, so not a source.\n")
    inner = package / "plugins" / "inner"
    _write(inner / "__init__.py", _says("Inner hello"))
    _write(inner / "forms.py", _says("Inner form"))
    _write(inner / "migrations" / "0001_initial.py", _says("In a nested migration"))
    (inner / "locale").mkdir()
    yield tmp_path
    tool.use(REPO)


def _within(path, directory):
    return path == directory or directory in path.parents


def _walked_whole(root):
    """Every directory and every file under ``root``, nothing left out, from one walk."""
    directories, files = [], []
    for where, names, others in os.walk(root):
        directories += [Path(where) / name for name in names]
        files += [Path(where) / name for name in others]
    return sorted(directories), sorted(files)


def _sets_before_the_walk_was_pruned(tool, directories=None):
    """`catalogue_sets` as it was until #1169: everything walked, the skipped discarded
    after. ``directories`` stands in for the walk where one listing serves several calls."""
    project = tool.project()
    if directories is None:
        found = sorted(project.package.rglob("locale"))
    else:
        found = [directory for directory in directories if directory.name == "locale"]
    sets = [tool.CatalogueSet(project.name, project.package, project.locale)]
    for locale in found:
        if locale == project.locale or not locale.is_dir():
            continue
        if tool.SKIP_DIRS & set(locale.relative_to(project.package).parts[:-1]):
            continue
        root = locale.parent
        sets.append(tool.CatalogueSet(root.relative_to(project.package).as_posix(), root, locale))
    return sets


def _sources_before_the_walk_was_pruned(subject, sets, skip, files=None):
    """`sources` as it was until #1169, the same way."""
    nested = [s.root for s in sets if s.root != subject.root]
    inside = [other for other in nested if _within(other, subject.root)]
    found = []
    for path in sorted(subject.root.rglob("*")) if files is None else files:
        if path.suffix not in (".py", ".html", ".txt") or not path.is_file():
            continue
        if skip & set(path.relative_to(subject.root).parts[:-1]):
            continue
        if any(_within(path, other) for other in inside):
            continue
        found.append(path)
    return found


def test_the_pruned_walks_find_what_the_whole_walks_found(tool, nested_repo):
    """The walks stopped entering the directories they skip (#1169); what they find is the
    same: a set per ``locale/`` outside those, and each file in the innermost set around it."""
    tool.use(nested_repo)
    package = nested_repo / "src" / "postulo_example"
    inner = package / "plugins" / "inner"

    sets = tool.catalogue_sets()
    assert [(s.name, s.root, s.locale) for s in sets] == [
        ("postulo-example", package, package / "locale"),
        ("plugins/inner", inner, inner / "locale"),
    ]
    assert sets == _sets_before_the_walk_was_pruned(tool)

    own, nested = sets
    assert tool.sources(own, sets) == [package / "__init__.py", package / "templates" / "page.html"]
    assert tool.sources(nested, sets) == [inner / "__init__.py", inner / "forms.py"]
    for subject in sets:
        before = _sources_before_the_walk_was_pruned(subject, sets, tool.SKIP_DIRS)
        assert tool.sources(subject, sets) == before
        assert tool.sources(subject) == before, "and the same for a caller with no list to pass"
    assert tool.sources() == tool.sources(own, sets), "the project's own set by default"


def test_the_pruned_walks_find_in_postulo_what_the_whole_walks_found(tool):
    """The same on the real tree, where the skipped directories are nineteen in twenty
    (#1169): the sets and every set's sources, against one walk that leaves nothing out."""
    tool.use(REPO)
    directories, files = _walked_whole(tool.project().package)
    sets = tool.catalogue_sets()
    assert sets == _sets_before_the_walk_was_pruned(tool, directories)
    assert len(sets) > 20 and sets[0].is_core
    named = [(str(path), path) for path in files]
    claimed = 0
    for subject in sets:
        below = [path for name, path in named if name.startswith(f"{subject.root}{os.sep}")]
        found = tool.sources(subject, sets)
        assert found == _sources_before_the_walk_was_pruned(subject, sets, tool.SKIP_DIRS, below)
        claimed += len(found)
    assert claimed > 500, "and it is the tree that was compared, not two empty lists"


def test_an_extract_finds_the_sets_once_and_enters_no_directory_it_skips(
    tool, nested_repo, monkeypatch
):
    """``extract --check`` looked for the catalogue sets once for itself and once more for
    every set, and each of those walks, like the walk for a set's sources, entered every
    ``locale/`` tree only to discard what it found there: 29 walks of 4,955 directories,
    4,704 of them catalogues, a third of the command's time (#1169)."""
    tool.use(nested_repo)
    package = nested_repo / "src" / "postulo_example"
    assert tool.cmd_extract(check=False) == 0, "so that each locale/ holds every language"

    searches = []
    find = tool.catalogue_sets
    entered = []
    scan = os.scandir

    def scandir(path="."):
        entered.append(Path(os.fspath(path)))
        return scan(path)

    with monkeypatch.context() as patched:
        patched.setattr(tool, "catalogue_sets", lambda: searches.append(1) or find())
        patched.setattr(os, "scandir", scandir)
        assert tool.cmd_extract(check=True) == 0

    assert len(searches) == 1, "the command finds the sets and passes them down"
    assert package in entered and package / "plugins" / "inner" in entered, "the walks are seen"
    skipped = [
        directory
        for directory in entered
        if directory.is_relative_to(package)
        and tool.SKIP_DIRS & set(directory.relative_to(package).parts)
    ]
    assert skipped == [], "and no walk goes into a directory whose files it would discard"


def test_an_extract_that_changes_nothing_writes_nothing(tool, plugin_repo, capsys):
    """``extract`` wrote every catalogue whether or not its text had changed: in Postulo
    2,352 files and 73 MB, each with a new modification time for git to hash again, an
    editor to reload and a file watcher to report (#1169). Now it writes the ones that
    differ, and says how many that was."""
    tool.use(plugin_repo)
    locale = plugin_repo / "src" / "postulo_example" / "locale"
    assert tool.cmd_extract(check=False) == 0
    assert "84 written, 0 unchanged." in capsys.readouterr().out

    catalogues = sorted(locale.glob("*/LC_MESSAGES/django.po"))
    long_ago = 1_000_000_000 * 10**9

    def age():
        for path in catalogues:
            os.utime(path, ns=(long_ago, long_ago))
        return {path: path.stat().st_mtime_ns for path in catalogues}

    stamps = age()
    assert tool.cmd_extract(check=False) == 0
    assert "0 written, 84 unchanged." in capsys.readouterr().out
    assert {path: path.stat().st_mtime_ns for path in catalogues} == stamps, "not one touched"

    french = locale / "fr_FR" / "LC_MESSAGES" / "django.po"
    portuguese = locale / "pt_PT" / "LC_MESSAGES" / "django.po"
    text = french.read_text(encoding="utf-8")
    french.write_text(text.replace("Hello, world", "Hello, word"), encoding="utf-8", newline="\n")
    portuguese.unlink()
    catalogues.remove(portuguese)
    stamps = age()
    assert tool.cmd_extract(check=True) == 1, "--check still names what is out of date"
    out = capsys.readouterr().out
    assert str(french.relative_to(plugin_repo)) in out
    assert str(portuguese.relative_to(plugin_repo)) in out
    assert out.count("django.po") == 2
    assert not portuguese.exists(), "and still writes nothing"

    assert tool.cmd_extract(check=False) == 0
    assert "2 written, 82 unchanged." in capsys.readouterr().out
    assert french.read_text(encoding="utf-8") == text
    assert portuguese.exists()
    moved = [path for path in catalogues if path.stat().st_mtime_ns != stamps[path]]
    assert moved == [french], "only the one that differed, and the one that was missing"


# --------------------------------------- a compile reads each catalogue once (#1145)


def _catalogue(tool, subject, sets, code, forms=None, drafts=()):
    """One language's catalogue in a temporary project: the slots ``extract`` writes for it,
    and the translations Weblate would have saved in them."""
    catalogue = tool.merge(tool.extract_all(subject, sets), None, code)
    for msgid, form in (forms or {}).items():
        message = catalogue.messages[(None, msgid)]
        message.msgstr = [form]
        message.flags = ["fuzzy"] if msgid in drafts else []
    path = tool.po_path(code, subject)
    path.parent.mkdir(parents=True)
    path.write_text(tool.dump(catalogue, code, subject), encoding="utf-8", newline="\n")


@pytest.fixture
def small_postulo(tmp_path, tool):
    """A project that calls itself Postulo, which is the one a compile writes the status
    report for. Two strings of its own and a nested set that repeats one and adds one;
    French in both sets with something to count, Portuguese in its own set alone, and no
    other language: a compile costs what its catalogues cost, and three show what 2,352 do."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "postulo"\nversion = "0.6.0"\n')
    package = tmp_path / "src" / "postulo"
    _write(package / "__init__.py", _says("Hello, world") + 'OTHER = _("Goodbye")\n')
    inner = package / "plugins" / "inner"
    _write(inner / "__init__.py", _says("Hello, world") + 'OTHER = _("Inner hello")\n')
    (inner / "locale").mkdir()
    tool.use(tmp_path)
    sets = tool.catalogue_sets()
    own, nested = sets
    draft = {"Hello, world": "Bonjour, le monde"}
    _catalogue(tool, own, sets, "fr-FR", draft, drafts=draft)
    _catalogue(tool, nested, sets, "fr-FR", {"Hello, world": "Salut", "Inner hello": "Bonjour"})
    _catalogue(tool, own, sets, "pt-PT")
    yield tmp_path
    tool.use(REPO)


def test_compile_writes_the_status_report_the_picker_reads(tool, small_postulo, capsys):
    """The picker and *Defaults* read locale/status.json and nothing else (#495). It is
    written with the .mo files rather than committed: every translation saved in Weblate
    changes it, and a committed copy was stale on every one of its pull requests (#349).

    Shown on three catalogues. It used to compile Postulo's own tree with the writes
    patched out, which parsed all 2,352 catalogues more than once to say the same, and was
    the third slowest test of the suite (#1033)."""
    status = small_postulo / "src" / "postulo" / "locale" / "status.json"
    assert not status.exists()

    assert tool.cmd_compile() == 0

    report = tool.build_report()
    assert set(report) == {"fr-FR", "pt-PT"}, "there is something to report"
    assert status.read_bytes() == tool._status_text(report).encode(), "what stats would print"
    assert f"written {status.relative_to(small_postulo)}" in capsys.readouterr().out


def test_a_compile_parses_each_catalogue_once(tool, small_postulo, monkeypatch, capsys):
    """``compile`` parsed every catalogue to write its .mo file, and the status report then
    read and parsed every one of them again: 2,352 catalogues twice, about ten of a
    compile's 27 seconds (#1145). The report is handed what the compile parsed."""
    package = small_postulo / "src" / "postulo"
    french = package / "locale" / "fr_FR" / "LC_MESSAGES" / "django.po"
    _write(package / "locale" / "xx" / "LC_MESSAGES" / "django.po", french.read_text("utf-8"))
    catalogues = sorted(package.rglob("django.po"))
    assert len(catalogues) == 4, "the fixture's three, and one in a directory that is no language"

    parsed = []
    parse = tool.parse
    with monkeypatch.context() as patched:
        patched.setattr(tool, "parse", lambda text: parsed.append(1) or parse(text))
        assert tool.cmd_compile() == 0

    assert len(parsed) == 4, "each one once"
    assert "4 catalogues compiled." in capsys.readouterr().out
    for path in catalogues:
        catalogue = tool.parse(path.read_text(encoding="utf-8"))
        assert path.with_suffix(".mo").read_bytes() == tool.compile_catalogue(catalogue), path
    report = json.loads((package / "locale" / "status.json").read_text(encoding="utf-8"))
    assert report == tool.build_report(), "and the report is the one reading them again gives"
    assert report == {
        # Postulo's own draft of the string both sets hold, not the nested set's reviewed one.
        "fr-FR": {"total": 3, "translated": 2, "drafts": 1, "reviewed": 1, "percent": 67},
        "pt-PT": {"total": 2, "translated": 0, "drafts": 0, "reviewed": 0, "percent": 0},
    }, "a row for each language that has a catalogue, counted across the sets that have it"


def test_the_tables_of_a_compiled_file_are_packed_once(tool, monkeypatch):
    """Each (length, offset) pair of a .mo file's two tables was packed on its own and added
    to the bytes written so far, which copied all of them every time: 9,684 times for a
    full core catalogue (#1145). The tables are packed in one go, and the file is laid out
    as it was: the header, the two tables, the ids, the strings."""

    def catalogue(size):
        messages = [
            tool.Message(msgid=f"String {n}", msgstr=[f"Chaîne {n}"]) for n in range(size)
        ] + [
            tool.Message(msgid="May", context="ctx", msgstr=["mai"]),
            tool.Message(
                msgid="%(n)s day", plural="%(n)s days", msgstr=["%(n)s jour", "%(n)s jours"]
            ),
            tool.Message(msgid="Empty", msgstr=[""]),
        ]
        header = {
            "Content-Type": "text/plain; charset=UTF-8",
            "Plural-Forms": "nplurals=2; plural=(n > 1);",
        }
        return tool.Catalogue(header=header, messages={m.key: m for m in messages})

    packs = []
    pack = struct.pack
    with monkeypatch.context() as patched:
        patched.setattr(struct, "pack", lambda *args: packs.append(args[0]) or pack(*args))
        tool.compile_catalogue(catalogue(3), "fr-FR")
        few = len(packs)
        data = tool.compile_catalogue(catalogue(300), "fr-FR")
    assert len(packs) == 2 * few, "as many packs for three hundred entries as for three"

    magic, revision, count, ids_table, strings_table, hash_size, hash_offset = struct.unpack_from(
        "<7I", data
    )
    assert (magic, revision, hash_size, hash_offset) == (0x950412DE, 0, 0, 0)
    assert count == 300 + 2 + 1, "every translated entry, and the header"
    assert (ids_table, strings_table) == (28, 28 + 8 * count)
    position = 28 + 16 * count
    read = []
    for table in (ids_table, strings_table):
        for index in range(count):
            length, offset = struct.unpack_from("<2I", data, table + 8 * index)
            assert offset == position, "each follows the last, with nothing between"
            assert data[offset + length] == 0
            read.append(data[offset : offset + length])
            position += length + 1
    assert position == len(data)
    ids, strings = read[:count], read[count:]
    assert ids == sorted(ids) and ids[0] == b""
    assert strings[ids.index(b"ctx\x04May")] == b"mai"
    assert strings[ids.index(b"%(n)s day\x00%(n)s days")] == b"%(n)s jour\x00%(n)s jours"
    assert strings[ids.index(b"String 7")] == "Chaîne 7".encode()
    assert gettext.GNUTranslations(io.BytesIO(data)).gettext("String 299") == "Chaîne 299"
