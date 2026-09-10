"""The wiki documents the whole of Postulo's API surface, read against the code (#170).

The wiki is its own repository (#169), so this can only look where the workspace keeps it:
`../postulo.wiki`, beside this checkout. Where it is not there the tests skip — CI checks out
this repository alone — which makes it a check for the machine a change is made on, the way
`tests/test_stylesheet.py` is for the stylesheet.

Three surfaces, three pages, and each is checked both ways, so a page cannot fall behind the
code and cannot outlive it either:

- every call the API answers is a row in *The API*'s list of every call, with its method, its
  path and the scope it needs;
- the endpoints for machines, and every metric, are on *Health, metrics and logs*;
- every name `postulo.plugins.api` promises, and every entry-point group a plugin registers
  in, is on *Writing a plugin*.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT.parent / "postulo.wiki"

pytestmark = pytest.mark.skipif(
    not WIKI.is_dir(), reason="the wiki is not checked out beside this repository"
)


def page(name: str) -> str:
    return (WIKI / f"{name}.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------------- the API


def api_calls() -> set[tuple[str, str, str]]:
    """(method, path, scope) for every operation, from the API's own routers."""
    from ninja.constants import NOT_SET

    from postulo.api.api import api
    from postulo.api.auth import ScopedAuth

    calls = set()
    for prefix, router in api._routers:
        for path, view in router.path_operations.items():
            for operation in view.operations:
                callbacks = operation.auth_callbacks or []
                if not callbacks:
                    inherited = router.auth if router.auth not in (None, NOT_SET) else api.auth
                    callbacks = inherited if isinstance(inherited, list | tuple) else [inherited]
                scopes = [auth.scope for auth in callbacks if isinstance(auth, ScopedAuth)]
                full = "/api/v1" + (prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip("/")
                full = re.sub(r"\{(int:)?pk\}", "{id}", full).replace("//", "/")
                scope = f"`{scopes[0]}`" if scopes else "any live token"
                for method in operation.methods:
                    calls.add((method, full, scope))
    return calls


def listed_calls() -> set[tuple[str, str, str]]:
    """The rows of the page's list of every call."""
    text = page("The-capture-API")
    section = text[text.index("## Every call") :]
    section = section[: section.index("\n## ", 1)] if "\n## " in section[1:] else section
    return {
        (method, path, scope.strip())
        for method, path, scope in re.findall(
            r"^\| `([A-Z]+)` \| `(/api/v1/[^`]*)` \| ([^|]+) \|", section, re.M
        )
    }


def test_every_call_the_api_answers_is_listed_with_its_scope():
    missing = api_calls() - listed_calls()
    assert not missing, (
        "The API answers calls its wiki page does not list (or lists with another scope). "
        f"Add them to *The API* → Every call: {sorted(missing)}"
    )


def test_the_page_lists_no_call_the_api_does_not_answer():
    stale = listed_calls() - api_calls()
    assert not stale, f"*The API* lists calls that are gone or changed: {sorted(stale)}"


def test_the_list_reader_finds_what_it_is_looking_for():
    """A reader that finds nothing would make both tests above pass on an empty page."""
    assert ("GET", "/api/v1/me", "any live token") in listed_calls()
    assert len(listed_calls()) > 30


# --------------------------------------------------------------- machine endpoints


def test_every_endpoint_for_machines_is_documented():
    text = page("Health-metrics-and-logs")
    for address in ("/healthz", "/metrics", "/logs"):
        assert f"`GET {address}`" in text, address


def test_every_metric_is_documented():
    source = (ROOT / "src" / "postulo" / "core" / "metrics.py").read_text(encoding="utf-8")
    metrics = set(re.findall(r'"(postulo_[a-z_]+)"', source))
    assert len(metrics) >= 5, "the reader found no metrics"
    text = page("Health-metrics-and-logs")
    missing = sorted(name for name in metrics if f"`{name}`" not in text)
    assert not missing, f"*Health, metrics and logs* does not describe: {missing}"


# ---------------------------------------------------------------- the plugin surface


def test_every_name_on_the_plugin_surface_is_documented():
    from postulo.plugins import api

    text = page("Writing-a-plugin")
    missing = sorted(name for name in api.__all__ if f"`{name}`" not in text)
    assert not missing, f"*Writing a plugin* does not name: {missing}"


def test_every_kind_of_plugin_is_documented_with_its_group():
    from postulo.plugins import registry

    text = page("Writing-a-plugin")
    groups = sorted(group for group in registry.GROUPS.values() if group)
    assert groups, "the reader found no entry-point groups"
    missing = [group for group in groups if f"`{group}`" not in text]
    assert not missing, f"*Writing a plugin* does not say which kind registers in: {missing}"
