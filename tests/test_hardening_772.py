"""A backup is signed, and an archive nobody can vouch for does not put code back (#772)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from postulo.accounts.models import User
from postulo.core import backup as backup_module
from postulo.core.backup import (
    AUTHENTIC,
    UNSIGNED,
    UNVERIFIED,
    BackupError,
    restore_backup,
    write_backup,
)

from .test_backup import (  # noqa: F401  (the fixtures that give a test its own media and plugins)
    _nothing_else_is_connected,
    _own_media_root,
    _own_plugins_dir,
    a_media_file,
    a_search,
    an_installed_plugin,
    manifest_of,
    members_of,
)

pytestmark = pytest.mark.django_db(transaction=True)


def altered(source: Path, target: Path, name: str, content: bytes) -> Path:
    """``source`` with one member's bytes replaced, as someone who can write to the copy would."""
    with tarfile.open(source, "r:gz") as old, tarfile.open(target, "w:gz") as new:
        for member in old.getmembers():
            handle = old.extractfile(member)
            if member.name == name:
                member.size = len(content)
                new.addfile(member, io.BytesIO(content))
            elif handle is None:
                new.addfile(member)
            else:
                new.addfile(member, handle)
    return target


def an_archive(tmp_path, settings):
    a_search()
    a_media_file(settings)
    an_installed_plugin(settings)
    return write_backup(tmp_path / "instance.tar.gz").path


def test_an_archive_carries_a_signed_manifest_and_a_digest_of_every_file(tmp_path, settings):
    archive = an_archive(tmp_path, settings)

    assert manifest_of(archive)["authentication"]["algorithm"] == "hmac-sha256"
    names = members_of(archive)
    assert names[0] == "manifest.json" and names[-1] == backup_module.INTEGRITY
    with tarfile.open(archive, "r:gz") as opened:
        trailer = json.loads(opened.extractfile(backup_module.INTEGRITY).read())
        assert authenticity_of(opened) == AUTHENTIC
    assert set(trailer["members"]) == {
        "media/documents/cv.pdf",
        "plugins/plugins.json",
        "plugins/postulo_imap/__init__.py",
    }


def empty_the_data_directories(settings) -> None:
    """A fresh instance: nothing under media/ or plugins/ to be kept instead of restored."""
    import shutil

    for root in (settings.MEDIA_ROOT, settings.POSTULO_PLUGINS_DIR):
        for child in Path(root).iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()


def authenticity_of(opened) -> str:
    return backup_module.authenticity(opened, backup_module.read_manifest(opened))[0]


def test_an_authentic_archive_restores_with_its_plugins(tmp_path, settings):
    archive = an_archive(tmp_path, settings)
    User.objects.all().delete()
    empty_the_data_directories(settings)

    report = restore_backup(archive)

    assert report.authenticity == AUTHENTIC
    assert report.plugin_files == 2


@pytest.mark.parametrize("member", ["plugins/postulo_imap/__init__.py", "media/documents/cv.pdf"])
def test_a_file_changed_after_the_archive_was_written_refuses_the_restore(
    tmp_path, settings, member
):
    archive = an_archive(tmp_path, settings)
    bad = altered(archive, tmp_path / "bad.tar.gz", member, b"import os  # planted\n")
    User.objects.all().delete()
    plugin = Path(settings.POSTULO_PLUGINS_DIR) / "postulo_imap" / "__init__.py"
    plugin.write_text("# what was here\n")

    with pytest.raises(BackupError, match="not the file this archive was signed with"):
        restore_backup(bad)

    assert plugin.read_text() == "# what was here\n", "nothing was written"
    assert not User.objects.exists(), "nor was the database loaded"


def test_a_changed_manifest_is_refused_unless_the_operator_says_otherwise(tmp_path, settings):
    archive = an_archive(tmp_path, settings)
    manifest = manifest_of(archive)
    manifest["counts"]["users"] = 999
    bad = altered(archive, tmp_path / "bad.tar.gz", "manifest.json", json.dumps(manifest).encode())

    with pytest.raises(BackupError, match="not signed by this instance"):
        restore_backup(bad, force=True)

    report = restore_backup(bad, force=True, unverified=True)
    assert report.authenticity == UNVERIFIED and report.plugin_files == 2


def test_an_archive_written_under_another_key_is_refused_without_the_flag(tmp_path, settings):
    archive = an_archive(tmp_path, settings)
    User.objects.all().delete()
    settings.POSTULO_FIELD_KEY = "a-different-key-for-another-instance"

    with pytest.raises(BackupError, match="another key"):
        restore_backup(archive)


def test_an_archive_from_before_signing_needs_the_flag_and_then_brings_its_plugins(
    tmp_path, settings
):
    """It cannot be told from one whose signature was removed, so it is asked for (#974)."""
    archive = an_archive(tmp_path, settings)

    def unsigned(manifest):
        del manifest["authentication"]
        return manifest

    from .test_backup import rewritten_manifest

    old = rewritten_manifest(archive, tmp_path / "old.tar.gz", unsigned)
    User.objects.all().delete()
    plugin = Path(settings.POSTULO_PLUGINS_DIR) / "postulo_imap" / "__init__.py"
    empty_the_data_directories(settings)

    with pytest.raises(BackupError, match="no signature"):
        restore_backup(old)
    assert not plugin.exists() and not User.objects.exists()

    report = restore_backup(old, unverified=True)

    assert report.authenticity == UNSIGNED
    assert report.plugin_files == 2 and report.media_files == 1
