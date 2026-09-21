"""The surface check, for a plugin's own test suite to run against itself (#229).

`postulo.plugins.api` is a promise: those names keep working across a minor release, and
everything else in `postulo` is this month's internals. Postulo holds itself to it —
`tests/test_plugin_surface.py` walks every plugin it ships and fails on one that reaches
past — and until now a plugin written outside the core had no way to run the same check.
So each of the official ones drifted past the surface without anything saying so, and the
drift was found by a code audit rather than by a test.

This module is that check, published the way the catalogue tool was (#187): a plugin
repository already depends on Postulo for its tests, so it can import this and assert the
same thing about itself in three lines.

    from pathlib import Path

    from postulo.plugins.testing import assert_imports_only_the_surface

    def test_the_plugin_imports_only_the_surface():
        assert_imports_only_the_surface(
            Path(__file__).parent.parent / "src" / "postulo_yours",
            package="postulo_yours",
        )

**It reads the source rather than importing it**, so a module that reaches past the surface
fails whether or not the import is ever executed: a lazy ``from postulo.core import site``
inside a method is exactly as much of a dependency as one at the top of the file, and is the
shape most real ones take. Relative imports are resolved, because a dependency spelled with
a dot is still a dependency.

**`allowed` is not a list of exemptions.** It maps a module to the reason that plugin has to
reach for it, and a new entry is a decision somebody makes on purpose. A stale entry is a
failure too — see `unused_allowances` — because one nobody removed hides the next real
dependency behind it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

#: The one module a plugin may import from, and the reason there is exactly one.
SURFACE = "postulo.plugins.api"


def _is_postulo(name: str) -> bool:
    """Whether ``name`` is a module of Postulo's, rather than one merely named like it.

    ``postulo_dav`` starts with the same eight letters and is the plugin's own code.
    """
    return name == "postulo" or name.startswith("postulo.")


def _package_parts(path: Path, *, root: Path, package: str) -> list[str]:
    """The dotted package a file sits in, for resolving its relative imports."""
    return [*package.split("."), *path.relative_to(root).parts[:-1]]


def imports_in(path: Path, *, root: Path, package: str) -> set[str]:
    """Every ``postulo.*`` module one file imports, at any depth, read rather than run.

    ``package`` is the plugin's own dotted name: reaching into itself is not reaching past
    the surface, and a plugin that has become a package is allowed an inside.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    here = _package_parts(path, root=root, package=package)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[: len(here) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            names = [module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        for name in names:
            mine = name == package or name.startswith(f"{package}.")
            if _is_postulo(name) and not mine:
                found.add(name)
    return found


def imports_of(package_dir: Path | str, *, package: str) -> set[str]:
    """Every ``postulo.*`` module a whole plugin package imports.

    ``package_dir`` is the directory the package's ``__init__.py`` is in; ``package`` is what
    it is imported as. Every ``.py`` under it is read, because a plugin is more than its
    ``__init__``.
    """
    root = Path(package_dir)
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        found |= imports_in(path, root=root, package=package)
    return found


def reaching_past_the_surface(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> list[str]:
    """What this plugin imports from Postulo that is neither the surface nor written down."""
    return sorted(imports_of(package_dir, package=package) - {SURFACE} - set(allowed))


def unused_allowances(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> list[str]:
    """Modules ``allowed`` names that the plugin no longer imports.

    Checked as well as the other direction, because the list is the map of what is left to
    do: an entry nobody removed lets the next real dependency in behind it.
    """
    return sorted(set(allowed) - imports_of(package_dir, package=package))


def assert_imports_only_the_surface(
    package_dir: Path | str,
    *,
    package: str,
    allowed: Mapping[str, str] | Iterable[str] = (),
) -> None:
    """Fail unless this plugin imports `SURFACE` and whatever ``allowed`` says it may.

    Both directions, in one call: something reached for that is not written down, and
    something written down that is no longer reached for.
    """
    past = reaching_past_the_surface(package_dir, package=package, allowed=allowed)
    assert not past, (
        f"{package} reaches past the plugin surface: {past}. Either use the name on "
        f"{SURFACE} instead, ask for it to be added, or record the dependency in `allowed` "
        f"with the reason it is needed — a new one is a decision, not an accident."
    )

    stale = unused_allowances(package_dir, package=package, allowed=allowed)
    assert not stale, (
        f"{package} no longer imports {stale}. Delete those entries from `allowed`: the "
        f"list is the map of what is left to do, and a stale entry hides the next one."
    )
