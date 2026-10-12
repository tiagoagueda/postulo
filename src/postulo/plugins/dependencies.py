"""What a plugin needs from the other plugins and modules on the instance (#884).

A release has always said which Postulo it is for (``requires_postulo``, #186). It can now say
which *plugins* and which *modules* it needs as well: ``requires`` on a manifest and on a
catalogue release is a list of names, each with an optional version specifier, in the
vocabulary of a requirement -- ``"helloworld"``, ``"helloworld>=0.3"``, ``"maps"``. A name
is either a plugin or module registered under it, or an installed package (distribution)
called that, because a catalogue speaks of packages and a manifest of the plugins in them.

Three places ask, and they ask here so that they cannot disagree:

* **The policy**, at every request: a plugin whose requirement is missing, or is off, is off
  and its switch is not anybody's to move, with the names beside it (see ``policy``).
* **Installing from a catalogue**: refused before the download, naming what is missing, and
  the page offers the missing plugin when the same catalogue lists it. Never installed on the
  administrator's behalf: installing runs code (#94), so each one is a decision.
* **Removing**: refused while something installed needs it, naming what.

A module's version is Postulo's, so a specifier on a module (``maps>=0.6``) is a statement
about Postulo.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext as _


@dataclass(frozen=True)
class Need:
    """One requirement: a name and, optionally, the versions that will do."""

    name: str
    specifier: str = ""

    def __str__(self) -> str:
        return f"{self.name}{self.specifier}"


def parse(entries) -> list[Need]:
    """The requirements in a list of strings, skipping what cannot be read.

    A string that is not a requirement is dropped and not guessed at: this is read from a
    manifest or a signed index, and a plugin that cannot say what it needs is better shown as
    needing nothing it can name than as breaking a page.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    found = []
    for entry in entries or ():
        try:
            parsed = Requirement(str(entry))
        except InvalidRequirement:
            continue
        found.append(Need(parsed.name, str(parsed.specifier)))
    return found


def _fits(specifier: str, version: str) -> bool:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    if not specifier:
        return True
    try:
        return SpecifierSet(specifier).contains(Version(version), prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        # A version nobody can read does not satisfy a range somebody wrote down.
        return False


def _canonical(name: str) -> str:
    from .record import canonicalise

    return canonicalise(name)


def _registered(name: str):
    """The plugin or module registered under ``name``, or ``None``."""
    from .registry import find_any

    return find_any(name)


def is_present(need: Need) -> bool:
    """Whether something installed or shipped is called this, at a version that will do.

    Present is not the same as on: a plugin switched off is present, and the policy treats
    its dependents as off for that reason, not for this one.
    """
    from . import base
    from .installing import installed

    plugin = _registered(need.name)
    if plugin is not None:
        return _fits(need.specifier, base.manifest_of(plugin).version or "0")
    entry = installed(need.name)
    if entry is not None and not entry.disabled:
        return _fits(need.specifier, entry.version)
    return False


def unmet(entries) -> list[Need]:
    """The requirements among ``entries`` that nothing present satisfies."""
    return [need for need in parse(entries) if not is_present(need)]


def label_of(name: str) -> str:
    """A name for a person: the plugin's label if there is one, else the name itself."""
    from . import base

    plugin = _registered(name)
    return str(base.manifest_of(plugin).label) if plugin is not None else name


def needs_of(plugin) -> list[Need]:
    """What a loaded plugin says it needs."""
    from . import base

    return parse(base.manifest_of(plugin).requires)


def provided_by(distribution: str) -> set[str]:
    """The names of the plugins a package carries, or nothing for one that is not loaded.

    Found through the top-level package each plugin's code lives in, as the plugins page finds
    a plugin's package (#603): a package's name and the names it registers are different
    strings, and a requirement may be written in either.
    """
    from .record import packages_by_distribution
    from .registry import GROUPS, plugins

    packages = packages_by_distribution()
    wanted = _canonical(distribution)
    found: set[str] = set()
    for kind in GROUPS:
        for plugin in plugins(kind):
            top_level = type(plugin).__module__.split(".")[0]
            if top_level == "postulo":
                continue
            if wanted in {_canonical(name) for name in packages.get(top_level, [])}:
                found.add(plugin.name)
    return found


def dependents_of(distribution: str) -> list[str]:
    """Who would be left without something if this package went: their names, sorted.

    Two sources, because a requirement is written in two places. What is installed says what
    it needs in the record (the release it came from), and what is loaded says it in its
    manifest. Either naming this package, or a plugin in it, counts. A package does not
    depend on itself.
    """
    from .record import read_record
    from .registry import GROUPS, plugins

    names = {_canonical(distribution), *(_canonical(name) for name in provided_by(distribution))}
    found: set[str] = set()
    for entry in read_record():
        if _canonical(entry.name) == _canonical(distribution):
            continue
        if any(_canonical(need.name) in names for need in parse(entry.requires)):
            found.add(entry.name)
    inside = provided_by(distribution)
    for kind in GROUPS:
        for plugin in plugins(kind):
            if plugin.name in inside:
                continue
            if any(_canonical(need.name) in names for need in needs_of(plugin)):
                found.add(plugin.name)
    return sorted(found)


def refuse_removing(distribution: str) -> str:
    """A sentence naming what needs this package, or nothing when nothing does."""
    needing = dependents_of(distribution)
    if not needing:
        return ""
    return str(
        _("%(name)s is needed by %(others)s. Remove those first.")
        % {"name": distribution, "others": ", ".join(needing)}
    )


def refuse_installing(name: str, entries) -> str:
    """A sentence naming what a release needs that is not here, or nothing."""
    missing = unmet(entries)
    if not missing:
        return ""
    return str(
        _("%(name)s needs %(missing)s, which is not installed. Install that first.")
        % {"name": name, "missing": ", ".join(str(need) for need in missing)}
    )
