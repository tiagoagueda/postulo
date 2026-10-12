"""The child that imports a new plugin gets an allow-listed environment (#820)."""

from __future__ import annotations

import subprocess

from postulo.plugins import installing


def test_the_import_check_is_not_handed_the_secrets(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("POSTULO_SECRET_KEY", "the-secret-key")
    monkeypatch.setenv("POSTULO_DATABASE_URL", "postgres://u:pw@db/x")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert installing.verify_imports(["postulo.features:thing"]) == []
    assert "POSTULO_SECRET_KEY" not in seen["env"]
    assert "POSTULO_DATABASE_URL" not in seen["env"]
