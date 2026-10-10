"""`scripts/messages.py guard` and `gate`: translations come from Weblate and nowhere else.

The rule (#706): every translation, correction and review is made in Weblate, which commits
it back as a pull request. A commit made anywhere else may change the English, add an empty
slot, lose a string the source dropped -- and nothing else about a translation. `guard`
reads each commit of a push or a pull request and names whatever breaks that, and the same
check runs on what is staged before a commit is made.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tool():
    from postulo.core import messages_tool

    messages_tool.use(REPO)
    return messages_tool


HEADER = 'msgid ""\nmsgstr ""\n"Language: fr_FR\\n"\n'


def po(*entries: str) -> str:
    return HEADER + "".join(f"\n{entry}\n" for entry in entries)


def entry(msgid: str, msgstr: str = "", *, fuzzy: bool = False, ref: str = "a.py") -> str:
    flags = "#, fuzzy\n" if fuzzy else ""
    return f'#: {ref}\n{flags}msgid "{msgid}"\nmsgstr "{msgstr}"'


# ------------------------------------------------------------- what a commit may do


def test_the_english_the_references_and_new_empty_slots_are_free(tool):
    before = po(entry("Save", "Enregistrer"), entry("Gone", "Parti"))
    after = po(entry("Save", "Enregistrer", ref="b.py"), entry("New"))
    assert tool.translation_changes(before, after, carry_reviewed=False) == []


def test_writing_a_translation_is_refused(tool):
    problems = tool.translation_changes(
        po(entry("New")), po(entry("New", "Nouveau")), carry_reviewed=False
    )
    assert len(problems) == 1 and "changed here" in problems[0]


def test_correcting_one_is_refused(tool):
    before, after = po(entry("Save", "Sauver")), po(entry("Save", "Enregistrer"))
    assert tool.translation_changes(before, after, carry_reviewed=False)


def test_reviewing_one_is_refused_and_so_is_marking_it_a_draft(tool):
    draft, reviewed = po(entry("Save", "Enregistrer", fuzzy=True)), po(entry("Save", "Enregistrer"))
    assert "reviewed" in tool.translation_changes(draft, reviewed, carry_reviewed=False)[0]
    assert "a draft" in tool.translation_changes(reviewed, draft, carry_reviewed=False)[0]


def test_the_old_draft_flag_becoming_fuzzy_is_not_a_review(tool):
    old = HEADER + '\n#, draft\nmsgid "Save"\nmsgstr "Enregistrer"\n'
    assert (
        tool.translation_changes(
            old, po(entry("Save", "Enregistrer", fuzzy=True)), carry_reviewed=False
        )
        == []
    )


def test_a_new_string_arriving_translated_is_refused(tool):
    problems = tool.translation_changes(po(), po(entry("New", "Nouveau")), carry_reviewed=False)
    assert "arrives with a translation" in problems[0]


def test_a_new_catalogue_must_be_empty(tool):
    assert tool.translation_changes(None, po(entry("New")), carry_reviewed=False) == []
    assert tool.translation_changes(None, po(entry("New", "Nouveau")), carry_reviewed=False)


def test_a_renamed_string_may_carry_its_translation_as_a_draft(tool):
    """A script that renames a string should not lose what speakers wrote for it."""
    before = po(entry("Kind", "Genre"))
    carried = po(entry("Type", "Genre", fuzzy=True))
    assert tool.translation_changes(before, carried, carry_reviewed=False) == []


def test_carrying_it_as_reviewed_needs_the_trailer(tool):
    before, after = po(entry("Kind", "Genre")), po(entry("Type", "Genre"))
    assert (
        "without being marked a draft"
        in tool.translation_changes(before, after, carry_reviewed=False)[0]
    )
    assert tool.translation_changes(before, after, carry_reviewed=True) == []


def test_a_carry_must_match_a_string_the_same_change_removed(tool):
    before, after = (
        po(entry("Kind", "Genre")),
        po(entry("Kind", "Genre"), entry("Type", "Genre", fuzzy=True)),
    )
    assert tool.translation_changes(before, after, carry_reviewed=False), "Kind was not removed"


# --------------------------------------------------------------- who is Weblate


def test_weblate_is_known_by_its_committer(tool):
    assert tool.from_weblate("weblate@tiagoagueda.com", "anything", ["src/x.py"])


def test_or_by_its_trailer_on_a_commit_of_catalogues_only(tool):
    """What is left of Weblate's commit after a squash or rebase merge by the maintainer."""
    message = "Translated using Weblate\n\nTranslate-URL: https://translate.tiagoagueda.com/projects/postulo/postulo/fr/\n"
    assert tool.from_weblate(
        "me@example.org", message, ["src/postulo/locale/fr_FR/LC_MESSAGES/django.po"]
    )
    assert not tool.from_weblate("me@example.org", message, ["src/postulo/views.py", "x.po"])
    assert not tool.from_weblate("me@example.org", "no trailer", ["x.po"])


# ------------------------------------------------------------ through git itself


def git(root: Path, *args: str, email: str = "me@example.org") -> str:
    env = {
        "GIT_AUTHOR_NAME": "Me",
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": "Me",
        "GIT_COMMITTER_EMAIL": email,
        "PATH": os.environ["PATH"],
        "HOME": str(root),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
    }
    return subprocess.run(  # noqa: S603 - git, with this test's own arguments
        ["git", "-c", "core.autocrlf=false", *args],  # noqa: S607
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path, tool):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "postulo-example"\n', encoding="utf-8"
    )
    catalogue = (
        tmp_path / "src" / "postulo_example" / "locale" / "fr_FR" / "LC_MESSAGES" / "django.po"
    )
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(po(entry("Save")), encoding="utf-8", newline="\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "start")
    tool.use(tmp_path)
    yield tmp_path, catalogue
    tool.use(REPO)


def test_guard_names_the_commit_that_translated_by_hand(tool, repo, capsys):
    root, catalogue = repo
    base = git(root, "rev-parse", "HEAD").strip()
    catalogue.write_text(po(entry("Save", "Enregistrer")), encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "translate by hand")

    assert tool.cmd_guard(base, "HEAD", staged=False) == 1
    assert "translation was changed here" in capsys.readouterr().out


def test_guard_lets_weblate_through(tool, repo):
    root, catalogue = repo
    base = git(root, "rev-parse", "HEAD").strip()
    catalogue.write_text(po(entry("Save", "Enregistrer")), encoding="utf-8", newline="\n")
    git(root, "commit", "-q", "-am", "Translated using Weblate", email="weblate@tiagoagueda.com")

    assert tool.cmd_guard(base, "HEAD", staged=False) == 0


def test_guard_reads_what_is_staged(tool, repo):
    root, catalogue = repo
    catalogue.write_text(po(entry("Save", "Enregistrer")), encoding="utf-8", newline="\n")
    git(root, "add", "-A")

    assert tool.cmd_guard(None, "HEAD", staged=True) == 1


# ----------------------------------------------------------------------- the gate


def test_the_gate_counts_a_draft_and_not_an_empty_slot(tool, repo, capsys):
    root, catalogue = repo
    tool.use(root)
    catalogue.write_text(
        po(entry("Save", "Enregistrer", fuzzy=True)), encoding="utf-8", newline="\n"
    )
    assert tool.cmd_gate(["fr-FR"]) == 0
    catalogue.write_text(po(entry("Save")), encoding="utf-8", newline="\n")
    assert tool.cmd_gate(["fr-FR"]) == 1
    assert "1 missing" in capsys.readouterr().out
