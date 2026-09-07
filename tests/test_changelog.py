"""The changelog's shape, so a convention stays one rather than becoming a habit.

The entries themselves are long on purpose: each says *why* a thing changed rather than
what changed, which is the house style and is not something a test can check. What a test
can check is that the file stays scannable — every section is one of the six kinds Keep a
Changelog names, each carries its mark, and every release the code claims to have made has
somewhere to have been written down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "CHANGELOG.md"

#: The six kinds of change, and the mark each carries. The word stays beside the mark:
#: the word is what reads in a terminal, in a screen reader, and in a font without the
#: glyph, and the mark is what makes the kind findable at a glance.
MARKS = {
    "Added": "✨",
    "Changed": "🔧",
    "Deprecated": "⚠️",
    "Removed": "🗑️",
    "Fixed": "🐛",
    "Security": "🔒",
}

TEXT = CHANGELOG.read_text(encoding="utf-8")
SECTIONS = [line for line in TEXT.split("\n") if line.startswith("### ")]


def test_the_changelog_has_sections_at_all():
    assert SECTIONS, "no ### sections; has the format changed?"


@pytest.mark.parametrize("heading", SECTIONS, ids=lambda h: h)
def test_every_section_is_a_kind_of_change_and_carries_its_mark(heading: str):
    """A heading nobody recognises, or one without its mark, fails here.

    Without this the convention decays into some sections having a mark and some not,
    which reads as a mistake rather than as a convention — worse than never having done it.
    """
    body = heading[len("### ") :].strip()
    expected = {f"{mark} {word}": word for word, mark in MARKS.items()}
    assert body in expected, (
        f"{heading!r} is not one of the six kinds of change. Use one of: "
        + ", ".join(f"### {mark} {word}" for word, mark in MARKS.items())
    )


def test_no_section_carries_the_wrong_mark():
    """Catches a mark copied from the section above, which the check above cannot."""
    wrong = []
    for heading in SECTIONS:
        body = heading[len("### ") :].strip()
        mark, _, word = body.partition(" ")
        if word in MARKS and MARKS[word] != mark:
            wrong.append(f"{heading!r} should be {MARKS[word]}")
    assert not wrong, wrong


def test_unreleased_is_the_first_version_section():
    """Where the next entry goes. A changelog whose top section is a release is one that
    somebody has written into the wrong place."""
    versions = [line for line in TEXT.split("\n") if line.startswith("## ")]
    assert versions, "no version sections"
    assert versions[0] == "## [Unreleased]", versions[0]


def test_every_version_section_is_a_version_and_a_date():
    for heading in [line for line in TEXT.split("\n") if line.startswith("## ")]:
        if heading == "## [Unreleased]":
            continue
        assert re.fullmatch(r"## \[\d+\.\d+\.\d+\] — \d{4}-\d{2}-\d{2}", heading), heading


def test_the_version_the_code_claims_has_a_section():
    """A release with no entry is a release nobody can read about."""
    import postulo

    version = postulo.__version__
    assert f"## [{version}]" in TEXT, (
        f"the code says {version} and the changelog has no section for it"
    )


def test_the_release_notes_tool_still_finds_a_section():
    """The marks live inside the version sections the release tooling slices out, so this
    proves they travel into the release notes rather than breaking the slice."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "release_tools", REPO / "scripts" / "release_tools.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_tools"] = module
    spec.loader.exec_module(module)

    import postulo

    notes = module.changelog_section(postulo.__version__, REPO)
    assert notes.strip(), "no notes for the current version"
    assert any(mark in notes for mark in MARKS.values()), "the marks did not survive"
