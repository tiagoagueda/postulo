"""Telling an administrator that a plugin has an update, and applying it (#1187).

Three things, and the first two are about *knowing*:

* **A check, once a day**, fetches each configured catalogue's signed index -- nothing is
  downloaded and nothing is installed -- and keeps what each said (`CatalogueAnswer`). It runs
  in the scheduler's pass (on the worker, where there is one) and, where no scheduler is
  running, at most once a day when an administrator opens a Server page. An administrator
  can switch it off on the Plugins page; ``POSTULO_PLUGIN_UPDATE_CHECK`` wins when set. The
  network is touched on that schedule or on a press of *Check for updates*, and nowhere else.
* **Telling**: the Server overview and the Plugins page read what was kept, compared with what
  is installed *now*, and every administrator gets one message per new release.
* **Applying**: *Update* for one plugin, *Update all* for every plugin with a compatible
  update that is not held and is not a breaking one. Each run re-fetches the signed index (what
  was kept is never trusted to install from), downloads and checks every wheel before it changes
  anything, takes one snapshot, installs in dependency order through the same checks as an
  install, and **puts everything back if any one fails**. A plugin switched off stays off.

Nothing here weakens a check: this module chooses *which* release; `catalogue.install_release`
verifies and installs it, as it does for a click on *Install*.

**Deploying without a shell.** The new code is on the volume once the run is over. The web
processes keep the modules they imported, so under gunicorn they are asked to reload
(``SIGHUP`` to the arbiter: workers finish their requests, new ones start on the new code).
Anywhere else, and for the scheduler and the worker, which are other processes, the page says
a restart finishes it, and shows the version loaded against the one installed until they agree.
A plugin installed from a wheel carries no tables of its own (that needs the image), so there
are no migrations to run inside the run.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import catalogue as catalogues
from . import dependencies, holds, http, installing
from .installing import canonicalise

audit = logging.getLogger("postulo.plugins.audit")
logger = logging.getLogger(__name__)

#: How often the check asks.
EVERY = timedelta(days=1)

#: A run is one request and the image kills a request past ``--timeout 120``. One install may
#: take up to `installing.INSTALL_TIMEOUT`, so a run that is past this before its next install
#: puts everything back and says to update fewer at a time, rather than being killed half way.
RUN_BUDGET = 70.0


class UpdateError(Exception):
    """An update did not happen, and nothing was changed; the sentence says why."""


# --------------------------------------------------------------------- the switch


def pinned_by_environment() -> bool:
    from postulo.core import configuration

    return configuration.is_set("POSTULO_PLUGIN_UPDATE_CHECK")


def enabled() -> bool:
    """Whether the daily check runs: the environment's word if it gave one, else the switch."""
    from django.conf import settings

    from .models import PluginUpdateSettings

    if pinned_by_environment():
        return getattr(settings, "POSTULO_PLUGIN_UPDATE_CHECK", "daily") != "off"
    return PluginUpdateSettings.current().checking


def switch(on: bool, *, by=None) -> None:
    """The administrator's switch. Refused while the environment decides."""
    from .models import PluginUpdateSettings

    if pinned_by_environment():
        raise UpdateError(
            str(
                _(
                    "POSTULO_PLUGIN_UPDATE_CHECK is set in the environment, so the switch "
                    "cannot be changed here."
                )
            )
        )
    row = PluginUpdateSettings.current()
    if row.checking != on:
        row.checking = on
        row.save(update_fields=["checking"])
        audit.warning(
            "Plugin update check switched %s by %s",
            "on" if on else "off",
            holds.who(by) or "unknown",
            extra={"event": "plugin_update_check", "actor": holds.who(by)},
        )


# ------------------------------------------------------------------ what is offered


@dataclass(frozen=True)
class Offer:
    """One update a catalogue offered for something installed."""

    plugin: str
    installed: str
    available: str
    catalogue: str
    requires_postulo: str = ""
    compatible: bool = True
    security: bool = False
    advisory: str = ""
    breaking: bool = False
    #: Where the plugin's own page is, which is where its release notes are.
    link: str = ""

    def as_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "installed": self.installed,
            "available": self.available,
            "catalogue": self.catalogue,
            "requires_postulo": self.requires_postulo,
            "compatible": self.compatible,
            "security": self.security,
            "advisory": self.advisory,
            "breaking": self.breaking,
            "link": self.link,
        }


def _version(text: str):
    from packaging.version import InvalidVersion, Version

    try:
        return Version(text)
    except InvalidVersion:
        return None


def is_breaking(installed_version: str, release, installed_requires_postulo: str = "") -> bool:
    """Whether moving to ``release`` is the kind of change that may break something.

    A release that leaves the line its plugin's users rely on (0.4 to 0.5, 1.9 to 2.0), or one
    that raises the Postulo it needs above what the installed one declared. Said in the label
    and kept out of *Update all*; the administrator can still press *Update* on it.
    """
    old, new = _version(installed_version), _version(release.version)
    if old is not None and new is not None:

        def compatible_line(version):
            # What a plugin's users may rely on: the minor while it is 0.x, the major after.
            return (0, version.minor) if version.major == 0 else (version.major,)

        if compatible_line(new) > compatible_line(old):
            return True
    if installed_requires_postulo and release.requires_postulo:
        return _floor(release.requires_postulo) > _floor(installed_requires_postulo)
    return False


def _floor(specifier: str):
    """The lowest version a specifier names (``>=0.3,<1.0`` is 0.3), for comparing two."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import Version

    try:
        bounds = [Version(item.version) for item in SpecifierSet(specifier) if ">" in item.operator]
    except InvalidSpecifier:
        return Version("0")
    return max(bounds, default=Version("0"))


def offers_from(one) -> list[Offer]:
    """The updates one verified catalogue offers for what was installed from it.

    Only for a plugin installed *from this catalogue*: a name another catalogue lists is not an
    update to what this one's release replaced (#601). The newest compatible newer release is
    the update; when every newer release needs another Postulo, the newest is shown, marked
    incompatible, with no button.
    """
    found = []
    for listing in one.listings:
        entry = installing.installed(listing.name)
        if entry is None or entry.origin != f"catalogue:{one.name}":
            continue
        newer = [
            release
            for release in listing.releases
            if holds.newer(release.version, entry.version) and _version(release.version)
        ]
        if not newer:
            continue

        def fine(release) -> bool:
            try:
                return installing.fits(release.requires_postulo)
            except ValueError:
                return False

        newer.sort(key=lambda release: _version(release.version), reverse=True)
        compatible = [release for release in newer if fine(release)]
        best = (compatible or newer)[0]
        found.append(
            Offer(
                plugin=entry.name,
                installed=entry.version,
                available=best.version,
                catalogue=one.name,
                requires_postulo=best.requires_postulo,
                compatible=bool(compatible),
                security=any(release.security for release in newer),
                advisory=next((release.advisory for release in newer if release.advisory), ""),
                breaking=is_breaking(entry.version, best, entry.requires_postulo),
                link=listing.repository,
            )
        )
    return found


# ------------------------------------------------------------------- keeping them


def keep(name: str, *, offers: list[Offer], error: str = "") -> None:
    """Write down what one catalogue said, or why it could not be asked."""
    from .models import CatalogueAnswer

    CatalogueAnswer.objects.update_or_create(
        name=name,
        defaults={
            "checked_at": timezone.now(),
            "ok": not error,
            "error": error,
            "offers": [offer.as_dict() for offer in offers],
        },
    )


def last_checked():
    from .models import CatalogueAnswer

    row = CatalogueAnswer.objects.order_by("-checked_at").first()
    return row.checked_at if row else None


def problems() -> dict[str, str]:
    """Each catalogue the last check could not use, and the sentence why."""
    from .models import CatalogueAnswer

    return {row.name: row.error for row in CatalogueAnswer.objects.filter(ok=False)}


def pending() -> list[dict]:
    """The updates still to be had, read from what was kept and compared with what is installed.

    Never fetches. An update applied since the check, a plugin removed, a catalogue no longer
    configured: none of those is shown. Each carries ``held`` (an administrator's hold or pin
    applies) and ``actionable`` (compatible, so there is a button).
    """
    from .models import CatalogueAnswer

    configured = set(catalogues.configured())
    held = holds.all_holds()
    found = []
    for row in CatalogueAnswer.objects.filter(ok=True, name__in=configured):
        for raw in row.offers:
            entry = installing.installed(raw.get("plugin", ""))
            if entry is None or not holds.newer(raw.get("available", ""), entry.version):
                continue
            is_held = canonicalise(entry.name) in held
            found.append(
                {
                    **raw,
                    "installed": entry.version,
                    "held": is_held,
                    "hold": held.get(canonicalise(entry.name)),
                    "actionable": bool(raw.get("compatible", True)),
                }
            )
    return sorted(found, key=lambda item: item["plugin"])


def summary() -> dict:
    """What a notice on the overview says: how many, and which."""
    items = pending()
    actionable = [item for item in items if item["actionable"]]
    names = ", ".join(f"{item['plugin']} {item['available']}" for item in actionable)
    return {
        "count": len(actionable),
        "items": actionable,
        "sentence": (
            ngettext(
                "%(count)d plugin update: %(names)s",
                "%(count)d plugin updates: %(names)s",
                len(actionable),
            )
            % {"count": len(actionable), "names": names}
            if actionable
            else ""
        ),
        "checked_at": last_checked(),
        "enabled": enabled(),
        "pinned": pinned_by_environment(),
        "problems": problems(),
    }


# ----------------------------------------------------------------------- checking


def due() -> bool:
    """Whether a day has passed since the last check, there is something to ask, and it is on."""
    if not enabled() or not catalogues.configured():
        return False
    last = last_checked()
    return last is None or timezone.now() - last >= EVERY


def scheduler_running() -> bool:
    """Whether a scheduler has finished a pass lately: it, and not a page, makes the check."""
    from postulo.core import scheduler

    seen = scheduler.last_pass()
    if seen is None:
        return False
    if timezone.is_naive(seen):
        seen = timezone.make_aware(seen)
    return timezone.now() - seen < timedelta(hours=1)


def check() -> list[Offer]:
    """Ask every configured catalogue for its signed index, keep the answers, tell the people.

    Never raises: a catalogue that is down, expired or not signed by its key is an answer --
    kept as the reason -- and not a reason to end a scheduler pass. Fetches nothing but indexes.
    """
    found: list[Offer] = []
    configured = catalogues.configured()
    from .models import CatalogueAnswer

    CatalogueAnswer.objects.exclude(name__in=list(configured)).delete()
    for name in configured:
        try:
            one = catalogues.fetch(name)
        except (catalogues.CatalogueError, http.DestinationRefused, httpx.HTTPError) as error:
            logger.warning("The plugin update check could not use catalogue %r: %s", name, error)
            keep(name, offers=[], error=str(error))
            continue
        offers = offers_from(one)
        keep(name, offers=offers)
        found.extend(offers)
    announce(found)
    return found


#: One check at a time, and one a day: held for an hour once started, so a worker that is slow to
#: pick the task up is not given it again by every pass of the scheduler.
LEASE_KEY = "postulo:plugin-update-check"


def start_if_due(*, queue=None) -> bool:
    """Make the check if it is time: now, or by ``queue`` (a worker's task) where one is used.

    For the scheduler, which gives the queue when a worker is expected, and for a Server page
    where no scheduler runs. Whoever takes the lease does it; the rest see it held.
    """
    from postulo.core import leases

    if not due():
        return False
    token = leases.take(LEASE_KEY, 3600)
    if token is None:
        return False
    if queue is not None:
        queue()
        return True
    try:
        check()
    finally:
        leases.release(LEASE_KEY, token)
    return True


def check_when_a_page_is_opened() -> bool:
    """Where no scheduler is running, an administrator opening a Server page is the clock (#1187).

    At most once a day, because `due` says so; never where a scheduler does the asking.
    """
    if scheduler_running():
        return False
    try:
        return start_if_due()
    except Exception:
        logger.exception("The plugin update check failed")
        return False


def record(fetched: list, problems_found: list[str]) -> list[Offer]:
    """Keep what an administrator's own *Check for updates* found, as the daily check would.

    The press already fetched and verified the indexes; this writes down what they said, so
    the page and the notices read it afterwards instead of the session.
    """
    found: list[Offer] = []
    for one in fetched:
        offers = offers_from(one)
        keep(one.name, offers=offers)
        found.extend(offers)
    for line in problems_found:
        name, _separator, why = line.partition(": ")
        if name in catalogues.configured():
            keep(name, offers=[], error=why)
    announce(found)
    return found


# ------------------------------------------------------------------------ telling


def announce(offers: list[Offer]) -> int:
    """Tell every administrator once about each release they have not been told of.

    A held plugin's newer release is information, shown on the page; it is announced only
    when it is a security release, so that holding is never a way to miss one. Returns how many
    releases were announced.
    """
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    from postulo.core.preferences import language_for
    from postulo.notifications import inbox

    from .models import PluginUpdateSettings

    record = PluginUpdateSettings.current()
    told = set(record.announced)
    held = holds.all_holds()
    new = [
        offer
        for offer in offers
        if offer.compatible
        and f"{offer.plugin}=={offer.available}" not in told
        and (canonicalise(offer.plugin) not in held or offer.security)
    ]
    if not new:
        return 0
    administrators = list(get_user_model().objects.filter(is_staff=True, is_active=True))
    for administrator in administrators:
        try:
            with translation.override(language_for(administrator)):
                inbox.leave(administrator, _notification(new, reverse("server:plugins")))
        except Exception:
            logger.exception("Could not tell %s about plugin updates", administrator.pk)
    record.announced = [*record.announced, *(f"{o.plugin}=={o.available}" for o in new)][-300:]
    record.save(update_fields=["announced"])
    return len(new)


def _notification(new: list[Offer], url: str):
    """What the inbox is handed: the words, in the language of whoever is reading them."""
    from types import SimpleNamespace

    names = ", ".join(f"{offer.plugin} {offer.available}" for offer in new)
    return SimpleNamespace(
        event="plugin_update",
        title=ngettext(
            "%(count)d plugin update is available",
            "%(count)d plugin updates are available",
            len(new),
        )
        % {"count": len(new)},
        body=names,
        url=url,
    )


# ----------------------------------------------------------------------- applying


@dataclass
class Result:
    """What a run did."""

    updated: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    reloaded: bool = False


def _order(selected: list) -> list:
    """Dependency order: a plugin another selected one needs goes first."""
    remaining, ordered = list(selected), []
    names = {canonicalise(item[0].name) for item in selected}
    while remaining:
        for item in remaining:
            wanted = {
                canonicalise(need.name) for need in dependencies.parse(item[1].requires)
            } & names
            if not wanted - {canonicalise(done[0].name) for done in ordered}:
                ordered.append(item)
                remaining.remove(item)
                break
        else:  # a cycle: nothing here can order it, so take them as they come
            ordered.extend(remaining)
            break
    return ordered


def apply(
    names: list[str] | None = None,
    *,
    by="",
    include_breaking: bool = False,
    include_held: bool = False,
    clock=time.monotonic,
) -> Result:
    """Update the named plugins, or every plugin that has a plain compatible update.

    All or nothing. The signed indexes are fetched again (what a check kept is never
    installed from), every wheel is downloaded and checked against its signed SHA-256 *before*
    anything changes, one snapshot is taken, and the installs run in dependency order, each
    through `catalogue.install_release`. Any failure -- a refusal, a failed install, a plugin
    that does not import, the run taking too long -- puts the whole directory and the record
    back, and the sentence names the plugin and why.

    ``names`` are an administrator's own choice, so a breaking or a held plugin among them is
    updated (a held one only with ``include_held``, which the page asks for after saying it is
    held); with no names, a held plugin and a breaking release are left out unless asked for.
    """
    import tempfile
    from pathlib import Path

    username = holds.who(by)
    fresh, bad = catalogues.fetch_all()
    if not fresh:
        raise UpdateError("; ".join(bad) or str(_("No catalogue is configured on this instance.")))
    wanted = {canonicalise(name) for name in names} if names else None
    held = holds.all_holds()
    selected, skipped = [], []
    for one in fresh:
        by_name = {canonicalise(item.name): item for item in one.listings}
        for offer in offers_from(one):
            key = canonicalise(offer.plugin)
            if wanted is not None and key not in wanted:
                continue
            if not offer.compatible:
                skipped.append(
                    str(_("%(name)s %(version)s needs Postulo %(wanted)s."))
                    % {
                        "name": offer.plugin,
                        "version": offer.available,
                        "wanted": offer.requires_postulo,
                    }
                )
                continue
            if key in held and (held[key].from_environment or not (include_held and wanted)):
                skipped.append(str(_("%(name)s is held.")) % {"name": offer.plugin})
                continue
            if offer.breaking and not (wanted or include_breaking):
                skipped.append(
                    str(_("%(name)s %(version)s may break something; update it on its own."))
                    % {"name": offer.plugin, "version": offer.available}
                )
                continue
            listing = by_name[key]
            release = next(item for item in listing.releases if item.version == offer.available)
            selected.append((listing, release, offer))
    if not selected:
        return Result(skipped=skipped)

    ordered = _order(selected)
    started = clock()
    result = Result(skipped=skipped)
    with installing.record_lock(), tempfile.TemporaryDirectory(prefix="postulo-update-") as scratch:
        wheels = []
        fetching = ""
        try:
            for fetched, release, _offer in ordered:
                fetching = fetched.name
                wheels.append(catalogues.download(release, Path(scratch)))
        except (catalogues.CatalogueError, http.DestinationRefused, httpx.HTTPError) as error:
            raise UpdateError(
                str(_("%(name)s was not updated, and nothing changed: %(why)s"))
                % {"name": fetching, "why": error}
            ) from error

        installing.take_snapshot()
        done = []
        try:
            for (listing, release, offer), wheel in zip(ordered, wheels, strict=True):
                if done and clock() - started > RUN_BUDGET:
                    raise UpdateError(
                        str(
                            _(
                                "That is more than one request has time for. Nothing was "
                                "changed; update fewer at a time."
                            )
                        )
                    )
                catalogues.install_release(
                    listing, release, by=username, wheel=wheel, snapshot=False
                )
                done.append(offer)
        except Exception as error:
            _put_back()
            failed = ordered[len(done)][0].name if len(done) < len(ordered) else ""
            audit.warning(
                "Plugin update by %s rolled back at %s: %s",
                username or "unknown",
                failed,
                error,
                extra={"event": "plugin_update_failed", "actor": username, "plugin": failed},
            )
            if isinstance(error, UpdateError):
                raise
            raise UpdateError(
                str(_("%(name)s was not updated, and nothing was changed: %(why)s"))
                % {"name": failed, "why": error}
            ) from error
        except BaseException:
            _put_back()
            raise
        installing.keep_snapshot()
    for offer in done:
        holds.follow_manual_install(offer.plugin, offer.available, by=by)
        audit.warning(
            "Plugin %s updated from %s to %s by %s (catalogue %s)",
            offer.plugin,
            offer.installed,
            offer.available,
            username or "unknown",
            offer.catalogue,
            extra={"event": "plugin_updated", "actor": username, "plugin": offer.plugin},
        )
        result.updated.append((offer.plugin, offer.installed, offer.available))
    return result


def _put_back() -> None:
    """Everything back to before the run: the files and the record."""
    installing.restore_snapshot()
    installing.give_back_snapshot()
    installing.activate()
    installing._forget_metadata_cache()


# --------------------------------------------------------------------- deploying


def reload_web(server_software: str) -> bool:
    """Ask gunicorn to restart its workers gracefully; False when this is not gunicorn.

    ``SIGHUP`` to the arbiter (this worker's parent): workers finish what they are serving,
    new ones start and import the code that is on the volume now. Anything else -- the
    development server, another WSGI server, no ``SIGHUP`` on this platform -- cannot be
    reloaded from inside, and the caller says a restart finishes it.
    """
    if not str(server_software or "").lower().startswith("gunicorn") or not hasattr(
        signal, "SIGHUP"
    ):
        return False
    try:
        os.kill(os.getppid(), signal.SIGHUP)
    except OSError:
        return False
    return True


def other_processes() -> list[str]:
    """The processes beside the web ones that this instance runs and a reload does not reach."""
    from postulo.core import scheduler

    found = []
    if scheduler.last_pass() is not None:
        found.append(str(_("the scheduler")))
    if scheduler.worker_last_pass() is not None:
        found.append(str(_("the worker")))
    return found


def loaded_version(distribution: str) -> str:
    """The version of a plugin as this process imported it, or an empty string if unknown."""
    from . import base
    from .registry import find_any

    for name in dependencies.provided_by(distribution):
        plugin = find_any(name)
        if plugin is not None:
            version = str(base.manifest_of(plugin).version or "")
            if version:
                return version
    return ""
