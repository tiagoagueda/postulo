"""A hard-linked file is in the backup, once for each name (#809)."""

from __future__ import annotations

import io
import os
import tarfile

import pytest

from postulo.core import backup


def test_both_names_of_a_hard_linked_file_are_archived_with_their_bytes(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "first.bin").write_bytes(b"the same bytes")
    try:
        os.link(root / "first.bin", root / "second.bin")
    except OSError:
        pytest.skip("this file system has no hard links")

    out = io.BytesIO()
    digests: dict[str, str] = {}
    with tarfile.open(fileobj=out, mode="w") as archive:
        backup._add_tree(archive, root, "media", digests)

    out.seek(0)
    with tarfile.open(fileobj=out) as opened:
        members = {m.name: m for m in opened.getmembers() if m.name != "media"}
        assert set(members) == {"media/first.bin", "media/second.bin"}
        assert all(m.isfile() for m in members.values()), (
            "plain files only, so a restore takes them"
        )
        for name in members:
            assert opened.extractfile(members[name]).read() == b"the same bytes"
    assert set(digests) == {"media/first.bin", "media/second.bin"}
