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


def test_the_dependency_workflow_keeps_its_token_away_from_the_new_packages():
    """#975: a step boundary is not a privilege boundary.

    `uv lock --upgrade` builds source distributions, `uv sync` installs the newest release
    of every locked package and `pytest` loads whatever they register, all as root. Anything
    that code leaves in the container (a git hook, a binary earlier on the PATH) would run in
    a later step with the bot's token in its environment. So the job holding the token is a
    different job, in a fresh container, which never runs `uv` or `pytest`, and every git
    command it runs has hooks switched off on the command line, where a planted
    `~/.gitconfig` cannot override it.
    """
    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".forgejo" / "workflows" / "dependencies.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    holders = [name for name, job in jobs.items() if "DEPENDENCIES_TOKEN" in yaml.safe_dump(job)]
    assert holders, "no job holds the token any more: the workflow does nothing"

    for name in holders:
        job = jobs[name]
        assert job.get("needs"), "the job with the token must come after the one that resolves"
        for step in job["steps"]:
            script = step.get("run", "")
            assert not re.search(r"(?<![\w./-])uv(?![\w./-])|pytest|pip |npm ", script), (
                f"{name}: {step.get('name')!r} runs code from the new packages beside the token"
            )
            for command in re.findall(r"^\s*git\b.*$", script, re.MULTILINE):
                assert "core.hooksPath=" in command, f"{name}: git without hooks off: {command}"
        assert any("git" in step.get("run", "") for step in job["steps"])
        assert "status --porcelain" in yaml.safe_dump(job), "the job must check what it commits"

    for name, job in jobs.items():
        if name not in holders:
            assert "secrets." not in yaml.safe_dump(job), f"{name} resolves and tests: no secret"
