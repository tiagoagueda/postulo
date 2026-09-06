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

Installing a plugin is running somebody else's code inside Postulo, with everything
Postulo can do. Nothing here pretends otherwise; the page that calls it says so plainly,
only administrators reach it, and a plugin can be switched off without being removed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
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
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

#: The file on the volume that says what is installed. Its absence means "nothing".
RECORD_NAME = "plugins.json"
#: Only wheels, and only pure-Python ones.
PURE_PYTHON = "py3-none-any"
#: Entry-point groups that make a package a Postulo plugin.
PLUGIN_GROUPS = ("postulo.sources", "postulo.notifiers", "postulo.stores", "postulo.syncs")
#: How long an install may take before it is called a failure.
INSTALL_TIMEOUT = 300


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
    home_page: str = ""
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


def read_record() -> list[Installed]:
    path = record_path()
    if not path.is_file():
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
    return found


def write_record(entries: list[Installed]) -> None:
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "plugins": [asdict(entry) for entry in sorted(entries, key=lambda e: e.name)],
    }
    record_path().write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


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
        author=headers.get("Author", "") or headers.get("Author-email", "") or "",
        home_page=headers.get("Home-page", "") or "",
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
    group = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1].strip()
        elif line and "=" in line and group in PLUGIN_GROUPS:
            found.append(f"{group}:{line.split('=', 1)[0].strip()}")
    return found


def digest_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# --------------------------------------------------------------- the constraint


def constraints() -> list[str]:
    """Every package in the running environment, pinned. A plugin may not move any of them."""
    pins = []
    for distribution in metadata.distributions():
        name = distribution.metadata["Name"]
        if name and distribution.version:
            pins.append(f"{canonicalise(name)}=={distribution.version}")
    return sorted(set(pins))


def conflicts_with_core(info: PackageInfo) -> list[str]:
    """Requirements that name one of Postulo's own packages at a version it does not have.

    Checked before the installer runs so the refusal names the package rather than
    quoting a resolver.
    """
    have = {}
    for pin in constraints():
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
            problems.append(
                str(_("%(package)s: needs %(wanted)s, and Postulo has %(have)s."))
                % {"package": name, "wanted": pinned.group(1), "have": version}
            )
    return problems


# ------------------------------------------------------------------ installing


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
            % {"name": info.name, "groups": ", ".join(PLUGIN_GROUPS)}
        )
    problems = conflicts_with_core(info)
    if problems:
        raise InstallError(
            str(_("%(name)s would change what Postulo itself depends on. %(problems)s"))
            % {"name": info.name, "problems": " ".join(problems)}
        )


def installer() -> list[str]:
    """The command that installs, preferring uv where the image put it."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable]
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]


def run_install(target: Path, wheel: Path, constraint_file: Path) -> str:
    """Install one wheel into ``target``. Returns whatever the installer said."""
    command = [
        *installer(),
        "--target",
        str(target),
        "--constraint",
        str(constraint_file),
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


def install_wheel(
    wheel: Path,
    *,
    origin: str = "upload",
    source: str = "",
    by: str = "",
    expected_sha256: str = "",
) -> Installed:
    """Check a wheel, install it into the plugins directory, and record it."""
    info = read_wheel(wheel)
    if expected_sha256 and info.sha256 != expected_sha256:
        raise InstallError(
            str(_("%(name)s does not match the checksum it was published with."))
            % {"name": info.name}
        )
    check(info)

    target = plugins_dir()
    target.mkdir(parents=True, exist_ok=True)
    constraint_file = target / ".constraints.txt"
    constraint_file.write_text("\n".join(constraints()) + "\n", encoding="utf-8")
    try:
        run_install(target, wheel, constraint_file)
    finally:
        constraint_file.unlink(missing_ok=True)

    entry = Installed(
        name=info.name,
        version=info.version,
        origin=origin,
        source=source or info.filename,
        sha256=info.sha256,
        installed_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        installed_by=by,
        entry_points=info.entry_points,
        disabled=False,
    )
    record = [item for item in read_record() if canonicalise(item.name) != canonicalise(info.name)]
    write_record([*record, entry])
    activate()
    _forget_metadata_cache()
    return entry


def remove(name: str) -> Installed:
    """Take a plugin off the instance: its files, and its line in the record."""
    entry = installed(name)
    if entry is None:
        raise InstallError(str(_("%(name)s is not installed.")) % {"name": name})
    for path in _paths_of(entry.name):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    _prune_empty_directories()
    write_record([item for item in read_record() if canonicalise(item.name) != canonicalise(name)])
    _forget_metadata_cache()
    return entry


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
    """Take away the directories a removed plugin's files were in, once they are empty."""
    directory = plugins_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def set_disabled(name: str, disabled: bool) -> Installed:
    """Stop a plugin loading, or let it load again. Its files stay where they are."""
    record = read_record()
    for entry in record:
        if canonicalise(entry.name) == canonicalise(name):
            entry.disabled = disabled
            write_record(record)
            return entry
    raise InstallError(str(_("%(name)s is not installed.")) % {"name": name})


def sync(*, fetch=None) -> tuple[list[str], list[str]]:
    """Reinstall anything the record lists that the directory no longer has.

    This is what makes an upgrade safe: the image changes, the volume does not, and the
    entry point calls this before the first request. ``fetch`` is how a catalogue plugin
    is fetched again; without it, only what can be found locally is restored.
    """
    activate()
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
                wheel = None
        if wheel is None:
            lost.append(entry.name)
            continue
        try:
            install_wheel(
                wheel,
                origin=entry.origin,
                source=entry.source,
                by=entry.installed_by,
                expected_sha256=entry.sha256,
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
    """Make the next look at entry points see what was just installed or removed."""
    from .registry import plugins as registry_plugins

    metadata.MetadataPathFinder.invalidate_caches()
    for kind in ("source", "notifier", "store", "sync"):
        try:
            registry_plugins(kind, refresh=True)
        except Exception:  # pragma: no cover - the registry logs the plugin that broke
            logger.exception("Refreshing the %s plugins failed after an install", kind)


def status() -> list[dict]:
    """The record, with whether each plugin is actually importable right now."""
    return [{**asdict(entry), "present": _is_present(entry.name)} for entry in read_record()]
