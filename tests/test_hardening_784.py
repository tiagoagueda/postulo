"""A plugin switched off, or its .pth file, cannot run code at every start (#784)."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

from postulo.plugins import installing, record

from .test_plugin_install import a_wheel


def with_pth(wheel: Path, name="evil.pth") -> Path:
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(name, "import os; os.environ['POSTULO_PTH_RAN'] = '1'\n")
    return wheel


def test_a_wheel_that_ships_a_pth_file_is_refused_when_it_is_read(tmp_path):
    plain = a_wheel(tmp_path)
    assert installing.read_wheel(plain).has_pth is False
    installing.check(installing.read_wheel(plain))

    info = installing.read_wheel(with_pth(plain))

    assert info.has_pth is True
    with pytest.raises(installing.InstallError, match=r"\.pth"):
        installing.check(info)


def test_activating_the_plugins_directory_runs_no_pth_file(tmp_path, settings, monkeypatch):
    settings.POSTULO_PLUGINS_DIR = str(tmp_path)
    (tmp_path / "evil.pth").write_text("import os; os.environ['POSTULO_PTH_RAN'] = '1'\n")
    monkeypatch.delenv("POSTULO_PTH_RAN", raising=False)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != str(tmp_path)])

    record.activate()

    assert "POSTULO_PTH_RAN" not in os.environ, "a .pth is not processed, off or on"
    assert str(tmp_path) in sys.path, "but the directory is on the import path"
