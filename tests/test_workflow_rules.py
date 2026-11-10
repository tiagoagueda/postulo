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
