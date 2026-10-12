"""A pull request's cache is its own: no prefix fallback to what main restores (#827)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted(
    (Path(__file__).resolve().parent.parent / ".forgejo" / "workflows").glob("*.yml")
)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_a_prefix_fallback_is_never_used_on_a_pull_request(path):
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^\s*pull_request\s*:", text, re.MULTILINE):
        pytest.skip("this workflow does not run on pull requests")
    for line in text.splitlines():
        if re.match(r"\s*restore-keys:", line):
            assert "github.event_name != 'pull_request'" in line, (
                f"{path.name}: {line.strip()} lets a pull request restore a cache by prefix"
            )
