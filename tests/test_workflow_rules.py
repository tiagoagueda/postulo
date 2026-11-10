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
        ("weekly.yml", "esco"),
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


# ------------------------------------------------ what runs weekly, and only that (#1136)


def triggers(name: str) -> dict:
    # YAML 1.1 reads the key `on` as true.
    return workflows()[name][True]


def test_the_whole_of_ci_does_not_run_on_a_schedule():
    """The weekly run of `ci.yml` cost about 50 runner-minutes to get an audit and an ESCO
    check; a week without a push misses nothing else that a push would have tested."""
    assert "schedule" not in triggers("ci.yml")
    assert "schedule" in triggers("weekly.yml")
    assert set(workflows()["weekly.yml"]["jobs"]) == {"security", "esco"}


def test_the_weekly_audit_is_the_audit_a_push_runs():
    """SECURITY.md promises the audit weekly as well as on every push. The weekly copy is
    the same job, step for step: an advisory ignored in one is ignored in the other, and
    `tests/test_security_audit.py` holds every one of those to its reason."""
    on_a_push = workflows()["ci.yml"]["jobs"]["security"]
    weekly = workflows()["weekly.yml"]["jobs"]["security"]

    assert weekly == on_a_push


def test_the_weekly_esco_check_downloads_and_reads_no_cache():
    """The unit jobs keep the classification between runs, so they stopped being the check
    that the service still answers in the shape the loader reads. This job is that check."""
    esco = workflows()["weekly.yml"]["jobs"]["esco"]
    env = workflows()["weekly.yml"]["env"]

    assert not any("actions/cache" in str(step.get("uses", "")) for step in esco["steps"])
    assert any("fetch_esco" in str(step.get("run", "")) for step in esco["steps"])
    for pin in ("UV_VERSION", "UV_INSTALLER_SHA256", "ESCO_REVISION"):
        assert env[pin] == workflows()["ci.yml"]["env"][pin], pin


# ------------------------------------ no step waits for ever on the network (#1138)

#: What a step's text holds when it reaches the network to set a job up.
REACHES_THE_NETWORK = (
    "apt-get ",
    "astral.sh/uv/",
    "uv sync",
    "npm ci",
    "playwright install",
    "fetch_esco",
    "uvx ",
)


def test_every_set_up_step_that_reaches_the_network_has_a_time_limit():
    """This runner ignores a job's `timeout-minutes`, so a download that stalls without
    failing ran until the runner's own limit, hours later, holding one of its two slots.
    A limit on the step ends it in minutes, with the step named."""
    unlimited = []
    for name, key, job in jobs():
        for step in job["steps"]:
            text = str(step.get("run", "")) + str(step.get("uses", ""))
            reaches = any(mark in text for mark in REACHES_THE_NETWORK) or "actions/cache@" in text
            if reaches and "timeout-minutes" not in step:
                unlimited.append(f"{name}: {key}: {step.get('name') or text[:40]}")

    assert unlimited == []


def test_no_job_relies_on_a_time_limit_of_its_own():
    """The runner does not honour one, so a job that had it would only look protected."""
    assert [f"{name}: {key}" for name, key, job in jobs() if "timeout-minutes" in job] == []


def test_the_installer_download_retries_and_gives_up():
    """curl retries nothing and waits for ever unless told otherwise; uv and apt do both by
    themselves. The checksum on the next line is unchanged, so a retry cannot let a different
    script through."""
    downloads = [
        str(step["run"])
        for _, _, job in jobs()
        for step in job["steps"]
        if "astral.sh/uv/" in str(step.get("run", ""))
    ]

    assert downloads, "the search finds the installer steps"
    for run in downloads:
        for flag in ("--retry 3", "--retry-all-errors", "--connect-timeout 10", "--max-time 120"):
            assert flag in run, flag
        assert "sha256sum -c -" in run
