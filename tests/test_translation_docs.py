"""The documents that tell people how to translate say what the code enforces (#706).

Translations are made in Weblate and nowhere else, and `scripts/messages.py guard` refuses a
commit that writes one. The translator's handbook, the contributor guide and the
assistant's instructions all used to say the opposite -- edit the `.po` file and open a pull
request, write the draft in the commit -- and a document that contradicts the check is
followed until the check fails. These hold them to the rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ["docs/TRANSLATING.md", "CONTRIBUTING.md", "CLAUDE.md"]

#: What each of them said before, and must not say again.
CONTRADICTIONS = [
    "Edit `src/postulo/locale/",
    "A catalogue is a pull request",
    "Translate, `check`, and open a pull request",
    "translate the new strings as drafts",
    "carries the `draft` flag until a speaker reviews it",
    "deleting the flag",
    "French and Portuguese\ncatalogues exist and are waiting for contributors",
    "Ordinary work translates English, French and European Portuguese",
]


@pytest.mark.parametrize("name", DOCUMENTS)
def test_no_document_tells_anybody_to_translate_in_a_file(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    said = [phrase for phrase in CONTRADICTIONS if phrase in text]
    assert not said, f"{name} still says {said}: translations are made in Weblate (#706)"


@pytest.mark.parametrize("name", DOCUMENTS)
def test_each_one_names_weblate(name):
    assert "Weblate" in (ROOT / name).read_text(encoding="utf-8")


def test_the_handbook_says_where_and_what_a_commit_may_do():
    text = (ROOT / "docs/TRANSLATING.md").read_text(encoding="utf-8")
    assert "translate.tiagoagueda.com" in text
    assert "What a commit does with a string" in text
    assert "Renaming strings in bulk" in text


def test_the_assistant_is_told_never_to_write_one():
    assert "Never write a translation" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
