"""gunicorn and Postulo trust one list of proxies, and the hardened override exists (#824)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from postulo.core import proxy

ROOT = Path(__file__).resolve().parent.parent


def test_every_forwarding_header_a_proxy_sets_is_stripped_from_a_stranger():
    for header in ("HTTP_X_FORWARDED_PROTOCOL", "HTTP_X_FORWARDED_SSL"):
        assert header in proxy.FORWARDING_HEADERS


def test_the_entrypoint_gives_gunicorn_the_trusted_proxies():
    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "FORWARDED_ALLOW_IPS" in script and "POSTULO_TRUSTED_PROXIES" in script


@pytest.mark.skipif(
    shutil.which("sh") is None,
    reason="no POSIX shell",
)
def test_the_entrypoint_exports_it_only_when_the_operator_has_not():
    snippet = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    start = snippet.index('if [ -z "${FORWARDED_ALLOW_IPS')
    block = snippet[start : snippet.index('exec "$@"')]

    def run(env):
        return subprocess.run(  # noqa: S603 - a shell and a block of this repository's own script
            [shutil.which("sh"), "-c", block + '\necho "${FORWARDED_ALLOW_IPS:-unset}"'],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert (
        run({"POSTULO_TRUSTED_PROXIES": "10.0.0.0/8,172.16.0.0/12"}) == "10.0.0.0/8,172.16.0.0/12"
    )
    assert (
        run({"POSTULO_TRUSTED_PROXIES": "10.0.0.0/8", "FORWARDED_ALLOW_IPS": "1.2.3.4"})
        == "1.2.3.4"
    )
    assert run({}) == "unset"


def test_the_hardened_override_names_what_it_drops():
    text = (ROOT / "docker" / "compose.hardened.yml").read_text(encoding="utf-8")
    for line in ("read_only: true", "cap_drop", "no-new-privileges", "mem_limit", "pids_limit"):
        assert line in text
