"""Holding a plugin where it is, or pinning it to a version (#1264).

An administrator decides, per plugin, one of three things: **follow the latest** (no row, the
default), **hold** it at the version installed now, or **pin** it to a version of their
choice. A held or pinned plugin is skipped by every automatic update and by *Update all*; a
newer release is still shown, as information, beside the word "held". It can be moved by hand,
after the page says it is held. A **security release** is never applied behind a hold, but the
page says one is there, with its advisory, so that holding is not a way to miss one.

Where it lives: a row per plugin (`PluginHold`), on the instance, so a restart and an upgrade
of Postulo leave it alone. ``POSTULO_PLUGIN_PINS`` (``name==version``, comma-separated) pins
from the environment for people who deploy from a file. The environment wins, and the page
shows such a pin without a way to change it, as it does a pinned catalogue.

Pinning *installs* the version, through exactly the checks any install makes (`catalogue.install`:
signature, compatibility, SHA-256, dependants, snapshot and rollback), down- or up-grade. This
module decides nothing about whether a wheel is trusted.

Every change is written to the instance's log under ``postulo.plugins.audit``, with the
administrator's name: that is the audit trail, as for the other decisions on the Plugins page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils.translation import gettext as _

from .record import canonicalise

audit = logging.getLogger("postulo.plugins.audit")

HOLD = "hold"
PIN = "pin"


class HoldError(Exception):
    """A hold could not be set or lifted, in words an administrator can read."""


@dataclass(frozen=True)
class Hold:
    """What is decided about one plugin."""

    plugin: str
    mode: str
    version: str
    note: str = ""
    set_by: str = ""
    set_at: datetime | None = None
    #: ``"environment"`` for ``POSTULO_PLUGIN_PINS``, which the page cannot change; ``"page"``.
    source: str = "page"

    @property
    def from_environment(self) -> bool:
        return self.source == "environment"


def environment() -> dict[str, str]:
    """What ``POSTULO_PLUGIN_PINS`` pins: canonical name to version (bad entries dropped)."""
    found: dict[str, str] = {}
    for part in (getattr(settings, "POSTULO_PLUGIN_PINS", "") or "").split(","):
        name, separator, version = part.strip().partition("==")
        if separator and name.strip() and version.strip():
            found[canonicalise(name.strip())] = version.strip()
    return found


def _rows() -> dict[str, Hold]:
    from .models import PluginHold

    try:
        rows = list(PluginHold.objects.select_related("set_by"))
    except Exception:  # pragma: no cover - before migrate, and never at runtime
        return {}
    return {
        canonicalise(row.plugin): Hold(
            plugin=row.plugin,
            mode=row.mode,
            version=row.version,
            note=row.note,
            set_by=row.set_by.get_username() if row.set_by else "",
            set_at=row.set_at,
        )
        for row in rows
    }


def all_holds() -> dict[str, Hold]:
    """Every plugin that is held or pinned, by canonical name. The environment over the rows."""
    found = _rows()
    for name, version in environment().items():
        found[name] = Hold(plugin=name, mode=PIN, version=version, note="", source="environment")
    return found


def get(name: str) -> Hold | None:
    return all_holds().get(canonicalise(name))


def is_held(name: str, holds: dict[str, Hold] | None = None) -> bool:
    """Whether automatic and bulk updates must leave this plugin alone."""
    return canonicalise(name) in (all_holds() if holds is None else holds)


def refuse_other_than_environment_pin(name: str, version: str) -> str:
    """A sentence when the environment pins ``name`` to another version than ``version``."""
    wanted = environment().get(canonicalise(name))
    if wanted is None or wanted == version:
        return ""
    return str(
        _(
            "POSTULO_PLUGIN_PINS pins %(name)s to %(wanted)s, so %(version)s is not installed "
            "from here. Change the pin where it is set."
        )
        % {"name": name, "wanted": wanted, "version": version}
    )


def _log(action: str, name: str, by: str, **details) -> None:
    audit.warning(
        "Plugin %s: %s by %s%s",
        name,
        action,
        by or "unknown",
        "".join(f" ({key}: {value})" for key, value in details.items() if value),
        extra={"event": f"plugin_{action}", "plugin": name, "actor": by},
    )


def refuse_if_environment_pins(name: str) -> None:
    if canonicalise(name) in environment():
        raise HoldError(
            str(
                _(
                    "POSTULO_PLUGIN_PINS pins “%(name)s”, so it cannot be changed here. "
                    "Change it where the environment is set."
                )
            )
            % {"name": name}
        )


def hold(name: str, *, by=None, note: str = "") -> Hold:
    """Keep ``name`` at the version installed now. It must be installed from a catalogue or file."""
    from .models import PluginHold
    from .record import installed

    refuse_if_environment_pins(name)
    entry = installed(name)
    if entry is None:
        raise HoldError(
            str(_("“%(name)s” is not installed here, so there is no version to hold."))
            % {"name": name}
        )
    return _save(PluginHold.Mode.HOLD, entry.name, entry.version, by, note, "held")


def lift(name: str, *, by=None) -> None:
    """Follow the latest again."""
    from .models import PluginHold

    refuse_if_environment_pins(name)
    row = PluginHold.objects.filter(plugin=canonicalise(name)).first()
    if row is None:
        raise HoldError(str(_("“%(name)s” is not held.")) % {"name": name})
    row.delete()
    _log("lifted", name, who(by), version=row.version)


def record_pin(name: str, version: str, *, by=None, note: str = "") -> Hold:
    """Keep ``name`` on ``version``, which has just been installed (see `catalogue.pin`)."""
    from .models import PluginHold

    refuse_if_environment_pins(name)
    return _save(PluginHold.Mode.PIN, name, version, by, note, "pinned")


def follow_manual_install(name: str, version: str, *, by=None) -> None:
    """After an administrator moved a held or pinned plugin by hand: the hold follows it.

    The hold is about the version the administrator chose, and they have now chosen another;
    it is kept at that one rather than lifted, because "do not move it by itself" is still
    what they asked for.
    """
    from .models import PluginHold

    row = PluginHold.objects.filter(plugin=canonicalise(name)).first()
    if row is None or row.version == version:
        return
    before, row.version = row.version, version
    row.save(update_fields=["version", "set_at"])
    _log("moved by hand", name, who(by), was=before, now=version)


def who(by) -> str:
    if by is None:
        return ""
    return by if isinstance(by, str) else by.get_username()


def _save(mode: str, name: str, version: str, by, note: str, verb: str) -> Hold:
    from .models import PluginHold

    user = by if by is not None and not isinstance(by, str) else None
    row, _made = PluginHold.objects.update_or_create(
        plugin=canonicalise(name),
        defaults={"mode": mode, "version": version, "note": note[:300], "set_by": user},
    )
    _log(verb, name, who(by), version=version, why=note)
    return Hold(
        plugin=row.plugin,
        mode=row.mode,
        version=row.version,
        note=row.note,
        set_by=who(by),
        set_at=row.set_at,
    )


# ---------------------------------------------------------- what the page says


def newer(release_version: str, installed_version: str) -> bool:
    """Whether a release is later than the installed version, by PEP 440 order."""
    from packaging.version import InvalidVersion, Version

    try:
        return Version(release_version) > Version(installed_version)
    except InvalidVersion:
        return False


def security_release(listing, installed_version: str):
    """The newest release past ``installed_version`` signed as a security fix, or ``None``."""
    found = None
    for release in listing.releases:
        if release.security and newer(release.version, installed_version):
            if found is None or newer(release.version, found.version):
                found = release
    return found


def incompatible_with(postulo_version: str) -> list[tuple[str, str, str]]:
    """The held or pinned plugins whose release does not declare this Postulo: name, version, range.

    For before an upgrade of Postulo: a plugin the administrator froze is the one nothing
    would otherwise move to a release that fits, so it is said before and not found at import
    (#186). The range is what the installed release declared when it was installed.
    """
    from .installing import fits
    from .record import installed

    found = []
    for name in sorted(all_holds()):
        entry = installed(name)
        if entry is None:
            continue
        try:
            fine = fits(entry.requires_postulo, postulo_version)
        except ValueError:
            fine = False
        if not fine:
            found.append((entry.name, entry.version, entry.requires_postulo))
    return found
