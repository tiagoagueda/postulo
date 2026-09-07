"""What a plugin says about itself, read correctly and then kept.

Two failures, one on each side of the install.

The wheel was read for a summary, a licence, an author and a home page; the confirmation
screen showed all four; and the record kept none of them. So an administrator could see who
wrote a plugin on the day they installed it and never again.

And two of those four were read from headers modern packaging does not emit. `Home-page` is
setuptools' old `url=`; anything using `[project.urls]` emits `Project-URL` instead — which
includes this project's own reference plugin, built with hatchling, whose declared homepage
Postulo therefore never saw. `Author` is a bare name while `Author-email` carries
``First Last <address>``, and the order tried the barer one first.
"""

from __future__ import annotations

import json
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest

from postulo.plugins import base, installing


def wheel(tmp_path: Path, metadata: str, name: str = "postulo_thing-1.0-py3-none-any.whl") -> Path:
    """A wheel that is nothing but its metadata, which is all `read_wheel` looks at."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("postulo_thing-1.0.dist-info/METADATA", metadata)
        archive.writestr("postulo_thing-1.0.dist-info/WHEEL", "Tag: py3-none-any\n")
        archive.writestr(
            "postulo_thing-1.0.dist-info/entry_points.txt",
            "[postulo.sources]\nthing = postulo_thing:Thing\n",
        )
    return path


BARE = "Metadata-Version: 2.4\nName: postulo-thing\nVersion: 1.0\n"


# --------------------------------------------------------- reading the metadata


def test_a_modern_wheel_yields_a_source_link(tmp_path):
    """The failure this fixes. `postulo-helloworld` is built with hatchling and declares

        [project.urls]
        Homepage = "https://source.tiagoagueda.com/postulo/postulo-helloworld"

    which becomes `Project-URL` and never `Home-page`, so Postulo showed no source link for
    the plugin it publishes as the example to copy.
    """
    info = installing.read_wheel(
        wheel(tmp_path, BARE + "Project-URL: Homepage, https://example.org/helloworld\n")
    )
    assert info.source_url == "https://example.org/helloworld"


def test_the_source_is_preferred_over_the_home_page(tmp_path):
    """A project offering several: the one that names the code wins."""
    info = installing.read_wheel(
        wheel(
            tmp_path,
            BARE
            + "Project-URL: Homepage, https://example.org/marketing\n"
            + "Project-URL: Changelog, https://example.org/news\n"
            + "Project-URL: Source, https://example.org/code\n",
        )
    )
    assert info.source_url == "https://example.org/code"


def test_an_unrecognised_label_still_beats_nothing(tmp_path):
    info = installing.read_wheel(
        wheel(tmp_path, BARE + "Project-URL: Where it lives, https://example.org/somewhere\n")
    )
    assert info.source_url == "https://example.org/somewhere"


def test_the_old_header_still_works(tmp_path):
    """setuptools-era wheels are not going away, and nothing here may stop reading them."""
    info = installing.read_wheel(wheel(tmp_path, BARE + "Home-page: https://example.org/old\n"))
    assert info.source_url == "https://example.org/old"


def test_the_author_with_an_address_is_preferred(tmp_path):
    """`Author-email` is `First Last <address>`, which is the form asked for. Trying
    `Author` first picked the less informative of the two whenever both were set."""
    info = installing.read_wheel(
        wheel(
            tmp_path,
            BARE + "Author: A Person\nAuthor-email: A Person <a.person@example.org>\n",
        )
    )
    assert info.author == "A Person <a.person@example.org>"


def test_a_bare_author_is_still_read(tmp_path):
    info = installing.read_wheel(wheel(tmp_path, BARE + "Author: A Person\n"))
    assert info.author == "A Person"


def test_nothing_declared_is_not_an_error(tmp_path):
    info = installing.read_wheel(wheel(tmp_path, BARE))
    assert info.author == "" and info.source_url == "" and info.summary == ""


# ------------------------------------------------------------ keeping it


def test_the_record_keeps_what_the_wheel_said(tmp_path, settings):
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    entry = installing.Installed(
        name="postulo-thing",
        version="1.0",
        summary="Does a thing.",
        licence="MIT",
        author="A Person <a.person@example.org>",
        source_url="https://example.org/code",
    )
    installing.write_record([entry])

    kept = installing.read_record()[0]
    assert kept.summary == "Does a thing."
    assert kept.author == "A Person <a.person@example.org>"
    assert kept.source_url == "https://example.org/code"
    assert kept.licence == "MIT"


def test_a_record_written_before_this_still_reads(tmp_path, settings):
    """`read_record` takes only the keys an entry has, so an older instance's file is not
    a migration — it just has blanks until the backfill runs."""
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / installing.RECORD_NAME).write_text(
        json.dumps({"version": 1, "plugins": [{"name": "postulo-old", "version": "0.9"}]}),
        encoding="utf-8",
    )

    kept = installing.read_record()[0]
    assert kept.name == "postulo-old"
    assert kept.author == "" and kept.source_url == ""


def test_the_backfill_completes_an_older_record(tmp_path, settings):
    """The wheel is long gone; the dist-info the installer wrote is not."""
    directory = tmp_path / "plugins"
    settings.POSTULO_PLUGINS_DIR = directory
    dist_info = directory / "postulo_old-0.9.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: postulo-old\nVersion: 0.9\n"
        "Summary: An older plugin.\nLicense-Expression: AGPL-3.0-or-later\n"
        "Author-email: A Person <a.person@example.org>\n"
        "Project-URL: Source, https://example.org/old\n",
        encoding="utf-8",
    )
    installing.write_record([installing.Installed(name="postulo-old", version="0.9")])

    assert installing.backfill_metadata() == ["postulo-old"]

    kept = installing.read_record()[0]
    assert kept.summary == "An older plugin."
    assert kept.author == "A Person <a.person@example.org>"
    assert kept.source_url == "https://example.org/old"
    assert kept.licence == "AGPL-3.0-or-later"


def test_the_backfill_leaves_alone_what_it_cannot_find(tmp_path, settings):
    """A missing author is a smaller problem than a wrong one."""
    directory = tmp_path / "plugins"
    settings.POSTULO_PLUGINS_DIR = directory
    directory.mkdir(parents=True)
    installing.write_record([installing.Installed(name="postulo-vanished", version="0.9")])

    assert installing.backfill_metadata() == []
    assert installing.read_record()[0].author == ""


def test_the_backfill_does_not_overwrite_what_is_already_there(tmp_path, settings):
    directory = tmp_path / "plugins"
    settings.POSTULO_PLUGINS_DIR = directory
    dist_info = directory / "postulo_known-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(BARE + "Summary: From the disk.\n", encoding="utf-8")
    installing.write_record(
        [installing.Installed(name="postulo-known", version="1.0", summary="From the record.")]
    )

    assert installing.backfill_metadata() == []
    assert installing.read_record()[0].summary == "From the record."


# ------------------------------------------- what a plugin object may say about itself


class Quiet:
    """Everything a source has been required to have since the beginning, and no more."""

    name = "quiet"
    version = "1.0"

    def can_handle(self, url: str) -> bool:
        return False

    def parse(self, url: str, html: str):
        return None


class Talkative(Quiet):
    name = "talkative"
    label = "Something Readable"
    description = "What it does, in a sentence."


def test_a_plugin_that_says_nothing_gets_its_own_name_back():
    assert base.label_of(Quiet()) == "quiet"
    assert base.description_of(Quiet()) == ""


def test_a_plugin_that_says_something_is_believed():
    assert base.label_of(Talkative()) == "Something Readable"
    assert base.description_of(Talkative()) == "What it does, in a sentence."


def test_the_new_fields_are_not_in_the_protocol():
    """The reason they are read through helpers rather than declared.

    `runtime_checkable` protocols check data members, and `registry._load_third_party`
    drops anything failing `isinstance`. Putting `label` on `SourcePlugin` would not be a
    request that plugins declare one — it would silently unload every source already
    written, including the ones in this project's own plugin repositories.
    """
    assert isinstance(Quiet(), base.SourcePlugin), "a source without a label must still load"
    assert "label" not in getattr(base.SourcePlugin, "__annotations__", {})
    assert "description" not in getattr(base.SourcePlugin, "__annotations__", {})


@pytest.mark.parametrize("kind", ["source", "notifier", "store", "importer"])
def test_every_built_in_still_loads(kind, db):
    """The regression this whole shape exists to prevent."""
    from postulo.plugins import registry

    assert registry.plugins(kind), f"nothing loads for {kind}"


def test_the_metadata_headers_are_read_the_way_packaging_writes_them():
    """A guard on the helpers themselves, without building a wheel for it."""
    headers = Parser().parsestr(
        "Author-email: A Person <a@example.org>\nProject-URL: Source, https://example.org/x\n"
    )
    assert installing._author(headers) == "A Person <a@example.org>"
    assert installing._source_url(headers) == "https://example.org/x"
