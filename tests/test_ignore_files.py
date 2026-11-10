"""What the repository and the image leave out: the data a running checkout downloads.

`manage.py fetch_esco` and `manage.py fetch_geonames` write megabytes of reference data into
`src/postulo/jobs/data/`, beside the few small files that are committed there. Neither a
commit nor an image built from a working tree should carry them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from postulo.jobs import places

ROOT = Path(__file__).resolve().parents[1]
DATA = "src/postulo/jobs/data"

#: Every file the two fetch commands leave in the data directory, or write on the way.
DOWNLOADED = (
    "esco-1.2.1.json",
    "esco-skills-1.2.1.zip",
    "esco-1.2.1.json.tmp",
    places.CITIES_FILE,
    places.COUNTRIES_FILE,
    places.POSTCODES_FILE,
    places.LOOKUP_FILE,
    places.POSTCODES_LOOKUP_FILE,
    places.LOOKUP_FILE + "-journal",
    places.LOOKUP_FILE + "-wal",
    places.LOOKUP_FILE + "x1y2z3.tmp",
    places.CITIES_FILE + ".tmp",
)
#: What is committed there, and must stay visible to git and to the image.
COMMITTED = ("ESCO-LICENCE.md", "GEONAMES-LICENCE.md", "nace-2.1.json")


def ignored(path: str) -> bool:
    answer = subprocess.run(  # noqa: S603 - git as found on the path, and a path built here
        [shutil.which("git"), "check-ignore", "--quiet", "--no-index", path],
        cwd=ROOT,
        check=False,
    )
    assert answer.returncode in (0, 1), f"git could not say for {path}"
    return answer.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the repository, not an export")
def test_git_ignores_everything_the_fetch_commands_write():
    """The lookup databases were not ignored: 30 MB, built from the downloaded tables, that
    one `git add -A` would have committed for every clone to carry (#1161)."""
    assert [name for name in DOWNLOADED if not ignored(f"{DATA}/{name}")] == []
    assert [name for name in COMMITTED if ignored(f"{DATA}/{name}")] == []
    for name in COMMITTED:
        assert (ROOT / DATA / name).is_file(), name


def docker_patterns() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def left_out_of_the_image(path: str) -> bool:
    """Docker's rule, near enough for these patterns: a pattern is matched against the whole
    path from the context root, `**/` standing for any directories, and a match on a
    directory leaves out what is under it."""
    from fnmatch import fnmatchcase

    parts = path.split("/")
    prefixes = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    for pattern in docker_patterns():
        if pattern.startswith("!"):
            continue
        if pattern.startswith("**/"):
            if any(fnmatchcase(part, pattern[3:]) for part in parts):
                return True
        elif any(
            fnmatchcase(prefix, pattern) and prefix.count("/") == pattern.count("/")
            for prefix in prefixes
        ):
            return True
    return False


def test_an_image_built_from_a_working_tree_leaves_the_downloads_out():
    """`.dockerignore` named `data`, `__pycache__` and `*.py[cod]`, which match at the
    context root alone. An image built on a machine that had run the app carried 71 MB of
    downloaded tables under `src` and every bytecode directory with them (#1141)."""
    assert [name for name in DOWNLOADED if not left_out_of_the_image(f"{DATA}/{name}")] == []
    assert [name for name in COMMITTED if left_out_of_the_image(f"{DATA}/{name}")] == []
    for path in (
        "src/postulo/jobs/__pycache__/places.cpython-314.pyc",
        "src/postulo/core/__pycache__",
        "src/postulo/core/models.pyc",
        "node_modules/tailwindcss/package.json",
        "src/postulo/static/node_modules/x.js",
        "src/.pytest_cache/README.md",
        "data/geonames/cities.sqlite",
    ):
        assert left_out_of_the_image(path), path
    for path in ("src/postulo/jobs/places.py", "src/postulo/jobs/data/nace-2.1.json", "uv.lock"):
        assert not left_out_of_the_image(path), path


def test_what_the_old_patterns_missed_is_what_the_test_would_have_caught(monkeypatch):
    """The matcher above says what Docker says of a bare name: the root, and nowhere else."""
    monkeypatch.setattr(
        "tests.test_ignore_files.docker_patterns", lambda: ["data", "__pycache__", "*.py[cod]"]
    )

    assert left_out_of_the_image("data/x") and left_out_of_the_image("__pycache__/x.pyc")
    assert not left_out_of_the_image("src/postulo/jobs/__pycache__/places.cpython-314.pyc")
    assert not left_out_of_the_image(f"{DATA}/{places.LOOKUP_FILE}")
