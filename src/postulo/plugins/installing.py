"""Installing a plugin into a running instance, and remembering that it is installed.

Until now a plugin was a shell command: ``uv pip install`` into Postulo's environment, and
a restart. That works on a manual install and not at all in a container, where the
environment is built once, the process runs as a user that cannot write to it, and
anything installed into it is gone at the next upgrade.

So plugins live on the **data volume** instead, in a directory that is added to the import
path at startup, with a **record** beside them saying what is installed and where it came
from. Because the record is on the volume, an upgrade cannot lose them: the entry point
runs ``manage.py plugins sync`` at boot and reinstalls anything the record lists that the
directory lacks.

Three refusals matter, and each of them says why:

* **Not pure Python.** The image has no compiler and should not have one, so a wheel that
  is not ``py3-none-any`` is refused rather than half-installed.
* **Not a Postulo plugin.** A package that declares no ``postulo.*`` entry point would sit
  there doing nothing; better to say so.
* **A dependency that would move one of Postulo's own.** Every install runs with the
  running environment as a constraint, so a plugin can never change the version of
  something Postulo itself depends on. The refusal names the package.

**What is verified, and where that stops.** A catalogue's index is signed and each
wheel is checked against the checksum the signed index carries -- but that covers the
plugin's own file and nothing else. Its requirements are resolved from PyPI when it is
installed, and are whatever was served that day. So the installer takes only wheels
(``--only-binary :all:``), because a source distribution runs its own build code during
installation; and the record keeps every package that actually arrived, so an
administrator can see what is in their instance without a shell. Trusting a plugin means
trusting its dependency list, and the page says as much before anything is fetched.

**The record is also how the processes agree.** Postulo runs as several processes -- three
web workers and a scheduler in the image -- each with its own idea of what is installed.
An install, a removal or a switching off used to be rebuilt only in the one that did it, so
a plugin an administrator had just switched off went on running everywhere else. The record
sits on the shared volume and is the one thing all of them can see, so each write moves its
stamp (`record_stamp`) and every process rebuilds from it the next time it looks a plugin
up (`registry.catch_up`). Only the process making the change writes; the rest read.

Installing a plugin is running somebody else's code inside Postulo, with everything
Postulo can do. Nothing here pretends otherwise; the page that calls it says so plainly,
only administrators reach it, and a plugin can be switched off without being removed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import site
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from email.parser import Parser
from importlib import metadata
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: The file on the volume that says what is installed. Its absence means "nothing".
RECORD_NAME = "plugins.json"
#: Only wheels, and only pure-Python ones.
PURE_PYTHON = "py3-none-any"
#: How long an install may take before it is called a failure.
INSTALL_TIMEOUT = 300
#: How long the check that a new plugin imports may take. Short: it imports the plugin's
#: modules and nothing else, and a module that takes a minute to import is a module that
#: will take a minute on every worker start.
VERIFY_TIMEOUT = 60
#: Where the directory as it was before the last install is kept. Inside the plugins
#: directory because that is certainly writable, and hidden behind a dot because nothing
#: looks for `*.dist-info` below the top level and nothing imports from a dotted name --
#: so what is in here is kept without being live (#246).
PREVIOUS_NAME = ".previous"
#: Where the snapshot from *before* this install waits while one is being taken, so that
#: an install which fails gives it back rather than spending it (#246).
HELD_NAME = ".previous.held"
#: What the snapshot never copies: itself, the one being held, and the scratch file an
#: install writes.
NOT_SNAPSHOTTED = (PREVIOUS_NAME, HELD_NAME, ".previous.writing", ".constraints.txt")


def plugin_groups() -> tuple[str, ...]:
    """Every entry-point group that makes a package a Postulo plugin.

    Asked of the registry rather than listed here. This used to be four names typed out by
    hand, written when there were four kinds, and it went out of date the moment there were
    more: a transport, an outbox, a feature or an importer declares a group the list did
    not know about, so the installer read the wheel's entry points as nobody's and refused
    it for "declaring no Postulo entry point" -- about a package whose entry point
    Postulo's own documentation had told its author to declare (#228).

    A kind whose group is empty registers nothing from outside this process, which is the
    same rule `registry._load_third_party` applies at the other end. One list, one answer.
    """
    from .registry import GROUPS

    return tuple(dict.fromkeys(group for group in GROUPS.values() if group))


class InstallError(Exception):
    """The install cannot go ahead, and the message is for the administrator."""


@dataclass
class PackageInfo:
    """What a wheel says about itself, read before anything is installed."""

    name: str
    version: str
    summary: str = ""
    licence: str = ""
    author: str = ""
    source_url: str = ""
    requires_python: str = ""
    requires: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    pure_python: bool = True
    filename: str = ""
    sha256: str = ""

    @property
    def is_plugin(self) -> bool:
        return bool(self.entry_points)


@dataclass
class Installed:
    """One line of the record: what is installed, and where it came from."""

    name: str
    version: str
    origin: str = "upload"  # upload | catalogue:<name>
    source: str = ""  # the wheel's filename, or the URL it came from
    sha256: str = ""
    installed_at: str = ""
    installed_by: str = ""
    entry_points: list[str] = field(default_factory=list)
    disabled: bool = False
    #: What the wheel said about itself. Read at install time and, until #97, thrown away
    #: the moment the confirmation screen had shown it -- so an administrator could see who
    #: wrote a plugin exactly once, and never again.
    summary: str = ""
    licence: str = ""
    author: str = ""
    source_url: str = ""
    #: Which Postulo the plugin says it is for, as the catalogue release declared it -- a
    #: version specifier such as ``>=0.3,<1.0``. Empty for an upload, which declares
    #: nothing: a wheel's own metadata cannot say it, since a plugin does not depend on
    #: Postulo. Checked at install and shown on the page ever after, so a core upgrade
    #: that leaves a plugin behind is visible there and not only in a log (#186).
    requires_postulo: str = ""
    #: Everything that arrived alongside the wheel, as ``name==version``. The signature
    #: and the checksum cover the plugin's own file and nothing else: its requirements are
    #: resolved from PyPI at install time and are whatever was served that day. Recording
    #: them is what lets an administrator see what is actually in their instance without a
    #: shell, and notice when an update quietly brings something new.
    dependencies: list[str] = field(default_factory=list)

    @property
    def is_from_catalogue(self) -> bool:
        return self.origin.startswith("catalogue:")


# ---------------------------------------------------------------- the directory


def plugins_dir() -> Path:
    return Path(settings.POSTULO_PLUGINS_DIR)


def record_path() -> Path:
    return plugins_dir() / RECORD_NAME


def activate() -> None:
    """Put the plugins directory on the import path, if it exists.

    ``site.addsitedir`` rather than a plain ``sys.path`` append, so that ``.pth`` files a
    package ships are honoured, exactly as they would be in a normal installation.
    """
    directory = plugins_dir()
    if not directory.is_dir():
        return
    if str(directory) not in sys.path:
        site.addsitedir(str(directory))


# ------------------------------------------------------------------- the record


#: The record as it was last parsed, and the stamp it was parsed at (#231). Read and parsed
#: on every plugin lookup before this: `decide` asks, `shipped_inside` asks, and a company
#: page that draws six plugin marks asked six times, each a file read and a JSON parse.
#:
#: Keyed on `record_stamp`, which is one `stat`. That is the same test every other process
#: uses to decide whether to reload, and `write_record` guarantees it moves whenever the
#: record does -- so this cannot go stale behind an install made by the scheduler or by a
#: second worker, which a plain memo would.
_record_cache: tuple[str, list[Installed]] | None = None


def read_record() -> list[Installed]:
    global _record_cache

    stamp = record_stamp()
    if _record_cache is not None and _record_cache[0] == stamp:
        # A copy, because callers filter and sort what they are handed and one of them
        # mutating the list would change what the next one reads.
        return list(_record_cache[1])
    path = record_path()
    if not path.is_file():
        _record_cache = (stamp, [])
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = raw.get("plugins", []) if isinstance(raw, dict) else raw
    found = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name"):
            fields = {key: entry.get(key) for key in Installed.__dataclass_fields__ if key in entry}
            found.append(Installed(**fields))
    _record_cache = (stamp, list(found))
    return found


#: Which distribution each importable top-level package came from, cached on the record's
#: stamp (#231).
#:
#: `importlib.metadata.packages_distributions()` walks *every* installed distribution and
#: reads its file list to build this -- Django, Pillow, WeasyPrint, the lot. On the
#: administrator's plugins page it was about four and a half seconds of a page load, per
#: load, and something like forty seconds of every CI run.
#:
#: The answer changes only when something is installed or removed, which is exactly when the
#: record's stamp moves, so that is the key. Nothing else in the process installs packages
#: while it runs.
_distributions_cache: tuple[str, dict[str, list[str]]] | None = None


def packages_by_distribution() -> dict[str, list[str]]:
    """``{top-level package: [distribution names]}``, read once per record. See above."""
    global _distributions_cache

    stamp = record_stamp()
    if _distributions_cache is None or _distributions_cache[0] != stamp:
        from importlib.metadata import packages_distributions

        try:
            mapping = dict(packages_distributions())
        except Exception:  # pragma: no cover - a broken distribution explains nobody's name
            mapping = {}
        _distributions_cache = (stamp, mapping)
    return _distributions_cache[1]


def write_record(entries: list[Installed]) -> None:
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "plugins": [asdict(entry) for entry in sorted(entries, key=lambda e: e.name)],
    }
    before = record_stamp()
    path = record_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if record_stamp() == before:
        # Every other process decides whether to reload from this stamp, so a write that
        # does not move it is a change nobody else ever sees. Two writes land in the same
        # stamp only where the filesystem keeps coarse timestamps *and* the record came out
        # the same length -- switching a plugin off and on again within the same second is
        # exactly that -- so the timestamp is pushed into the next tick rather than left
        # ambiguous. Nothing reads this time as a time; it only has to differ.
        status = path.stat()
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))


def record_stamp() -> str:
    """What the record looks like from outside, as "has it changed?" and nothing more.

    The modification time and the length, not the contents: this is asked on every plugin
    lookup in every process, and a `stat` is one cheap syscall where reading and parsing the
    file is several. ``write_record`` guarantees the pair moves whenever the record does.

    Empty when there is no record, or when the settings are not usable yet, which are both
    "nothing installed" as far as a caller is concerned.
    """
    try:
        status = record_path().stat()
    except (OSError, ImproperlyConfigured):
        return ""
    return f"{status.st_mtime_ns}:{status.st_size}"


def installed(name: str) -> Installed | None:
    canonical = canonicalise(name)
    for entry in read_record():
        if canonicalise(entry.name) == canonical:
            return entry
    return None


def disabled_names() -> set[str]:
    """Canonical names of plugins the administrator has switched off."""
    return {canonicalise(entry.name) for entry in read_record() if entry.disabled}


def canonicalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


# -------------------------------------------------------------- reading a wheel


#: `Project-URL` labels that name where the code lives, best first. Projects label these
#: however they like, so the list is a preference and the fallback is "whatever there is".
SOURCE_URL_LABELS = ("source", "source code", "repository", "code", "homepage", "home")


def _source_url(headers) -> str:
    """Where a plugin's code lives, from whichever field its build backend used.

    `Home-page` is setuptools' old `url=`, and asking only for that found nothing on any
    modern wheel: `[project.urls]` in `pyproject.toml` becomes `Project-URL` instead. This
    was not theoretical — Postulo's own reference plugin declares

        [project.urls]
        Homepage = "https://source.tiagoagueda.com/postulo/postulo-helloworld"

    is built with hatchling, and so shipped no source link at all.
    """
    offered: dict[str, str] = {}
    for entry in headers.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if url.strip():
            offered.setdefault(label.strip().lower(), url.strip())
    for label in SOURCE_URL_LABELS:
        if label in offered:
            return offered[label]
    # Nothing recognised, so the first URL the project offered beats no URL at all.
    return next(iter(offered.values()), "") or headers.get("Home-page", "") or ""


def _author(headers) -> str:
    """Who wrote it, preferring the field that carries a name *and* an address.

    `Author-email` holds ``First Last <address>``; `Author` holds a bare name. Trying
    `Author` first — which this did — picked the less informative of the two whenever a
    package set both.
    """
    return headers.get("Author-email", "") or headers.get("Author", "") or ""


def read_wheel(path: Path) -> PackageInfo:
    """What the wheel says about itself. Nothing is installed and no code is run."""
    if not zipfile.is_zipfile(path):
        raise InstallError(str(_("That is not a wheel: Postulo installs .whl files.")))
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        metadata_name = _one(names, r"[^/]+\.dist-info/METADATA")
        if metadata_name is None:
            raise InstallError(str(_("The wheel has no metadata; it may be damaged.")))
        headers = Parser().parsestr(wheel.read(metadata_name).decode("utf-8", "replace"))
        wheel_name = _one(names, r"[^/]+\.dist-info/WHEEL")
        tags = []
        if wheel_name:
            tags = (
                Parser().parsestr(wheel.read(wheel_name).decode("utf-8", "replace")).get_all("Tag")
                or []
            )
        entry_points = []
        points_name = _one(names, r"[^/]+\.dist-info/entry_points\.txt")
        if points_name:
            entry_points = _plugin_entry_points(wheel.read(points_name).decode("utf-8", "replace"))

    return PackageInfo(
        name=headers.get("Name", "") or path.stem,
        version=headers.get("Version", ""),
        summary=headers.get("Summary", "") or "",
        licence=headers.get("License-Expression") or headers.get("License", "") or "",
        author=_author(headers),
        source_url=_source_url(headers),
        requires_python=headers.get("Requires-Python", "") or "",
        requires=[
            value for value in (headers.get_all("Requires-Dist") or []) if "extra ==" not in value
        ],
        entry_points=entry_points,
        pure_python=any(PURE_PYTHON in tag for tag in tags)
        or path.name.endswith(f"-{PURE_PYTHON}.whl"),
        filename=path.name,
        sha256=digest_of(path),
    )


def _one(names: list[str], pattern: str) -> str | None:
    for name in names:
        if re.fullmatch(pattern, name):
            return name
    return None


def _plugin_entry_points(text: str) -> list[str]:
    """``group:name`` for every Postulo entry point the package declares."""
    found: list[str] = []
    groups = plugin_groups()
    group = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1].strip()
        elif line and "=" in line and group in groups:
            found.append(f"{group}:{line.split('=', 1)[0].strip()}")
    return found


def digest_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# --------------------------------------------------------------- the constraint


def pin_owners(exclude: str = "") -> dict[str, str]:
    """``package -> what pins it``: a plugin's name, or ``""`` for Postulo itself.

    Two things pin a version in this environment. Postulo's own dependencies, which a
    plugin may never move -- that check predates this. And **the other installed plugins**,
    which nothing accounted for at all: installing one could pull a shared dependency to a
    version another plugin could not use, and nothing refused it and nothing said it had
    happened. The other plugin simply started failing, at some later import, in a way
    nobody could trace back to the install that caused it (#246).

    ``exclude`` is the package being installed. Its own pins are left out, or an upgrade
    would be refused for moving the very versions it exists to move -- including its own:
    the plugins directory goes on `sys.path` at install time, so `postulo-example==1.0` was
    in the constraint file while 2.0 was being installed over it.
    """
    skip = canonicalise(exclude) if exclude else ""
    owners: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if name and distribution.version:
            owners[canonicalise(name)] = ""

    mine: set[str] = set()
    for entry in read_record():
        canonical = canonicalise(entry.name)
        pinned = [f"{entry.name}=={entry.version}", *entry.dependencies]
        for pin in pinned:
            package = canonicalise(pin.partition("==")[0])
            if canonical == skip:
                mine.add(package)
            else:
                owners[package] = entry.name

    # A package this plugin brought and nobody else claims is this plugin's to move.
    for package in mine:
        if owners.get(package) == "":
            owners.pop(package, None)
    owners.pop(skip, None)
    return owners


def constraints(exclude: str = "") -> list[str]:
    """Every package that is pinned here, and at what. ``exclude`` may move freely.

    Postulo's own environment, plus every *other* installed plugin and everything each of
    them brought with it. Handed to the installer, so a plugin that needs a version another
    plugin has pinned is refused at install rather than discovered later by whichever of
    the two breaks first.
    """
    owners = pin_owners(exclude)
    versions: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if name and distribution.version and canonicalise(name) in owners:
            versions[canonicalise(name)] = distribution.version
    for entry in read_record():
        if entry.name and owners.get(canonicalise(entry.name)) == entry.name:
            versions[canonicalise(entry.name)] = entry.version
        for pin in entry.dependencies:
            package, _sep, version = pin.partition("==")
            if version and owners.get(canonicalise(package)) == entry.name:
                versions[canonicalise(package)] = version
    return sorted(f"{package}=={version}" for package, version in versions.items())


def explain_conflict(message: str, exclude: str = "") -> str:
    """Name the plugin whose pin a refusal is about, where the refusal named a package.

    A resolver says *"cannot install x==2.0 and x==1.0"* and stops there, which leaves an
    administrator with a package name and no idea which of their plugins cares about it.
    The constraint file is built here, so who pinned what is known here (#246).
    """
    owners = pin_owners(exclude)
    named = sorted(
        {
            owner
            for package, owner in owners.items()
            if owner and re.search(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", message, re.I)
        }
    )
    if not named:
        return message
    return message + " " + str(_("Pinned by: %(plugins)s.")) % {"plugins": ", ".join(named)}


def conflicts_with_core(info: PackageInfo) -> list[str]:
    """Requirements that name a package already pinned here, at a version it is not pinned at.

    Checked before the installer runs so the refusal names the package rather than quoting
    a resolver -- and, since #246, names *who* holds the pin. That used to be Postulo by
    definition, because Postulo's environment was the whole of the constraint; now another
    plugin can hold one, and "Postulo has 1.0" would be a wrong answer to "why not".
    """
    owners = pin_owners(exclude=info.name)
    have = {}
    for pin in constraints(exclude=info.name):
        name, _sep, version = pin.partition("==")
        have[name] = version
    problems = []
    for requirement in info.requires:
        name = canonicalise(re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0])
        version = have.get(name)
        if version is None:
            continue
        specifier = (
            requirement[len(requirement.split(name, 1)[0]) + len(name) :].split(";")[0].strip()
        )
        pinned = re.fullmatch(r"==\s*([\w.!+-]+)", specifier)
        if pinned and pinned.group(1) != version:
            owner = owners.get(name) or ""
            wording = (
                _("%(package)s: needs %(wanted)s, and %(owner)s has %(have)s.")
                if owner
                else _("%(package)s: needs %(wanted)s, and Postulo has %(have)s.")
            )
            problems.append(
                str(wording)
                % {
                    "package": name,
                    "wanted": pinned.group(1),
                    "have": version,
                    "owner": owner,
                }
            )
    return problems


# ------------------------------------------------------------------ installing


def fits(requirement: str, version: str | None = None) -> bool:
    """Whether this Postulo is one the plugin says it is for.

    ``requirement`` is a version specifier as a catalogue release carries it. Nothing
    declared fits everything: the field is optional and a third party may reasonably not
    know. A specifier that cannot be read raises, because a plugin that *tried* to say
    which Postulo it needs and was misread is not one to install on a guess (#186).
    """
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    from postulo import __version__

    if not (requirement or "").strip():
        return True
    try:
        wanted = SpecifierSet(requirement.strip())
        have = Version(version or __version__)
    except (InvalidSpecifier, InvalidVersion) as error:
        raise ValueError(str(error)) from error
    return wanted.contains(have, prereleases=True)


def compatible(requirement: str) -> bool:
    """`fits`, for a page: a specifier nobody can read counts as not fitting."""
    try:
        return fits(requirement)
    except ValueError:
        return False


def check(info: PackageInfo) -> None:
    """Everything that must be true before a wheel is installed. Raises with the reason."""
    if not info.pure_python:
        raise InstallError(
            str(
                _(
                    "%(name)s is not pure Python. Postulo installs only wheels built for "
                    "any platform (py3-none-any); this image has no compiler."
                )
            )
            % {"name": info.name}
        )
    if not info.is_plugin:
        raise InstallError(
            str(
                _(
                    "%(name)s declares no Postulo entry point, so installing it would do "
                    "nothing. A plugin registers under one of: %(groups)s."
                )
            )
            % {"name": info.name, "groups": ", ".join(plugin_groups())}
        )
    problems = conflicts_with_core(info)
    if problems:
        raise InstallError(
            str(_("%(name)s would change what something here already depends on. %(problems)s"))
            % {"name": info.name, "problems": " ".join(problems)}
        )


def installer() -> list[str]:
    """The command that installs, preferring uv where the image put it.

    The pip branch is for an ordinary installation -- a `pip install postulo` on somebody's
    server, where pip is certainly there and uv may not be. **The container has no pip**
    since #190: it carries uv deliberately, and the copy pip vendors for its own cache was
    the only fixable High the image scan could find. So in the image this always takes the
    first branch, and outside it always takes whichever exists.

    Neither is a real state -- a stripped image somebody built themselves -- and it used to
    produce `No module named pip` from a subprocess, which says nothing about what to do.
    """
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable]
    if importlib.util.find_spec("pip") is None:
        raise InstallError(
            str(
                _(
                    "Nothing here can install a plugin: this environment has neither uv nor "
                    "pip. Postulo's own image ships uv; an image built without it needs one "
                    "of the two on the path."
                )
            )
        )
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]


def distributions_in(directory: Path) -> dict[str, str]:
    """``{name: version}`` for every package sitting in ``directory``.

    Read from the ``.dist-info`` directory names rather than by importing anything, so it
    works before and after an install without a metadata cache getting in the way.
    """
    found: dict[str, str] = {}
    if not directory.is_dir():
        return found
    for dist_info in directory.glob("*.dist-info"):
        stem = dist_info.name[: -len(".dist-info")]
        name, _sep, version = stem.rpartition("-")
        if name:
            found[canonicalise(name)] = version
    return found


def run_install(target: Path, wheel: Path, constraint_file: Path) -> str:
    """Install one wheel into ``target``. Returns whatever the installer said."""
    command = [
        *installer(),
        "--target",
        str(target),
        "--constraint",
        str(constraint_file),
        # Wheels only. A source distribution runs its own build code during installation,
        # as the container's user, and installing a plugin already means installing
        # whatever its requirements resolve to on PyPI that day -- so the one thing worth
        # refusing outright is a dependency that gets to execute before anybody has seen
        # what it is. A plugin genuinely needing a source build is one an operator should
        # install by hand, deliberately.
        "--only-binary",
        ":all:",
        "--upgrade",
        str(wheel),
    ]
    try:
        finished = subprocess.run(  # noqa: S603 - the argument list is built here, not typed
            command,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InstallError(
            str(_("The installer could not be run: %(error)s")) % {"error": error}
        ) from error
    if finished.returncode != 0:
        raise InstallError(
            str(_("The installer refused it: %(error)s"))
            % {"error": (finished.stderr or finished.stdout or "").strip()[-800:]}
        )
    return (finished.stdout or "").strip()


# ------------------------------------------------------- a way back, and a check


def previous_dir() -> Path:
    """Where the plugins directory as it was before the last install is kept."""
    return plugins_dir() / PREVIOUS_NAME


def _copyable(directory: Path) -> list[Path]:
    return [item for item in directory.iterdir() if item.name not in NOT_SNAPSHOTTED]


def take_snapshot() -> None:
    """Keep the directory as it is now, so the next install has something to go back to.

    The whole directory rather than one plugin's files, because a plugin's dependencies are
    installed beside it and an upgrade moves those too: putting back only the wheel would
    leave the new versions of everything it dragged in, which is not the state that worked.

    One snapshot, replaced each time. Keeping a chain would mean deciding how long to keep
    it and how much of somebody's volume to spend on it; one covers what an administrator
    actually asks for, which is to undo the thing they just did.

    The snapshot that was there is *held* rather than thrown away, until the install it is
    being replaced for has worked. An attempt that fails puts the directory back and gives
    the old snapshot back with it, so a failed upgrade costs nothing: without that, trying
    a bad wheel would silently spend the way back to the version before the one running.
    """
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    previous, held = previous_dir(), directory / HELD_NAME
    shutil.rmtree(held, ignore_errors=True)
    if previous.is_dir():
        previous.rename(held)

    scratch = directory / ".previous.writing"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    for item in _copyable(directory):
        if item.name == scratch.name:
            continue
        if item.is_dir():
            shutil.copytree(item, scratch / item.name, symlinks=True)
        else:
            shutil.copy2(item, scratch / item.name)
    # Swapped into place at the end, so an interrupted copy never becomes the thing an
    # administrator rolls back to.
    shutil.rmtree(previous, ignore_errors=True)
    scratch.rename(previous)


def keep_snapshot() -> None:
    """The install worked: the snapshot from before it is no longer the way back."""
    shutil.rmtree(plugins_dir() / HELD_NAME, ignore_errors=True)


def give_back_snapshot() -> None:
    """The install failed: the way back is where it was before the attempt."""
    directory = plugins_dir()
    held = directory / HELD_NAME
    if not held.is_dir():
        # Nothing was held, so this was the first install: before it there was no plugin,
        # and the snapshot just taken is still the right answer to "go back".
        return
    shutil.rmtree(previous_dir(), ignore_errors=True)
    held.rename(previous_dir())


def restore_snapshot() -> bool:
    """Put the directory back as `take_snapshot` left it. False if there is nothing kept."""
    previous = previous_dir()
    if not previous.is_dir():
        return False
    directory = plugins_dir()
    for item in _copyable(directory):
        if item.name == PREVIOUS_NAME:
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
    for item in previous.iterdir():
        if item.is_dir():
            shutil.copytree(item, directory / item.name, symlinks=True)
        else:
            shutil.copy2(item, directory / item.name)
    _forget_metadata_cache()
    return True


def can_roll_back() -> bool:
    return previous_dir().is_dir()


def _tidy_snapshots() -> None:
    """Take away whatever an interrupted snapshot left, so the next one starts clean."""
    directory = plugins_dir()
    for name in (".previous.writing", HELD_NAME):
        shutil.rmtree(directory / name, ignore_errors=True)


def roll_back() -> list[Installed]:
    """Undo the last install. Returns the record as it was before it.

    For the install that succeeded and turned out to be wrong -- a plugin that imports
    cleanly and then does the wrong thing, which no check here could have caught. The
    snapshot is spent afterwards: there is one, and rolling back twice would be rolling
    back to a state nobody kept.
    """
    if not can_roll_back():
        raise InstallError(
            str(_("There is nothing to go back to: no plugin has been installed since."))
        )
    restore_snapshot()
    shutil.rmtree(previous_dir(), ignore_errors=True)
    _tidy_snapshots()
    activate()
    _forget_metadata_cache()
    return read_record()


#: The program the check below runs. It loads the entry points exactly as the registry
#: does -- by group, from the directory that was just installed into -- and imports what
#: each one names. Printing nothing and exiting 0 is the whole of "it loads".
_VERIFY = """
import sys, site
site.addsitedir(sys.argv[1])
from importlib.metadata import entry_points
wanted = set(sys.argv[2].split(",")) if sys.argv[2] else set()
failed = []
for group in sys.argv[3].split(","):
    for point in entry_points(group=group):
        if f"{group}:{point.name}" not in wanted:
            continue
        try:
            point.load()
        except BaseException as error:
            failed.append(f"{point.name}: {type(error).__name__}: {error}")
for line in failed:
    print(line)
sys.exit(1 if failed else 0)
"""


def verify_imports(names: list[str]) -> list[str]:
    """Import every entry point ``names`` lists, in a separate process. What broke, if any.

    **In a subprocess on purpose.** Importing somebody else's module runs somebody else's
    code, and there is no way to unimport it: doing this in the worker would leave whatever
    a half-loaded module did behind, in the process that then has to carry on serving. A
    module that calls `sys.exit` or exhausts memory takes the subprocess with it and
    nothing else, which is the same reason `registry` catches `BaseException` (#228).

    An empty list means every entry point loaded. It is only ever asked about the plugin
    just installed, so a plugin that was already broken is not this install's failure.
    """
    if not names:
        return []
    groups = ",".join(sorted({point.split(":", 1)[0] for point in names}))
    try:
        finished = subprocess.run(  # noqa: S603 - the argument list is built here
            # `-B`: checking that a plugin imports should not leave anything behind,
            # and an import writes `__pycache__` beside the source unless told not to.
            [sys.executable, "-B", "-c", _VERIFY, str(plugins_dir()), ",".join(names), groups],
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [
            str(_("it did not finish importing within %(seconds)ss")) % {"seconds": VERIFY_TIMEOUT}
        ]
    except (OSError, subprocess.SubprocessError) as error:
        # The check could not be run, which is not the same as the plugin being broken.
        # Saying so and going on beats refusing an install over the checker's own trouble.
        logger.warning("Could not check that the plugin imports: %s", error)
        return []
    if finished.returncode == 0:
        return []
    said = (finished.stdout or finished.stderr or "").strip().splitlines()
    return said or [str(_("it could not be imported"))]


def install_wheel(
    wheel: Path,
    *,
    origin: str = "upload",
    source: str = "",
    by: str = "",
    expected_sha256: str = "",
    requires_postulo: str = "",
    disabled: bool = False,
    installed_at: str = "",
) -> Installed:
    """Check a wheel, install it into the plugins directory, and record it.

    ``disabled`` and ``installed_at`` are for a restore rather than for an install: `sync`
    puts a plugin back after an upgrade and has to put back the state it was in, not a
    fresh one. An install leaves both alone, which is enabled and now.
    """
    info = read_wheel(wheel)
    if expected_sha256 and info.sha256 != expected_sha256:
        raise InstallError(
            str(_("%(name)s does not match the checksum it was published with."))
            % {"name": info.name}
        )
    check(info)

    target = plugins_dir()
    target.mkdir(parents=True, exist_ok=True)
    before = distributions_in(target)
    # Kept before anything is written, because what this is protecting against is an
    # upgrade: `--target --upgrade` writes over the working version, and until #246 there
    # was nothing to go back to once it had.
    take_snapshot()
    constraint_file = target / ".constraints.txt"
    constraint_file.write_text("\n".join(constraints(exclude=info.name)) + "\n", encoding="utf-8")
    try:
        run_install(target, wheel, constraint_file)
    except InstallError as error:
        restore_snapshot()
        give_back_snapshot()
        raise InstallError(explain_conflict(str(error), exclude=info.name)) from error
    finally:
        constraint_file.unlink(missing_ok=True)

    # It is on the volume; whether it *loads* is a different question, and the one that
    # matters. Asked before the record names it, so a plugin that cannot be imported is a
    # refused install rather than an instance with a line in its record and nothing behind
    # it (#246).
    _forget_metadata_cache()
    if broken := verify_imports(info.entry_points):
        restore_snapshot()
        give_back_snapshot()
        activate()
        _forget_metadata_cache()
        raise InstallError(
            str(_("%(name)s installed but could not be loaded, so it was put back: %(why)s"))
            % {"name": info.name, "why": " ".join(broken)}
        )

    # What the installer actually brought, as opposed to what the wheel asked for. The
    # two differ: a requirement of a requirement never appears in the wheel's metadata.
    after = distributions_in(target)
    arrived = sorted(
        f"{name}=={version}"
        for name, version in after.items()
        if canonicalise(name) != canonicalise(info.name) and before.get(name) != version
    )

    entry = Installed(
        name=info.name,
        version=info.version,
        origin=origin,
        source=source or info.filename,
        sha256=info.sha256,
        installed_at=installed_at or dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        installed_by=by,
        entry_points=info.entry_points,
        disabled=disabled,
        dependencies=arrived,
        summary=info.summary,
        licence=info.licence,
        author=info.author,
        source_url=info.source_url,
        requires_postulo=requires_postulo,
    )
    record = [item for item in read_record() if canonicalise(item.name) != canonicalise(info.name)]
    write_record([*record, entry])
    keep_snapshot()
    activate()
    _forget_metadata_cache()
    return entry


def is_internal(name: str) -> bool:
    """Whether ``name`` is a plugin that ships inside Postulo rather than one installed.

    Asked by name, because that is what a form posts and what a command takes. A built-in
    has no line in the record, so every route that removes or disables one is really being
    asked to do something to a plugin it cannot see -- and answering "no such plugin" would
    be true and useless. This is what lets those routes say why instead (#94).
    """
    from . import registry

    return any(
        getattr(plugin_class, "name", "") == name
        for classes in registry.builtins().values()
        for plugin_class in classes
    )


def remove(name: str) -> Installed:
    """Take a plugin off the instance: its files, and its line in the record.

    Refused while anything it owns still holds rows (#128). Not a courtesy check the page
    happens to do first: this is the last door, and a management command or a shell reaching
    `remove()` directly would otherwise leave a table nothing can read.
    """
    from . import data

    if is_internal(name):
        raise InstallError(
            str(_("%(name)s ships inside Postulo and cannot be removed.")) % {"name": name}
        )
    entry = installed(name)
    if entry is None:
        raise InstallError(str(_("%(name)s is not installed.")) % {"name": name})
    if refusal := data.refuse_removing(name):
        raise InstallError(refusal)
    going = [entry.name, *unshared_dependencies(entry)]
    for package in going:
        for path in _paths_of(package):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    _prune_empty_directories()
    write_record([item for item in read_record() if canonicalise(item.name) != canonicalise(name)])
    _forget_metadata_cache()
    return entry


def unshared_dependencies(entry: Installed) -> list[str]:
    """What this plugin brought that nothing else installed is using.

    `remove` used to delete the files the plugin's own ``RECORD`` lists and stop, so
    everything it had dragged in stayed on the volume and stayed importable for ever --
    growing with every plugin ever tried and removed, and quietly satisfying an import in
    a plugin that never declared it (#246).

    Counted from the record's `dependencies`, which is what each install wrote down as
    having arrived with it. A package two plugins both brought is kept while either
    remains; a package Postulo itself provides is not in the plugins directory at all, so
    `_paths_of` finds nothing for it and it cannot be removed by this.
    """
    mine = {canonicalise(pin.partition("==")[0]) for pin in entry.dependencies}
    canonical = canonicalise(entry.name)
    for other in read_record():
        if canonicalise(other.name) == canonical:
            continue
        mine -= {canonicalise(pin.partition("==")[0]) for pin in other.dependencies}
        mine.discard(canonicalise(other.name))
    return sorted(mine)


def _paths_of(name: str) -> list[Path]:
    """Everything that install put in the directory for this distribution."""
    directory = plugins_dir()
    if not directory.is_dir():
        return []
    canonical = canonicalise(name)
    paths: list[Path] = []
    for dist_info in directory.glob("*.dist-info"):
        if canonicalise(dist_info.name.split("-")[0]) != canonical:
            continue
        record = dist_info / "RECORD"
        if record.is_file():
            for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
                relative = line.split(",", 1)[0].strip()
                if not relative or relative.startswith(".."):
                    continue
                candidate = (directory / relative).resolve()
                if directory.resolve() in candidate.parents:
                    paths.append(candidate)
        paths.append(dist_info)
    # Deepest first, so a file goes before the directory holding it.
    return sorted(set(paths), key=lambda path: len(path.parts), reverse=True)


def _prune_empty_directories() -> None:
    """Take away the directories a removed plugin's files were in, once they are empty.

    A directory holding nothing but `__pycache__` counts as empty, because that is a
    directory of compiled copies of files that are gone. Every worker that imported the
    plugin wrote one, and none of them is in the `RECORD` the removal works from -- so
    without this, removing a plugin that had ever been imported left its package directory
    on the volume for ever, and `postulo_example/` looked installed to anyone who looked
    (#246).
    """
    directory = plugins_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir() or path.name in (PREVIOUS_NAME, "__pycache__"):
            continue  # the snapshot is kept on purpose; a cache goes with its own parent
        if not [item for item in path.iterdir() if item.name != "__pycache__"]:
            shutil.rmtree(path, ignore_errors=True)


def set_disabled(name: str, disabled: bool) -> Installed:
    """Stop a plugin loading, or let it load again. Its files stay where they are.

    Not the way a built-in is switched off. `plugins/policy.py` decides those, per person
    and per instance, with a record of who decided -- and this record has no line to write
    on for a plugin that was never installed (#94).
    """
    if is_internal(name):
        raise InstallError(
            str(
                _(
                    "%(name)s ships inside Postulo. Switch it off under "
                    "Server settings → Plugins instead."
                )
            )
            % {"name": name}
        )
    record = read_record()
    for entry in record:
        if canonicalise(entry.name) == canonicalise(name):
            entry.disabled = disabled
            write_record(record)
            # Every other process notices from the record's stamp; this one wrote the
            # record, so its own stamp already matches and nothing would tell it (#228).
            _forget_metadata_cache()
            return entry
    raise InstallError(str(_("%(name)s is not installed.")) % {"name": name})


def backfill_metadata() -> list[str]:
    """Fill in what the record never kept, from the metadata still sitting on the volume.

    Every plugin installed before #97 has a line in ``plugins.json`` with no summary, no
    licence, no author and no source link — they were read from the wheel, shown once on
    the confirmation screen, and dropped. The wheel itself is gone, but the ``.dist-info``
    the installer wrote is right there in the plugins directory, and it carries the same
    headers.

    So an existing instance fills in rather than showing blanks for ever. Returns the names
    it managed to complete. Anything it cannot find is left exactly as it was: a missing
    author is a smaller problem than a wrong one.
    """
    directory = plugins_dir()
    if not directory.is_dir():
        return []

    entries = read_record()
    completed: list[str] = []
    for entry in entries:
        if entry.summary or entry.author or entry.source_url or entry.licence:
            continue
        canonical = canonicalise(entry.name)
        for dist_info in directory.glob("*.dist-info"):
            if canonicalise(dist_info.name.split("-")[0]) != canonical:
                continue
            metadata = dist_info / "METADATA"
            if not metadata.is_file():
                continue
            headers = Parser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"))
            entry.summary = headers.get("Summary", "") or ""
            entry.licence = headers.get("License-Expression") or headers.get("License", "") or ""
            entry.author = _author(headers)
            entry.source_url = _source_url(headers)
            completed.append(entry.name)
            break

    if completed:
        write_record(entries)
    return completed


def sync(*, fetch=None) -> tuple[list[str], list[str]]:
    """Reinstall anything the record lists that the directory no longer has.

    This is what makes an upgrade safe: the image changes, the volume does not, and the
    entry point calls this before the first request. ``fetch`` is how a catalogue plugin
    is fetched again; without it, only what can be found locally is restored.
    """
    activate()
    # An upgrade is exactly when a record written by an older Postulo is read by a newer
    # one, so it is the right moment to complete it.
    backfill_metadata()
    restored: list[str] = []
    lost: list[str] = []
    for entry in read_record():
        if _is_present(entry.name):
            continue
        wheel = None
        if fetch is not None:
            try:
                wheel = fetch(entry)
            except Exception:
                # The caller is told the plugin is lost; the log is the only place the
                # reason can go, and "the catalogue no longer lists that version" is
                # exactly the reason somebody will want.
                logger.exception("%s could not be fetched to restore it", entry.name)
                wheel = None
        if wheel is None:
            lost.append(entry.name)
            continue
        try:
            # Every field the record kept, because this is a restore: a plugin the
            # administrator had switched off must come back switched off -- it was
            # switched off for a reason, and an upgrade is not somebody changing their
            # mind -- and the compatibility marker the catalogue declared is not
            # something the wheel can say, so dropping it made a plugin that no longer
            # fits this Postulo look as though it had never been asked (#228).
            install_wheel(
                wheel,
                origin=entry.origin,
                source=entry.source,
                by=entry.installed_by,
                expected_sha256=entry.sha256,
                requires_postulo=entry.requires_postulo,
                disabled=entry.disabled,
                installed_at=entry.installed_at,
            )
            restored.append(entry.name)
        except InstallError:
            lost.append(entry.name)
    return restored, lost


def _is_present(name: str) -> bool:
    # The files may have changed since anything last looked — an upgrade, a removal — so
    # ask the import machinery to look again rather than trusting what it remembers.
    metadata.MetadataPathFinder.invalidate_caches()
    canonical = canonicalise(name)
    directory = plugins_dir()
    if directory.is_dir():
        for dist_info in directory.glob("*.dist-info"):
            if canonicalise(dist_info.name.split("-")[0]) == canonical:
                return True
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def _forget_metadata_cache() -> None:
    """Make the next look at entry points see what was just installed or removed.

    Every kind, asked of the registry. Four of them were named here by hand, so installing
    a transport or a feature left the process that installed it holding a list from before
    the install -- and the plugin the administrator had just watched arrive was not there
    until something else happened to refresh it (#228).
    """
    from .registry import GROUPS
    from .registry import plugins as registry_plugins

    metadata.MetadataPathFinder.invalidate_caches()
    for kind in GROUPS:
        try:
            registry_plugins(kind, refresh=True)
        except Exception:  # pragma: no cover - the registry logs the plugin that broke
            logger.exception("Refreshing the %s plugins failed after an install", kind)


def status() -> list[dict]:
    """Everything this instance can do, and where each part of it came from.

    The record *and the built-ins*, because until #94 this page listed what an
    administrator had installed and not what the instance could actually do -- so somebody
    asking "can this instance read a posting off a page" was looking in the wrong place.
    The two built-in sources never pass through here; they are Python classes in the image.

    Provenance is derived rather than stored: an upload is checked against what the enabled
    repositories currently sign, so a file that matches one is named as theirs however it
    arrived. The checksums are fetched once for the whole list rather than once per row.
    """
    from . import provenance, registry

    rows = []
    for plugin_class in _every_builtin(registry):
        mark = provenance.of_builtin(plugin_class)
        # The same keys an installed row has, filled in with what is true of a built-in:
        # no checksum, nobody installed it, no date. One shape means every reader of this
        # list -- the page, the command, a future one -- works on both kinds (#94).
        rows.append(
            {
                **asdict(Installed(name=getattr(plugin_class, "name", ""), version="")),
                "version": str(getattr(plugin_class, "version", "") or ""),
                "origin": "internal",
                "present": True,
                "compatible": True,
                "removable": mark.removable,
                "provenance": mark.kind,
                "provenance_label": mark.label,
                "provenance_explanation": mark.explanation,
                "repository": "",
                "summary": str(getattr(plugin_class, "description", "") or ""),
            }
        )

    entries = read_record()
    digests = provenance.signed_digests() if entries else {}
    for entry in entries:
        mark = provenance.of_record(entry, digests=digests)
        rows.append(
            {
                **asdict(entry),
                "present": _is_present(entry.name),
                # Asked again on every page, not only at install: a core upgrade is what
                # changes the answer, and the page is where it should show (#186).
                "compatible": compatible(entry.requires_postulo),
                "removable": mark.removable,
                "provenance": mark.kind,
                "provenance_label": mark.label,
                "provenance_explanation": mark.explanation,
                "repository": mark.repository,
            }
        )
    return rows


def _every_builtin(registry) -> list:
    """One instance of each plugin Postulo ships, whatever kind it is."""
    seen: list = []
    for classes in registry.builtins().values():
        for plugin_class in classes:
            try:
                seen.append(plugin_class())
            except Exception:  # pragma: no cover - a built-in that will not instantiate
                logger.exception("Built-in plugin %r could not be described", plugin_class)
    return sorted(seen, key=lambda plugin: getattr(plugin, "name", ""))
