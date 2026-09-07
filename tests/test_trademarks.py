"""The trademark notice, held to the artwork actually in the tree.

A file like this goes stale the moment somebody vendors something and does not think about
it, and the failure is silent: the repository ships a mark it does not own with nothing
beside it saying whose it is. That is what happened to `assets/support/buy-me-a-coffee.png`
between #79 and #107.

So these check the two things that can drift. Every directory of third-party artwork is
named in the document, and every directory that holds a mark rather than merely licensed
artwork carries a notice beside the files -- the arrangement the flags already use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TRADEMARKS = REPO / "TRADEMARKS.md"
TEXT = TRADEMARKS.read_text(encoding="utf-8")

#: The same, with every run of whitespace flattened, so an assertion about a sentence is
#: not really an assertion about where the paragraph happened to wrap.
FLAT = " ".join(TEXT.split())

#: Directories holding artwork Postulo did not draw, and the notice each carries. `None`
#: means the licence travels inside the files themselves -- Lucide keeps an `@license`
#: comment in every icon -- so there is nothing separate to check for.
THIRD_PARTY = {
    "src/postulo/static/icons": None,
    "src/postulo/static/flags": "LICENSE.txt",
    "assets/support": "NOTICE.txt",
}


def test_the_document_is_there_at_all():
    assert TRADEMARKS.is_file()


@pytest.mark.parametrize("directory", sorted(THIRD_PARTY))
def test_every_directory_of_borrowed_artwork_is_named(directory: str):
    """Vendoring something new without a line here is the failure this catches."""
    assert (REPO / directory).is_dir(), f"{directory} has moved; update {TRADEMARKS.name}"
    assert directory.rsplit("/", 1)[-1] in TEXT, (
        f"{directory} holds artwork from somewhere else and {TRADEMARKS.name} does not mention it"
    )


@pytest.mark.parametrize(
    "directory,notice",
    sorted((d, n) for d, n in THIRD_PARTY.items() if n),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_notice_travels_with_the_files(directory: str, notice: str):
    """A notice in the document alone does not survive somebody copying the directory."""
    path = REPO / directory / notice
    assert path.is_file(), f"{directory} has no {notice} beside its files"
    assert path.read_text(encoding="utf-8").strip(), f"{path} is empty"


def test_the_licence_and_the_marks_are_kept_apart():
    """The distinction the document exists to make: a licence is not a mark.

    Lucide and flag-icons grant a copyright licence and are satisfied by carrying it. A
    trademark grants nothing and needs a different kind of note. A document that blurred
    the two would be worse than none.
    """
    assert "AGPL-3.0-or-later" in TEXT
    assert "not licensed at all" in FLAT, "the distinction has gone out of the document"
    assert "All trademarks are the property of their respective owners." in FLAT


def test_the_readme_points_at_it():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "TRADEMARKS.md" in readme, "nobody will find it"


def test_the_ecosystem_convention_is_explicitly_allowed():
    """Eight repositories are already called `postulo-something`.

    A trademark note that did not permit the project's own naming convention would put
    every existing plugin in the wrong, which is the classic way these documents cause
    the harm they were written to prevent.
    """
    assert "postulo-something" in TEXT
