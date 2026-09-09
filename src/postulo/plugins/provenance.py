"""Where a plugin came from: shipped inside Postulo, signed by a repository, or a file.

> plugins kinds: internal (shipped with postulo), oficial plugin (downloaded from official
> repo, or thru a zip) custom plugin (downloaded from custom repo, or thru a zip)

Three kinds, and the whole difficulty is in the second one. **A zip is a zip.** A file
somebody uploads carries no evidence of who published it, and calling it official because it
was named after an official plugin would be worse than not labelling it at all.

There is an honest way, and it uses what the catalogue machinery already computes: a signed
index lists each release's SHA-256, and `installing.py` records the digest of every wheel it
installs. So a file is official when **its bytes are byte-for-byte the ones an official
repository signed** — the signature covers the bytes, not the delivery, so a wheel that
arrived by upload and a wheel that arrived by download are the same wheel or they are not.

**Which repository is official is decided by a key, never by a name.** Anybody can call
their repository ``postulo``; nobody else can sign with Postulo's key. :data:`OFFICIAL_KEYS`
is what this instance was built believing, and it is empty — Postulo publishes no catalogue
yet, so *nothing is official today* and everything installed is custom or uploaded. That is
the truthful answer rather than a placeholder, and the day there is a catalogue the answer
changes by adding a key rather than by writing this again.

**What a label here does not mean.** Not safe: installing a plugin runs somebody else's code
inside Postulo, and that is true of an official one. Not "its dependencies were checked": the
signature and the checksum cover the plugin's own wheel, and its requirements come from PyPI
at install time and are whatever was served that day. The wording on the page has to keep
both of those from quietly coming undone (#94).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

#: Shipped inside Postulo. Not installed, not removable, and running whatever else is.
INTERNAL = "internal"

#: Published by a repository this instance was built trusting, and proved by signature.
OFFICIAL = "official"

#: Published by some other repository the operator added, and proved by its signature.
CUSTOM = "custom"

#: A file somebody uploaded that matches nothing any enabled repository signed. Not a
#: judgement about the file: it is the whole truth about where it came from.
UPLOADED = "uploaded"

#: The public keys Postulo itself vouches for, base64 as a catalogue row carries them.
#:
#: Empty, and that is the current fact rather than an unfinished edge: Postulo publishes no
#: catalogue. Every plugin an instance has today is therefore custom or uploaded, which is
#: exactly what a page should say. A name would have been the easy thing to key on and the
#: wrong one -- anybody can call their repository ``postulo`` -- so this is a key, because
#: the point of the label is that it describes evidence.
OFFICIAL_KEYS: tuple[str, ...] = ()

LABELS = {
    INTERNAL: _("Internal"),
    OFFICIAL: _("Official"),
    CUSTOM: _("Custom"),
    UPLOADED: _("Uploaded"),
}

EXPLANATIONS = {
    INTERNAL: _("Ships inside Postulo. It cannot be removed."),
    OFFICIAL: _("Its file matches what the official repository signed."),
    CUSTOM: _("Its file matches what a repository on this instance signed."),
    UPLOADED: _("Uploaded. Nothing here signed this file, so nothing vouches for it."),
}


@dataclass(frozen=True)
class Provenance:
    """What is known about where one plugin came from, and how firmly."""

    kind: str
    #: The repository whose signature covers this file, where one does.
    repository: str = ""

    @property
    def label(self):
        return LABELS.get(self.kind, LABELS[UPLOADED])

    @property
    def explanation(self):
        return EXPLANATIONS.get(self.kind, EXPLANATIONS[UPLOADED])

    @property
    def removable(self) -> bool:
        """Whether it can be taken off the instance. A built-in never can."""
        return self.kind != INTERNAL


def official_repositories() -> set[str]:
    """The configured repositories whose key Postulo itself vouches for."""
    if not OFFICIAL_KEYS:
        return set()
    from . import catalogue

    trusted = set(OFFICIAL_KEYS)
    return {name for name, row in catalogue.configured().items() if row.get("key") in trusted}


def signed_digests() -> dict[str, str]:
    """Every checksum the enabled repositories publish, to the repository that signed it.

    Read from the indexes already fetched and verified: `catalogue.fetch_all` checks each
    signature before returning anything, so a digest reaching this dictionary is one a
    repository actually signed.

    Fetching can fail -- a repository is unreachable, its index will not verify -- and then
    its digests are simply not here. That is the case the issue is most careful about: the
    answer when verification is unavailable is *uploaded*, never a guess.
    """
    from . import catalogue

    found: dict[str, str] = {}
    try:
        catalogues, _failed = catalogue.fetch_all()
    except Exception:  # pragma: no cover - fetch_all already collects its own failures
        return found
    for one in catalogues:
        for listing in one.listings:
            for release in listing.releases:
                if release.sha256:
                    found.setdefault(release.sha256.lower(), one.name)
    return found


def of_builtin(plugin) -> Provenance:
    """A plugin that ships inside Postulo. Nothing to verify: it is the application."""
    return Provenance(kind=INTERNAL)


def of_record(entry, *, digests: dict[str, str] | None = None) -> Provenance:
    """Where an installed plugin came from, derived rather than declared.

    An entry that names the catalogue it was downloaded from says so and is believed: it
    was verified against that catalogue's signed checksum at the moment it was installed,
    which is the same evidence.

    An upload is checked here, now, against what the enabled repositories currently sign.
    A match means the bytes on this instance are the bytes a repository published --
    whatever route they took to get here. No match means uploaded, and that is the whole
    truth about it rather than a suspicion.
    """
    origin = getattr(entry, "origin", "") or ""
    digest = (getattr(entry, "sha256", "") or "").lower()
    official = official_repositories()

    if origin.startswith("catalogue:"):
        repository = origin.partition(":")[2]
        kind = OFFICIAL if repository in official else CUSTOM
        return Provenance(kind=kind, repository=repository)

    if digest:
        signed = signed_digests() if digests is None else digests
        repository = signed.get(digest, "")
        if repository:
            kind = OFFICIAL if repository in official else CUSTOM
            return Provenance(kind=kind, repository=repository)

    return Provenance(kind=UPLOADED)
