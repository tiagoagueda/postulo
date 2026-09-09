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


# ------------------------------------------------- what the image installs, and keeps

#: The line that builds the runtime environment. Read rather than run, because nothing here
#: builds an image (#81) and the last two mistakes in this file both looked correct.
SYNC = re.compile(r"^RUN uv sync .*$", re.M)


def sync_line() -> str:
    found = SYNC.findall(DOCKERFILE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one `uv sync` line, found {len(found)}"
    return found[0]


def test_the_sync_excludes_every_dependency_group():
    """#154: `--no-dev` omits the group called `dev` and nothing else.

    `pyproject.toml` declares `dev` and `e2e` and puts both in `default-groups`, so a line
    saying `--no-dev` reads correctly and installs Playwright anyway — 136 MB of browser test
    tool, bundling a Node.js runtime, in an image that runs neither. Naming groups to exclude
    goes stale the moment a third is added; excluding all of them does not.
    """
    line = sync_line()

    assert "--no-default-groups" in line, line
    assert "--no-dev" not in line, "names one group and misses the others"


def test_the_sync_keeps_no_download_cache():
    """uv unpacks every wheel into its cache and keeps it: 296 MB of a single-stage image.

    Deleting it in a later layer frees nothing — the bytes are already below — so it has to
    not be written in the first place.
    """
    assert "--no-cache" in sync_line(), sync_line()


def test_every_group_in_the_project_is_covered_by_that_flag():
    """The reason `--no-default-groups` is the right flag, asserted rather than assumed.

    If somebody adds a third group tomorrow, this test keeps passing — which is the whole
    point. It fails only if `default-groups` stops naming what the Dockerfile relies on.
    """
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = set(project.get("dependency-groups", {}))
    defaults = set(project.get("tool", {}).get("uv", {}).get("default-groups", []))

    assert defaults <= groups, (
        f"default-groups names a group that does not exist: {defaults - groups}"
    )
    assert len(groups) > 1, "with one group `--no-dev` would have been enough; this guards the rest"


# ------------------------------------------- what the image does about Debian's updates


def apt_commands(text: str | None = None) -> list[str]:
    """Every apt layer, one string each, with the line continuations folded away.

    Read rather than run, for the same reason as everything else in this file (#81).
    """
    source = DOCKERFILE.read_text(encoding="utf-8") if text is None else text
    folded = source.replace("\\\n", " ")
    return [line for line in folded.splitlines() if "apt-get update" in line]


def test_the_apt_reader_finds_what_it_is_looking_for():
    """A test that reads a file has to be shown failing, or it passes on an empty match."""
    assert len(apt_commands()) == 2, "the runtime layer and the plugin one"
    assert apt_commands("RUN apt-get update \\\n && apt-get install -y curl") == [
        "RUN apt-get update   && apt-get install -y curl"
    ]
    assert apt_commands("RUN echo hello") == []


def test_every_apt_layer_takes_debians_updates():
    """#155: `libpcre2-8-0` carried a fixable High because nothing ever upgraded it.

    It arrives with Python rather than with anything Postulo asked for, so no `install`
    line would have touched it, and the base image keeps whatever it was built with. The
    only thing that reaches a package like that is `upgrade`.

    Asserted for every apt layer, not only the first: an image built with
    `POSTULO_EXTRA_PACKAGES` refreshes the index a second time, and an image with plugins
    must not be quietly less patched than one without them.
    """
    for layer in apt_commands():
        assert "apt-get upgrade" in layer, f"takes no security updates: {layer}"


def test_the_upgrade_sits_between_the_refresh_and_the_install():
    """Order is the whole of whether it does anything.

    Before `update` it runs against whatever index the base image shipped, which is the
    stale one that caused this. After `install`, the packages just installed came from the
    old index. And in a `RUN` of its own it is a separate layer, served from cache on a
    build whose `update` layer was also cached — so it would be skipped exactly when the
    index it needed had changed.
    """
    for layer in apt_commands():
        refresh = layer.index("apt-get update")
        upgrade = layer.index("apt-get upgrade")
        assert refresh < upgrade, f"upgrades against a stale index: {layer}"
        if "apt-get install" in layer:
            assert upgrade < layer.index("apt-get install"), (
                f"installs from the index it has not refreshed against: {layer}"
            )


def test_it_is_upgrade_and_not_dist_upgrade():
    """Within a stable release, nothing should add or remove packages behind the build."""
    for layer in apt_commands():
        assert "dist-upgrade" not in layer, layer


def test_the_base_image_is_not_pinned_by_digest():
    """The decision, written down where changing it has to be deliberate.

    A digest buys reproducibility and makes Debian's updates arrive only when somebody
    remembers to bump it. A manual step nobody performs is how the finding got here, so
    the image drifts towards being patched instead. If this ever becomes a pin, the
    upgrade above stops being optional — it becomes the only thing patching anything.
    """
    from_lines = [
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("FROM ")
    ]

    assert from_lines, "no FROM line found"
    for line in from_lines:
        assert "@sha256:" not in line, (
            "pinned by digest: see #155 before deciding this, and keep the upgrade"
        )
