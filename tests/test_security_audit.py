"""The dependency audit's exceptions, each held to its reason (#323).

`ci.yml` runs `pip-audit --strict` over the lock file, and an advisory can be ignored there
by name. An ignored advisory is a hole in the one check that notices a vulnerable
dependency without anybody having to look, so each one has to say why it cannot reach
Postulo and when it goes -- and the day its reason stops being true, this file fails.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".forgejo" / "workflows" / "ci.yml"
LOCK = ROOT / "uv.lock"

#: advisory -> (package, the first version that fixes it, the issue that says why).
IGNORED = {
    # oauthlib's OAuth2 token endpoint compares a PKCE verifier with `==`. That is the
    # provider's side; Postulo is only ever the client (allauth's OpenID Connect sign-in),
    # and every django-allauth release so far requires `oauthlib<4`.
    "CVE-2026-49265": ("oauthlib", "4.0.0", "#323"),
}


def version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def locked(package: str) -> str:
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return next(entry["version"] for entry in lock["package"] if entry["name"] == package)


def test_every_ignored_advisory_is_one_this_file_explains():
    flagged = set(re.findall(r"--ignore-vuln\s+(\S+)", CI.read_text(encoding="utf-8")))
    assert flagged == set(IGNORED), (
        "an advisory ignored in ci.yml needs its reason here, and one listed here must be "
        f"ignored there: ci.yml has {sorted(flagged)}, this file {sorted(IGNORED)}"
    )


def test_an_ignored_advisory_goes_once_the_lock_can_take_its_fix():
    for advisory, (package, fixed, issue) in IGNORED.items():
        held = locked(package)
        assert version(held) < version(fixed), (
            f"the lock holds {package} {held}, which fixes {advisory}: drop "
            f"`--ignore-vuln {advisory}` from ci.yml and its entry here ({issue})"
        )


def test_postulo_never_runs_oauthlibs_provider_side():
    """The reason CVE-2026-49265 cannot reach Postulo: no OAuth2 provider is installed,
    and nothing in the code talks to oauthlib directly."""
    assert not [app for app in settings.INSTALLED_APPS if app.startswith("allauth.idp")]
    source = ROOT / "src" / "postulo"
    importing = [
        str(path.relative_to(ROOT))
        for path in source.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+oauthlib\b", path.read_text(encoding="utf-8"), re.M)
    ]
    assert not importing, importing
