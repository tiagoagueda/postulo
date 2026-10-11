"""Five Low findings of the security audit of 2026-10-10 (#771, #773, #774, #775, #787)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from django.utils import timezone

from postulo.documents import scrub

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------- #775


def _files(root: Path, count: int, *, old: bool = True) -> list[Path]:
    folder = root / "documents" / "1"
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for index in range(count):
        path = folder / f"stray{index}.pdf"
        path.write_bytes(b"%PDF-1.7")
        if old:
            then = (timezone.now() - dt.timedelta(hours=72)).timestamp()
            os.utime(path, (then, then))
        made.append(path)
    return made


def test_a_pass_that_would_delete_most_of_what_is_kept_deletes_nothing(tmp_path, settings, caplog):
    settings.MEDIA_ROOT = str(tmp_path)
    made = _files(tmp_path, 12)

    removed = scrub.sweep()

    assert removed == [] and all(path.exists() for path in made)
    assert "removed none" in caplog.text


def test_a_handful_of_orphans_is_still_swept(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    made = _files(tmp_path, 3)

    assert len(scrub.sweep()) == 3 and not any(path.exists() for path in made)
