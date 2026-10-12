"""The page renderer starts with an allow-listed environment and an empty directory (#814)."""

from __future__ import annotations

import subprocess

import pytest

from postulo.core import childenv
from postulo.jobs import rendering

pytestmark = pytest.mark.django_db


def test_the_secrets_in_this_process_are_not_in_the_child_environment(monkeypatch):
    monkeypatch.setenv("POSTULO_SECRET_KEY", "the-secret-key")
    monkeypatch.setenv("POSTULO_DATABASE_URL", "postgres://user:pw@db/postulo")
    monkeypatch.setenv("POSTULO_FIELD_KEY", "field")
    monkeypatch.setenv("PATH", "/usr/bin")
    environment = childenv.child_environment()
    assert environment["PATH"] == "/usr/bin"
    assert not {k for k in environment if k.startswith("POSTULO_")}
    assert "the-secret-key" not in " ".join(environment.values())


def test_what_the_child_is_started_with_is_that_environment_in_an_empty_directory(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        import os

        seen["cwd_was_empty"] = os.listdir(kwargs["cwd"]) == []
        return subprocess.CompletedProcess(command, rendering.rendering_child.DREW, b"%PDF-1", b"")

    monkeypatch.setenv("POSTULO_SECRET_KEY", "the-secret-key")
    monkeypatch.setattr(subprocess, "run", fake_run)
    chosen = rendering.Renderer(name="weasyprint", command=("python", "-m", "x"))
    assert rendering.draw("<p>hi</p>", using=chosen) == b"%PDF-1"
    assert "POSTULO_SECRET_KEY" not in seen["env"]
    assert seen["cwd_was_empty"]


@pytest.mark.parametrize("name", ["PATH"])
def test_what_a_program_needs_to_find_its_libraries_is_kept(name):
    assert name in childenv.ENVIRONMENT_KEPT
