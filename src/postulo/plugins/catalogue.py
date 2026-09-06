"""Catalogues: signed lists of plugins an administrator can install by name.

A catalogue is one JSON file, published anywhere, listing plugins and — per version — a
wheel to fetch, its SHA-256, the Postulo versions it is compatible with, and what it
provides. Beside it sits a signature over exactly those bytes, and an administrator
configures a catalogue as a URL **and** a public key. Without the key there is no
catalogue: an unsigned list of URLs to run code from is not something Postulo will offer.

The checks, in order, and each of them fatal:

1. the index's signature verifies against the configured key (Ed25519);
2. the plugin and version asked for are in the index;
3. the wheel that arrives matches the SHA-256 the signed index gave.

So a mirror, a hijacked download host, or a modified index cannot ship code. What a
catalogue cannot do is vouch for the plugin's behaviour; being listed means the people who
publish that catalogue looked at it, which is a review and not a guarantee, and the page
says so.

**Nothing here runs on its own.** An index is fetched when an administrator opens the page
and asks, and never in the background: Postulo makes no request nobody asked for.
"""

from __future__ import annotations

import base64
import binascii
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from django.conf import settings
from django.utils.translation import gettext as _

from . import http
from .installing import canonicalise

#: A published index is small. Anything larger is not one.
MAX_INDEX_BYTES = 2_000_000
#: A plugin wheel is a few hundred kilobytes; this is room to spare, not an invitation.
MAX_WHEEL_BYTES = 40_000_000


class CatalogueError(Exception):
    """The catalogue could not be read or could not be trusted."""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    requires_postulo: str = ""
    provides: tuple[str, ...] = ()


@dataclass(frozen=True)
class Listing:
    """One plugin as a catalogue describes it."""

    name: str
    summary: str = ""
    maintainer: str = ""
    licence: str = ""
    repository: str = ""
    releases: tuple[Release, ...] = ()
    catalogue: str = ""

    @property
    def latest(self) -> Release | None:
        return self.releases[0] if self.releases else None


@dataclass
class Catalogue:
    name: str
    url: str
    public_key: str
    listings: list[Listing] = field(default_factory=list)


def configured() -> dict[str, dict[str, str]]:
    """The catalogues this instance knows: name → {url, key}.

    Empty by default. Postulo publishes no catalogue yet, and pointing at one is a
    decision an operator makes, not something an upgrade does for them.
    """
    raw = getattr(settings, "POSTULO_PLUGIN_CATALOGUES", "") or ""
    found: dict[str, dict[str, str]] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split("|")
        if len(pieces) != 3:
            continue
        name, url, key = (piece.strip() for piece in pieces)
        if name and url and key:
            found[name] = {"url": url, "key": key}
    return found


def verify(payload: bytes, signature: str, public_key: str) -> None:
    """Ed25519 over the index's exact bytes. Anything that does not check out is refused."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(base64.b64decode(signature, validate=True), payload)
    except (ValueError, binascii.Error, TypeError) as error:
        raise CatalogueError(
            str(_("The catalogue's signature or key is not readable: %(error)s")) % {"error": error}
        ) from error
    except InvalidSignature as error:
        raise CatalogueError(
            str(_("The catalogue's signature does not match its contents; it was not used."))
        ) from error


def parse(payload: bytes, *, catalogue: str = "") -> list[Listing]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise CatalogueError(str(_("The catalogue is not readable JSON."))) from error
    listings = []
    for entry in document.get("plugins", []):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        releases = []
        for release in entry.get("releases", []):
            if not isinstance(release, dict):
                continue
            if not (release.get("version") and release.get("url") and release.get("sha256")):
                continue
            releases.append(
                Release(
                    version=str(release["version"]),
                    url=str(release["url"]),
                    sha256=str(release["sha256"]).lower(),
                    requires_postulo=str(release.get("requires_postulo", "")),
                    provides=tuple(str(item) for item in release.get("provides", [])),
                )
            )
        listings.append(
            Listing(
                name=str(entry["name"]),
                summary=str(entry.get("summary", "")),
                maintainer=str(entry.get("maintainer", "")),
                licence=str(entry.get("licence", entry.get("license", ""))),
                repository=str(entry.get("repository", "")),
                releases=tuple(releases),
                catalogue=catalogue,
            )
        )
    return listings


def fetch(name: str) -> Catalogue:
    """Fetch one catalogue's index and its signature, and check the one against the other."""
    known = configured()
    if name not in known:
        raise CatalogueError(
            str(_("No catalogue called “%(name)s” is configured.")) % {"name": name}
        )
    url = known[name]["url"]
    with http.client() as client:
        payload = _get(client, url, MAX_INDEX_BYTES)
        signature = _get(client, url + ".sig", 4096).decode("ascii", "replace").strip()
    verify(payload, signature, known[name]["key"])
    return Catalogue(
        name=name, url=url, public_key=known[name]["key"], listings=parse(payload, catalogue=name)
    )


def fetch_all() -> tuple[list[Catalogue], list[str]]:
    """Every configured catalogue, and the errors from the ones that would not come."""
    found, problems = [], []
    for name in configured():
        try:
            found.append(fetch(name))
        except (CatalogueError, http.DestinationRefused, httpx.HTTPError) as error:
            problems.append(f"{name}: {error}")
    return found, problems


def _get(client: httpx.Client, url: str, cap: int) -> bytes:
    response = client.get(url)
    if response.status_code != 200:
        raise CatalogueError(
            str(_("%(url)s answered %(code)s.")) % {"url": url, "code": response.status_code}
        )
    content = response.content
    if len(content) > cap:
        raise CatalogueError(
            str(_("%(url)s is far larger than a catalogue should be.")) % {"url": url}
        )
    return content


def find(catalogues: list[Catalogue], plugin: str) -> tuple[Listing, Release]:
    canonical = canonicalise(plugin)
    for catalogue in catalogues:
        for listing in catalogue.listings:
            if canonicalise(listing.name) == canonical and listing.latest:
                return listing, listing.latest
    raise CatalogueError(str(_("No catalogue lists %(name)s.")) % {"name": plugin})


def download(release: Release, into: Path) -> Path:
    """Fetch a wheel and check it against the checksum the signed index carried."""
    from .installing import digest_of

    into.mkdir(parents=True, exist_ok=True)
    target = into / (release.url.rstrip("/").rsplit("/", 1)[-1] or "plugin.whl")
    with http.client(timeout=120.0) as client, client.stream("GET", release.url) as response:
        if response.status_code != 200:
            raise CatalogueError(
                str(_("%(url)s answered %(code)s."))
                % {"url": release.url, "code": response.status_code}
            )
        written = 0
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                written += len(chunk)
                if written > MAX_WHEEL_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise CatalogueError(
                        str(_("The download is far larger than a plugin should be."))
                    )
                handle.write(chunk)
    if digest_of(target) != release.sha256:
        target.unlink(missing_ok=True)
        raise CatalogueError(
            str(_("The wheel does not match the checksum the catalogue published for it."))
        )
    return target


def install(plugin: str, *, by: str = ""):
    """Fetch, verify and install one plugin named in a configured catalogue."""
    from .installing import install_wheel

    catalogues, problems = fetch_all()
    if not catalogues:
        raise CatalogueError(
            "; ".join(problems) or str(_("No catalogue is configured on this instance."))
        )
    listing, release = find(catalogues, plugin)
    with tempfile.TemporaryDirectory(prefix="postulo-plugin-") as scratch:
        wheel = download(release, Path(scratch))
        return install_wheel(
            wheel,
            origin=f"catalogue:{listing.catalogue}",
            source=release.url,
            by=by,
            expected_sha256=release.sha256,
        )
