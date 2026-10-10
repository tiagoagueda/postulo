"""What the pipeline and the image fetch is pinned by hash, not only by name (#790).

``curl | sh`` ran whatever an address answered with, and ``ghcr.io/astral-sh/uv:0.12.17`` is a
tag a registry can move. The installer is the trust root for the uv binary it then downloads,
so its sha256 is checked before it runs, and the image the binary is copied from is named by
digest. These tests keep the pins from being half-bumped or quietly removed: they cannot
fetch anything, so they check the shape, and the CI run is what proves the hashes are right.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".forgejo" / "workflows").glob("*.yml"))
DOCKERFILE = ROOT / "docker" / "Dockerfile"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def installers() -> dict[Path, str]:
    return {path: read(path) for path in WORKFLOWS if "astral.sh/uv/" in read(path)}


def test_there_are_workflows_that_install_uv():
    assert len(installers()) >= 4


def test_no_workflow_pipes_the_installer_into_a_shell():
    for path, text in installers().items():
        assert not re.search(r"install\.sh\"?\s*\|\s*(?:ba)?sh(?:\s|$)", text), path.name


def test_every_install_checks_the_installer_before_running_it():
    for path, text in installers().items():
        fetches = text.count('astral.sh/uv/${UV_VERSION}/install.sh" -o /tmp/uv-install.sh')
        checks = text.count("sha256sum -c -")
        runs = text.count("sh /tmp/uv-install.sh")
        assert fetches >= 1, path.name
        assert fetches == checks == runs, (
            f"{path.name}: {fetches} fetches, {checks} checks, {runs} runs"
        )
        assert text.index("sha256sum -c -") < text.index("sh /tmp/uv-install.sh"), path.name


def pins() -> dict[str, tuple[str, str]]:
    found = {}
    for path, text in installers().items():
        version = re.search(r'^\s*UV_VERSION:\s*"([^"]+)"', text, re.M)
        digest = re.search(r'^\s*UV_INSTALLER_SHA256:\s*"([^"]+)"', text, re.M)
        assert version and digest, f"{path.name} names no version or no checksum"
        found[path.name] = (version[1], digest[1])
    return found


def test_each_workflow_names_a_checksum_beside_its_version():
    for name, (_version, digest) in pins().items():
        assert SHA256.match(digest), f"{name}: {digest!r} is not a sha256"


def test_all_workflows_pin_the_same_version_and_the_same_checksum():
    assert len(set(pins().values())) == 1, pins()


def test_the_dockerfile_names_the_uv_image_by_digest_and_the_same_version():
    (version, _digest), *_ = pins().values()
    lines = [line for line in read(DOCKERFILE).splitlines() if "ghcr.io/astral-sh/uv:" in line]
    assert len(lines) == 2, "the build and the runtime stage both copy uv"
    for line in lines:
        found = re.search(r"uv:([\w.]+)@sha256:([0-9a-f]{64})\b", line)
        assert found, f"not pinned by digest: {line}"
        assert found[1] == version, f"{line} is not uv {version}"
    assert len({re.search(r"@(sha256:[0-9a-f]{64})", line)[1] for line in lines}) == 1


def test_pre_commit_names_the_same_version():
    (version, _digest), *_ = pins().values()
    assert f"rev: {version}" in read(PRE_COMMIT)
