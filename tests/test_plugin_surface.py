"""What a plugin may import from Postulo, and the plugins that still reach past it (#126).

> a plugin, even a internal must be a self-contained as possible ... and dont depende on
> the core

That could not be enforced, or even checked, while there was no written answer to *depend on
what, then*. `postulo.plugins.api` is that answer, and this file is what makes it real: a rule
nobody checks drifts back within a release.

The check reads the source rather than importing it, so a module that reaches past the surface
fails here whether or not the import is ever executed — a lazy `from postulo.core import site`
inside a method is exactly as much of a dependency as one at the top of the file, and is the
shape most of the ones below take.

**`REACHING_PAST` is not a list of exemptions.** It is the map of what #129 has still to move,
each entry saying what the plugin needs and therefore what has to become part of the surface or
part of the plugin. It may shrink. A new entry is a decision somebody has to make on purpose,
which is the whole point of the test failing on one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "postulo"

#: Every module holding a plugin Postulo ships, and the plugins in it.
SHIPPED = {
    "plugins/builtin/__init__.py": "the two built-in sources",
    "notifications/email.py": "the email notifier",
    "notifications/smtp.py": "the SMTP transport",
    "documents/stores.py": "the local store",
    "resume/europass.py": "the Europass importer",
    "core/features.py": "the telephone-numbers feature",
}

#: The one module a plugin may import from, and the reason there is exactly one.
SURFACE = "postulo.plugins.api"

#: What each shipped plugin still reaches past the surface for, and why. Every line is work
#: #129 has to do: either the name becomes part of the surface, or it moves into the plugin.
REACHING_PAST: dict[str, dict[str, str]] = {
    "notifications/email.py": {
        "postulo.core": "`site`: the instance's name and from-address, for the message it sends",
        "postulo.notifications.base": "`Notification`, which is what a notifier is handed",
    },
    "notifications/smtp.py": {
        "postulo.core": (
            "`mail` to open an SMTP connection and prove it, and `destinations` for where the "
            "server is allowed to dial (#148)"
        ),
    },
    "documents/stores.py": {
        "postulo.documents.models": (
            "`DocumentKind`, `RenderedDocument`, `UploadedDocument`: the rows whose files it "
            "is storing. The data question #129 names, and the reason the store cannot move "
            "before it is answered"
        ),
        "postulo.notifications.base": "an absolute URL for a document it has stored",
    },
    "resume/europass.py": {
        "postulo.accounts": "`identifiers`: the schemes a Europass file carries",
        "postulo.accounts.models": "`PersonIdentifier`, to write those onto a profile",
        "postulo.core": "`phone_numbers`, to write the numbers it read",
        "postulo.resume.models": (
            "`Education`, `Experience`, `LanguageSkill`, `Project`, `Skill`, `SkillGroup`: "
            "the rows a read Europass file becomes"
        ),
    },
    "core/features.py": {},
    "plugins/builtin/__init__.py": {},
}


def _package_of(path: Path) -> list[str]:
    """The dotted package a file lives in, e.g. `postulo.plugins.builtin` for its `__init__`."""
    return ["postulo", *path.relative_to(ROOT).parts[:-1]]


def _own_package(path: Path) -> str | None:
    """What counts as *inside* this plugin, for a plugin that is a package.

    A plugin that has become a package has an inside, and reaching into it is not reaching
    past the surface -- `plugins/builtin` carrying the HTML helper it is the only user of is
    the point of #129, not a violation. A plugin still living as a single module in a core
    app has no inside, and an import from the app around it is exactly the dependency the
    rule is about.
    """
    return ".".join(_package_of(path)) if path.name == "__init__.py" else None


def postulo_imports(path: Path) -> set[str]:
    """Every `postulo.*` module this file imports, at any depth, read rather than run.

    Relative imports are resolved to the module they name. `from .base import shipped`
    inside `plugins/builtin/` is a dependency on `postulo.plugins.base` exactly as much as
    spelling it out would be, and counting only the absolute form let one hide here until
    #127 moved the file and turned it into `..base` (#126).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(path)
    mine = _own_package(path)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            if module.startswith("postulo") and not _is_mine(module, mine):
                found.add(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("postulo") and not _is_mine(alias.name, mine):
                    found.add(alias.name)
    return found


def _is_mine(module: str, mine: str | None) -> bool:
    return mine is not None and (module == mine or module.startswith(f"{mine}."))


@pytest.mark.parametrize("path", sorted(SHIPPED), ids=lambda p: p)
def test_a_shipped_plugin_imports_the_surface_or_something_written_down(path: str):
    """The rule, and the only way past it is a line somebody wrote deliberately."""
    reaching = postulo_imports(ROOT / path)
    allowed = {SURFACE} | set(REACHING_PAST.get(path, {}))

    past = sorted(reaching - allowed)

    assert not past, (
        f"{SHIPPED[path]} reaches past the plugin surface: {past}. Either add the name to "
        f"postulo.plugins.api, or record the dependency in REACHING_PAST with the reason it "
        f"is needed — a new one is a decision, not an accident."
    )


@pytest.mark.parametrize("path", sorted(SHIPPED), ids=lambda p: p)
def test_nothing_recorded_has_quietly_been_fixed(path: str):
    """A stale entry would let a real dependency back in behind it."""
    reaching = postulo_imports(ROOT / path)

    stale = sorted(set(REACHING_PAST.get(path, {})) - reaching)

    assert not stale, (
        f"{SHIPPED[path]} no longer imports {stale}. Delete those lines from REACHING_PAST: "
        f"the list is the map of what is left to do, and a stale entry hides the next one."
    )


def test_the_stateless_plugins_need_only_the_surface():
    """The check that the surface is not so wide as to be meaningless.

    The two built-in sources need the surface and nothing else, which is what the shape of a
    source makes possible: a URL and some HTML in, a `JobPostingData` out.

    They used to appear to need *nothing*, and that was an artefact rather than a fact: they
    reached for `postulo.plugins.base` through a relative import, which the checker did not
    resolve. #127 moved the file, the relative import changed depth, and the pretence ended.
    """
    assert postulo_imports(ROOT / "plugins/builtin/__init__.py") == {SURFACE}
    assert REACHING_PAST["plugins/builtin/__init__.py"] == {}


def test_a_feature_needs_only_the_surface():
    """A feature is a declaration, so it should reach for nothing else, and does not."""
    assert postulo_imports(ROOT / "core/features.py") == {SURFACE}


# --------------------------------------------------------------- what the surface is


def test_every_promised_name_resolves():
    """A surface that names something it cannot hand over is worse than none."""
    from postulo.plugins import api

    missing = [name for name in api.__all__ if not hasattr(api, name)]

    assert not missing, missing


def test_asking_for_something_else_says_where_to_look():
    from postulo.plugins import api

    with pytest.raises(AttributeError) as raised:
        api.Connection  # noqa: B018

    assert "not part of the plugin surface" in str(raised.value)
    assert "docs/PLUGINS.md" in str(raised.value)


def test_the_surface_holds_the_four_reasons_a_plugin_has_to_depend_on_postulo():
    """Ownership scoping, redirects, outbound requests, consent — each is a promise kept.

    Named individually rather than counted, because the argument for the surface existing at
    all is that these are unavoidable: a plugin that skips any of them breaks something the
    project promises rather than merely being untidy.
    """
    from postulo.plugins import api

    assert api.OwnedQuerySet is not None, "for_user(), or one person sees another's data"
    assert api.safe_next is not None, "or a plugin bounces somebody off the instance"
    assert api.client is not None, "or a plugin dials where the server should not"
    assert api.access_token is not None, "or a plugin keeps a token instead of refreshing it"


def test_importing_the_surface_touches_no_database():
    """It is reachable before the app registry is ready, so it must cost nothing to import."""
    import importlib

    module = importlib.import_module("postulo.plugins.api")

    assert module.__all__, "and it still lists what it promises"
