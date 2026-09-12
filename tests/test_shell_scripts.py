"""The shell scripts parse, and are executable.

Two faults in one afternoon, both found by a container build rather than by a test, and
both trivial to catch here (#190):

- `scripts/scan-image.sh` and `scripts/check-image.sh` were recorded `100644`, so
  `./scripts/scan-image.sh` — the way `CONTRIBUTING.md` tells a person to run it, and the
  way both image workflows call it — failed with *Permission denied* on any checkout that
  honours the bit. They were written where `core.fileMode` is `false`.
- A comment added to one of them carried an unescaped newline, which left a quote open and
  broke the whole file. `bash -n` would have said so in a millisecond; instead a container
  was built first and the script failed after it.

Neither needed a shell to *run* to be caught, only to be read. There is no shellcheck here
and this is not it: this asks the two questions that have actually gone wrong.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: Resolved rather than named, so the calls below are a full path and a fixed argument
#: list — which is what the bandit rules are asking for, and is true here.
GIT = shutil.which("git") or "git"
BASH = shutil.which("bash")


#: Tracked shell scripts. Read from git rather than the filesystem so a stray `.sh` in a
#: build directory or a virtualenv is not mistaken for one of ours.
def tracked_scripts() -> list[Path]:
    # A fixed argument list, and git from PATH is how every script and workflow here
    # calls it. Asking git rather than the filesystem is the point: what is *tracked*.
    listed = subprocess.run(  # noqa: S603 - a fixed argument list, both binaries resolved above
        [GIT, "ls-files", "*.sh"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [REPO / name for name in listed]


SCRIPTS = tracked_scripts()


def test_there_are_some_to_check():
    """A glob that quietly matched nothing would pass every test below it."""
    assert SCRIPTS, "no tracked shell scripts found"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_it_parses(script: Path):
    """`bash -n` reads the file and does not run a line of it."""
    if BASH is None:  # pragma: no cover - every machine this runs on has one
        pytest.skip("no bash on this machine")

    result = subprocess.run(  # noqa: S603 - a fixed argument list, both binaries resolved above
        [BASH, "-n", str(script)],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert result.returncode == 0, f"{script.name} does not parse:\n{result.stderr.strip()}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_git_records_it_as_executable(script: Path):
    """The mode in the index, not on this filesystem.

    Windows checkouts set `core.fileMode false`, so the working copy says nothing useful
    and `os.access` would pass on a machine where the bit was never recorded. What matters
    is what somebody else's clone gets. Do not 'simplify' this into an `os.access` call:
    it would pass on the machine where the bug is and fail nowhere.

    `docker/entrypoint.sh` is exempt: it is copied into the image and made executable there
    by the Dockerfile, which is the only place it is ever run.
    """
    if script.name == "entrypoint.sh":
        pytest.skip("made executable by the Dockerfile that copies it")
    # As above: a fixed argument list, and the index is the only place the mode lives.
    mode = subprocess.run(  # noqa: S603 - a fixed argument list, both binaries resolved above
        ["git", "ls-files", "-s", str(script.relative_to(REPO)).replace("\\", "/")],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert mode, f"{script.name} is not tracked"
    assert mode[0] == "100755", (
        f"{script.name} is recorded {mode[0]}, so `./{script.name}` is Permission denied "
        f"on a fresh clone. Fix with: git update-index --chmod=+x {script.name}"
    )


def test_every_one_starts_with_a_shebang():
    """A script run as `./name` needs one, and the mode above promises it will be."""
    missing = [
        script.name for script in SCRIPTS if not script.read_text(encoding="utf-8").startswith("#!")
    ]
    assert not missing, f"no shebang: {missing}"
