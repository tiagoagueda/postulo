"""Helpers for the tests of the writing checkers (#1258): a checker made in a line, and a
plugin put into the registry for one test, as one Postulo ships or as one installed beside it.
"""

from __future__ import annotations

import html
import re

from postulo.plugins import api, registry

KIND = "writing-checker"


def checker(check, **changes):
    values = dict(  # noqa: C408
        key="test-checks",
        label="Test checks",
        documents=("letter",),
        languages=(),
        offline=True,
        check=check,
    )
    values.update(changes)
    return api.WritingChecker(**values)


def finding(**changes):
    values = dict(check="test", message="Something to look at.", passage="body", start=0, end=4)  # noqa: C408
    values.update(changes)
    return api.WritingFinding(**values)


def plugin(name: str, *checkers, **attributes):
    """A writing-checker plugin class called ``name`` that holds ``checkers``."""
    return type(
        "Plugin",
        (),
        {"name": name, "kind": KIND, "label": name.title(), "checkers": checkers, **attributes},
    )


def forget() -> None:
    """Have the registry build the kind again, and the sub-plugins with it."""
    registry._cache.pop(KIND, None)
    registry._changed()


def ship(monkeypatch, *classes) -> None:
    """Register plugin classes as ones Postulo ships, after its own, for this test."""
    monkeypatch.setitem(registry._builtin, KIND, [*registry._builtin.get(KIND, []), *classes])
    forget()


def install(monkeypatch, *classes) -> None:
    """Have plugin classes loaded as ones installed beside Postulo, for this test."""
    forget()
    monkeypatch.setitem(
        registry._cache, KIND, [*(one() for one in classes), *registry.plugins(KIND)]
    )
    registry._changed()


def only(monkeypatch, *classes) -> None:
    """Have these be the only writing checkers Postulo ships: none, to have none at all."""
    monkeypatch.setitem(registry._builtin, KIND, list(classes))
    forget()


def section(page: str) -> str:
    """The list's own section of a page, or nothing where the page draws none."""
    found = re.search(r'<section id="writing-findings".*?</section>', page, re.S)
    return found.group(0) if found else ""


def forms(page: str) -> list[dict[str, str]]:
    """The fields of every replace form in a page, as a browser would post them."""
    found = []
    for form in re.findall(r"<form[^>]*data-writing-apply[^>]*>.*?</form>", page, re.S):
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
        fields.pop("csrfmiddlewaretoken", None)
        found.append({name: html.unescape(value) for name, value in fields.items()})
    return found
