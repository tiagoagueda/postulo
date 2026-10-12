"""The collected static files are readable by the user the image runs as.

The image runs collectstatic as root and serves as another user. Owner-only modes for uploads
(#789) once leaked into the static storage too, and the container could not read /app/staticfiles.
Read from the source, because the test settings replace the storage after importing it.
"""

from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "src/postulo/config/settings/base.py"


def test_the_static_storage_names_public_modes_whatever_uploads_are():
    text = BASE.read_text(encoding="utf-8")
    assert '"file_permissions_mode": 0o644' in text
    assert '"directory_permissions_mode": 0o755' in text
    assert "FILE_UPLOAD_PERMISSIONS = 0o600" in text, "uploads stay owner-only"
