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
