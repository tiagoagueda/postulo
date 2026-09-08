"""The image build, checked by reading it, because nothing here builds it.

Building the image needs a runner advertising the `docker` label and none is registered
(#81), so the step that would have caught #121 has never run. Until it does, the cheap
check is to read the file: any key written into the build has to be one the production
settings would accept, since that is the settings module the build imports.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from postulo.config.settings import keys

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker" / "Dockerfile"
WORKFLOWS = sorted((ROOT / ".forgejo" / "workflows").glob("*.yml"))

#: A literal value assigned to one of the key variables. A `$(...)` substitution is not a
#: literal and cannot be judged here — nor does it need to be, since the point of writing
#: one is that nobody knows what it will be.
ASSIGNMENT = re.compile(
    r"""POSTULO_(?:SECRET|FIELD)_KEY[=:]\s*["']?(?P<value>[^"'\s$][^"'\s]*)""",
)


def literals_in(text: str) -> list[str]:
    return [match["value"] for match in ASSIGNMENT.finditer(text)]


def test_the_dockerfile_does_not_write_a_key_production_would_refuse():
    """#121: the image stopped building the day #111 landed, and nothing said so.

    The build imports the production settings, which refuse a weak key before Django will
    start. A literal short enough to trip that turns every `docker build` into a failure —
    including the upgrade path of every instance that already exists.
    """
    for value in literals_in(DOCKERFILE.read_text(encoding="utf-8")):
        keys.refuse_a_weak_key(value, name="POSTULO_SECRET_KEY")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_writes_a_key_production_would_refuse(path: Path):
    """The same rule where CI sets one, so the two cannot drift apart again."""
    for value in literals_in(path.read_text(encoding="utf-8")):
        keys.refuse_a_weak_key(value, name="POSTULO_SECRET_KEY")


def test_the_reader_finds_what_it_is_looking_for():
    """A test that reads a file has to be shown failing, or it passes on an empty match."""
    assert literals_in("POSTULO_SECRET_KEY=short") == ["short"]
    assert literals_in('  POSTULO_FIELD_KEY: "a-long-one"') == ["a-long-one"]
    assert literals_in('POSTULO_SECRET_KEY="$(python -c ...)"') == [], (
        "a substitution, not a literal"
    )
