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

THIRD_PARTY_MD = REPO / "THIRD-PARTY.md"
REGISTER = THIRD_PARTY_MD.read_text(encoding="utf-8")

#: Directories holding work Postulo did not write or draw, and the notice each carries.
#: `None` means the licence travels inside the files themselves -- Lucide keeps an
#: `@license` comment in every icon -- so there is nothing separate to check for. Since
#: #279 the code is here too: zxcvbn and htmx are somebody else's source served to every
#: page, and Basecoat is compiled into the stylesheet, and none of the three carried a
#: notice anywhere in the tree.
THIRD_PARTY = {
    "src/postulo/static/icons": None,
    "src/postulo/static/flags": "LICENSE.txt",
    "assets/support": "NOTICE.txt",
    "src/postulo/static/js/vendor/zxcvbn": "LICENSE.txt",
    "src/postulo/static/js/vendor": "htmx.LICENSE.txt",
    "src/postulo/static/css": "basecoat.LICENSE.txt",
}


def test_the_document_is_there_at_all():
    assert TRADEMARKS.is_file()


@pytest.mark.parametrize("directory", sorted(THIRD_PARTY))
def test_every_directory_of_borrowed_work_is_registered(directory: str):
    """Vendoring something new without a line in the register is the failure this catches."""
    assert (REPO / directory).is_dir(), f"{directory} has moved; update {THIRD_PARTY_MD.name}"
    assert directory.rsplit("/", 1)[-1] in REGISTER, (
        f"{directory} holds work from somewhere else and {THIRD_PARTY_MD.name} does not mention it"
    )


def test_the_register_names_every_work_and_the_copyright_holder():
    for work in ("Lucide", "flag-icons", "Tailwind CSS", "basecoat-css", "htmx", "zxcvbn"):
        assert work in REGISTER, f"{work} is shipped and not registered"
    assert "Copyright (C) 2026 Tiago Agueda" in REGISTER
    assert "Copyright (C) 2026 Tiago Agueda" in TEXT
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Copyright (C) 2026 Tiago Agueda" in readme and "THIRD-PARTY.md" in readme


def test_nothing_whose_code_is_in_the_tree_is_described_as_merely_a_name():
    """htmx is 52 KB of somebody else's source served to every page, not a name Postulo
    mentions; the trademarks file listed it, and Tailwind, as names only (#279)."""
    prose = FLAT[FLAT.index("Postulo talks about the software") :]
    sentence = prose[: prose.index("and others.")]
    for work in ("htmx", "Tailwind CSS", "zxcvbn"):
        assert work not in sentence, f"{work} is listed as a name Postulo merely mentions"


def test_the_stylesheet_carries_both_banners():
    """Tailwind writes its own; Basecoat's is in the source and has to survive the build."""
    compiled = (REPO / "src/postulo/static/css/app.css").read_text(encoding="utf-8")
    head = compiled[:2000]
    assert "/*! tailwindcss" in head and "MIT License" in head
    assert "/*! basecoat-css v1.0.2 | MIT License | Copyright (c) 2025 Ronan Berder" in head


def test_the_notices_are_the_real_ones():
    zxcvbn = (REPO / "src/postulo/static/js/vendor/zxcvbn/LICENSE.txt").read_text(encoding="utf-8")
    assert "Dan Wheeler and Dropbox" in zxcvbn and "@zxcvbn-ts" in zxcvbn
    basecoat = (REPO / "src/postulo/static/css/basecoat.LICENSE.txt").read_text(encoding="utf-8")
    assert "Ronan Berder" in basecoat and "MIT" in basecoat
    htmx = (REPO / "src/postulo/static/js/vendor/htmx.LICENSE.txt").read_text(encoding="utf-8")
    assert "Zero-Clause BSD" in htmx or "0BSD" in htmx or "Permission to use" in htmx


def test_every_vendored_script_has_a_pinned_provenance():
    """htmx arrived in the first commit from nowhere, and only a string inside the minified
    bundle said which version it was; `sync:vendor` could not update it and nothing showed
    when it fell behind (#279). Every vendored script is now in package.json at an exact
    version and in the sync script's list, so a bump is a diff."""
    import json
    import re

    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    assert package["license"] == "AGPL-3.0-or-later"
    pinned = package["devDependencies"]
    sync = (REPO / "scripts/sync-vendor.mjs").read_text(encoding="utf-8")
    for name in ("htmx.org", "@zxcvbn-ts/core"):
        assert name in pinned, f"{name} is vendored and not a dependency"
        assert f'["{name}/' in sync, f"{name} is not in the sync script's list"
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned["htmx.org"]), "exact, not a range"
    bundle = (REPO / "src/postulo/static/js/vendor/htmx.min.js").read_text(encoding="utf-8")
    assert f'version:"{pinned["htmx.org"]}"' in bundle, "the served bundle is the pinned version"


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
