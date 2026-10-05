"""The tool versions the lock names and the hooks run are the same (#585)."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_pre_commit_ruff_is_the_locked_ruff():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == "ruff")
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    match = re.search(r"ruff-pre-commit\s*\n\s*rev:\s*v(\S+)", hooks)
    assert match, "ruff-pre-commit is not pinned in .pre-commit-config.yaml"
    assert match.group(1) == locked, (
        f"uv.lock has ruff {locked} but .pre-commit-config.yaml pins v{match.group(1)}: "
        "contributors' hooks would format differently from CI"
    )
