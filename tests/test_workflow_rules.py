"""Rules the workflows are held to, each learned from a run that went wrong on this runner.

Nothing here starts a workflow: the files are read as the YAML they are. What a rule protects
is in its test; the workflows' own comments say where each was found.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".forgejo" / "workflows"


def workflows() -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }


def jobs():
    for name, workflow in workflows().items():
        for key, job in workflow["jobs"].items():
            yield name, key, job


# -------------------------------------------------------- a job is never skipped by `if` (#1159)

#: The one job-level `if` there is, and why it may stay: it is only false on a branch the
#: workflow's own trigger already leaves out, so it never has to skip anything.
JOB_LEVEL_IFS = {("dev-image.yml", "image"): "github.ref_name == 'main'"}


def test_no_job_is_skipped_with_a_job_level_if():
    """This runner fails a job whose `if` is false instead of skipping it, so a condition
    goes on each step. The monthly dependency run's second job had one, and a month in which
    the lock did not move would have ended red (#1159)."""
    found = {(name, key): job["if"] for name, key, job in jobs() if "if" in job}

    assert found == JOB_LEVEL_IFS


def test_the_monthly_proposal_does_nothing_when_nothing_moved():
    propose = workflows()["dependencies.yml"]["jobs"]["propose"]
    moved = "needs.upgrade.outputs.changed == 'true'"

    conditions = [step.get("if") for step in propose["steps"]]

    assert conditions[0] == "needs.upgrade.outputs.changed != 'true'", "the step that says so"
    assert conditions[1:] == [moved] * (len(conditions) - 1), "every other step is guarded"


# ------------------------------------------- `uv run` uses what the sync step built (#1135)


def sync_commands(job) -> list[str]:
    return [step["run"] for step in job["steps"] if "uv sync" in str(step.get("run", ""))]


def test_a_job_that_leaves_the_browser_group_out_keeps_it_out():
    """`uv run` brings the environment back to the default groups before it runs anything,
    and the browser group is one of them: a job that synced without it downloaded Playwright
    on its first `uv run` all the same, 45 MB a run, under a comment that said it did not."""
    narrow = [
        (name, key, job)
        for name, key, job in jobs()
        if any("--no-group e2e" in command for command in sync_commands(job))
    ]

    assert {(name, key) for name, key, _ in narrow} == {
        ("ci.yml", "test"),
        ("ci.yml", "checks"),
        ("ci.yml", "postgres"),
        ("every-python.yml", "test"),
        ("dependencies.yml", "upgrade"),
    }
    for name, key, job in narrow:
        assert job.get("env", {}).get("UV_NO_SYNC") == "1", f"{name}: {key}"


def test_the_unit_jobs_install_the_playwright_the_unit_tests_import():
    """`tests/test_documents.py` imports `playwright.sync_api` to stand in for the fallback
    renderer, so a job that runs the unit suite installs the library, through the `chromium`
    extra, and neither the pytest plugin nor a browser."""
    for name, key in (
        ("ci.yml", "test"),
        ("every-python.yml", "test"),
        ("dependencies.yml", "upgrade"),
    ):
        (command,) = sync_commands(workflows()[name]["jobs"][key])
        assert "--no-group e2e" in command and "--extra chromium" in command, f"{name}: {key}"
    assert "import playwright.sync_api" in (ROOT / "tests" / "test_documents.py").read_text(
        encoding="utf-8"
    )
