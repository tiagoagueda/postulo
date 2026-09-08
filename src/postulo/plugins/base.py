"""The contract every capture source obeys.

There is exactly one shape of parsed posting in Postulo, and everything produces it: the
built-in parser, a third-party plugin, and one day a browser extension posting to the
API. Validating them all through the same schema means a plugin cannot invent a field,
misspell one, or smuggle a value past the review screen.

A source is deliberately given very little to do. It receives a URL and the HTML that was
fetched from it, and returns data. It does not touch the database, decide whether the
result is good enough, or create anything — the person capturing does that, on the review
screen. A parser that guesses wrong should waste a few seconds of somebody's attention,
not put a fabricated job title into their records.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Protocol, runtime_checkable

from django.utils.translation import gettext as _
from pydantic import BaseModel, Field, field_validator

#: Nothing longer than this is kept from a page. Job adverts are not novels, and an
#: unbounded field is an invitation to store somebody's entire single-page application.
MAX_DESCRIPTION_CHARS = 40_000
MAX_FIELD_CHARS = 500


class JobPostingData(BaseModel):
    """A posting as some source understood it.

    Every field is optional except the title, because a source that could only find the
    job title has still saved somebody most of the typing. Fields it could not determine
    are left empty rather than guessed.
    """

    model_config = {"str_strip_whitespace": True, "extra": "forbid"}

    title: str = Field(max_length=MAX_FIELD_CHARS)
    company_name: str = Field(default="", max_length=MAX_FIELD_CHARS)
    location: str = Field(default="", max_length=MAX_FIELD_CHARS)
    remote_type: str = Field(default="", max_length=20)
    employment_type: str = Field(default="", max_length=20)
    # No max_length here on purpose: a length constraint is checked before any
    # validator runs, so declaring one would reject an over-long advert instead of
    # letting truncate_description shorten it. The cap is enforced there.
    description: str = ""

    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = Field(default="", max_length=3)
    salary_period: str = Field(default="", max_length=10)

    posted_at: dt.date | None = None
    closes_at: dt.date | None = None

    url: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_say_something(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A posting needs a title.")
        return value

    @field_validator("description")
    @classmethod
    def truncate_description(cls, value: str) -> str:
        """Cut an over-long description rather than rejecting the whole capture.

        Losing the last few thousand characters of an advert is a much smaller problem
        than throwing away a capture that was otherwise perfectly good.
        """
        if len(value) > MAX_DESCRIPTION_CHARS:
            return value[:MAX_DESCRIPTION_CHARS].rstrip() + "\n\n[…truncated]"
        return value


@runtime_checkable
class SourcePlugin(Protocol):
    """What a capture source must provide.

    Implementations need no base class. A plugin is anything with these four names, which
    keeps third-party packages from having to import Postulo internals just to be
    recognised.
    """

    #: A short identifier, recorded against every capture this source produced.
    name: str
    #: The plugin's own version, so a capture can be traced to the code that made it.
    version: str

    def can_handle(self, url: str) -> bool:
        """Whether this source wants to parse ``url``."""
        ...

    def parse(self, url: str, html: str) -> JobPostingData | None:
        """Extract a posting, or return ``None`` if this page yielded nothing useful."""
        ...


class CaptureError(Exception):
    """Raised when a page cannot be fetched or cannot be understood."""


# ------------------------------------------------------- saying what a plugin is
#
# Everything a plugin says about itself lives in one object, and `@declares` puts it there.
# The alternative — a loose attribute per fact — was what came first, and it has two
# problems. Each new fact is a new optional attribute for every plugin author to know
# about, and none of them can ever be *required*: `runtime_checkable` protocols check data
# members as well as methods, and `registry.py` runs `isinstance` over every third-party
# plugin and drops the ones that fail. Adding `label` to `SourcePlugin` would not have been
# a request. It would have silently unloaded every source anybody had already written.
#
# A manifest sidesteps both. One optional attribute carries any number of facts, and adding
# a field later changes nothing for a plugin that has not heard of it.
#
# A plugin that declares nothing still works, and gets its identifier back where a name
# should be — which is what the interface showed before any of this existed.


@dataclass(frozen=True)
class Manifest:
    """Who a plugin is: everything it says about itself, in one place.

    ``name`` is the identifier the registry keys on and, for a source, the value written
    into every capture's ``source`` field. Changing it orphans that history, so it is the
    one field here that is not free to edit.

    ``author`` is a person or a project and an address — ``First Last <first@example.org>``
    — because "who wrote this" is a question an administrator installing somebody else's
    code is entitled to an answer to. ``licence`` is an SPDX identifier. ``source_url`` is
    where the code actually lives, which is the only claim in here anybody can go and check.

    ``logo`` names an image this plugin would like shown beside its name. Nothing renders it
    yet; the field exists so that shipping one (#106) is a matter of pointing at an image
    rather than of inventing somewhere to put it.
    """

    name: str
    label: str = ""
    version: str = ""
    kind: str = ""
    description: str = ""
    author: str = ""
    licence: str = ""
    source_url: str = ""
    logo: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("A manifest needs a name: it is what the registry keys on.")
        # A plugin that gave no name in words gets its identifier, rather than a blank
        # where a heading should be.
        if not self.label:
            object.__setattr__(self, "label", self.name)


def declares(manifest: Manifest):
    """Class decorator: attach a manifest, and satisfy the protocol from it.

    ``name``, ``version``, ``kind`` and ``label`` are protocol members that the registry,
    the stored records and half the interface read directly. Writing them out beside a
    manifest that repeats them would be two sources of truth for one fact, and the day they
    disagree is the day a capture is filed against a plugin that does not exist. So they are
    set from the manifest, and there is one place to change them.
    """

    def attach(plugin_class):
        plugin_class.manifest = manifest
        for field_name in ("name", "version", "kind", "label", "description"):
            value = getattr(manifest, field_name)
            if value:
                setattr(plugin_class, field_name, value)
        return plugin_class

    return attach


def manifest_of(plugin) -> Manifest:
    """Everything this plugin says about itself, wherever it happens to say it.

    Three places, and the earlier one wins: its own manifest, the loose attributes plugins
    declared before there was one, and the record of the wheel it was installed from. That
    last one matters — a third-party plugin that never heard of manifests still has an
    author and a licence in its packaging, and Postulo already reads them at install time.
    Falling back to it is the difference between "one place to look" being true and being a
    thing this project asks of other people and not of itself.
    """
    declared = getattr(plugin, "manifest", None)
    if isinstance(declared, Manifest):
        known = declared
    else:
        known = Manifest(
            name=str(getattr(plugin, "name", "") or "?"),
            label=str(getattr(plugin, "label", "") or ""),
            version=str(getattr(plugin, "version", "") or ""),
            kind=str(getattr(plugin, "kind", "") or ""),
            description=str(getattr(plugin, "description", "") or ""),
        )
    filled = {
        field_name: value
        for field_name, value in _from_the_wheel(plugin).items()
        if value and not getattr(known, field_name)
    }
    return replace(known, **filled) if filled else known


def _from_the_wheel(plugin) -> dict:
    """What the package a plugin came from said about itself, or nothing.

    Nothing for a built-in, which came from no package. Defensive because a record that
    cannot be read must not take down a page that only wanted to print an author's name.
    """
    try:
        from importlib.metadata import packages_distributions

        from .installing import canonicalise, read_record

        top_level = type(plugin).__module__.split(".")[0]
        distributions = {canonicalise(d) for d in packages_distributions().get(top_level, [])}
        if not distributions:
            return {}
        for entry in read_record():
            if canonicalise(entry.name) in distributions:
                return {
                    "description": entry.summary,
                    "author": entry.author,
                    "licence": entry.licence,
                    "source_url": entry.source_url,
                }
    except Exception:  # pragma: no cover - a broken record explains nobody's name
        return {}
    return {}


def label_of(plugin) -> str:
    """A plugin's name in words, falling back to the identifier it registered under."""
    return str(manifest_of(plugin).label)


def description_of(plugin) -> str:
    """What a plugin says it does, or nothing. Nothing is a perfectly good answer."""
    return str(manifest_of(plugin).description)


# ------------------------------------------- the plugins Postulo itself ships

#: The same for every plugin in this repository, and saying them once is the point: a
#: built-in claiming an independent author, licence or version is inventing a fact.
SHIPPED_AUTHOR = "Postulo <postulo@tiagoagueda.com>"
SHIPPED_LICENCE = "AGPL-3.0-or-later"
SHIPPED_SOURCE_URL = "https://source.tiagoagueda.com/postulo/postulo"


def shipped(*, name: str, label: str, kind: str, description, logo: str = "") -> Manifest:
    """A manifest for a plugin that ships inside Postulo.

    The version is Postulo's own, because that is the truth: these ship with the application
    and change when it does. A built-in with a literal version number identifies nothing --
    ``version = "1.0"`` meant 1.0 the day it was written and would have gone on meaning it
    through every change to the parser underneath.

    Not part of the contract a third party writes against. It is a convenience for this
    repository, so that six built-ins cannot drift into claiming six different licences.
    """
    from postulo import __version__

    return Manifest(
        name=name,
        label=label,
        version=__version__,
        kind=kind,
        description=str(description),
        author=SHIPPED_AUTHOR,
        licence=SHIPPED_LICENCE,
        source_url=SHIPPED_SOURCE_URL,
        logo=logo,
    )


# ------------------------------------------------------------------- importers

#: Where an importer registers itself. Nothing publishes here yet — Europass is built in —
#: and the contract a third party would write against is #105's subject.
IMPORTER_GROUP = "postulo.importers"

#: The most a career file may be. A CV is not a novel, and an unbounded upload handed to a
#: parser is the parser's problem right up until it is everyone's.
MAX_IMPORT_BYTES = 5 * 1024 * 1024


class ImportRefused(Exception):
    """The file will not be read, and the message says why, to the person who chose it."""


def refuse_unreadable(data: bytes) -> None:
    """What Postulo refuses before any importer sees a byte.

    An importer is handed **a file somebody uploaded**, which is not the threat a source
    faces: a source is given a URL and the HTML that Postulo fetched from it. So these
    refusals belong to the kind rather than to each plugin, because "every plugin author
    remembers" is not a control, and the one they would forget is the third.

    The DOCTYPE check applies to anything that looks like XML. That is where entity
    expansion lives, and the point is to refuse it rather than to hand it to a parser and
    hope. It is not applied to everything: ``<!DOCTYPE`` inside an early string value of a
    perfectly ordinary JSON file is not an attack, and refusing it would be a bug.
    """
    if not data:
        raise ImportRefused(_("That file is empty."))
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportRefused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_IMPORT_BYTES // (1024 * 1024)}
        )
    head = data[:4096].lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<") and re.search(rb"<!DOCTYPE", data[:4096], re.I):
        raise ImportRefused(
            _(
                "That file carries a document type declaration, which Postulo will not "
                "read. A Europass export does not have one."
            )
        )


@runtime_checkable
class ImporterPlugin(Protocol):
    """What something that reads a career out of a file must provide.

    The mirror of :class:`SourcePlugin`, for a different input and a different output: a
    source reads a *job posting* off a *page*, an importer reads a *person's career* out of
    a *file*. No base class, as everywhere else here.

    ``read`` returns whatever the importing app understands — today a
    ``postulo.resume.europass.Record``. This protocol does not name that type, because the
    plugin machinery has no business depending on the resume app, and because the built-in
    is the only implementation until #105 writes the contract for anybody else's.

    **An importer does not write anything.** It turns bytes into a record and stops. What
    reaches the database is decided on the review screen, by the person, for the reason
    stated at the top of this module: a parser that guesses wrong should waste a few
    seconds of somebody's attention, not put a fabricated job title into their records —
    and an import writes a career, which is a great deal more than a title.
    """

    name: str
    version: str
    kind: str
    label: str
    description: str

    def can_handle(self, data: bytes, filename: str = "") -> bool:
        """Whether this importer recognises the file."""
        ...

    def read(self, data: bytes):
        """Turn the file into a record, or raise :class:`ImportRefused` saying why not."""
        ...


# ------------------------------------------------------------------ transports

#: Where something that *delivers* mail registers itself.
TRANSPORT_GROUP = "postulo.transports"


# ----------------------------------------------------------- connected plugins

#: The kinds of plugin that talk to another service on a person's behalf, and the
#: entry-point group each registers under. Sources are stateless and live apart.
CONNECTED_KINDS = {
    "notifier": "postulo.notifiers",
    "store": "postulo.stores",
    "sync": "postulo.syncs",
}

FIELD_TYPES = ("text", "url", "email", "password", "integer", "boolean", "choice", "textarea")


@dataclass(frozen=True)
class FieldSpec:
    """One thing a plugin needs from a person: an address, a token, a folder name.

    Postulo builds the connection form from these, so a plugin never renders HTML and a
    person never sees a form Postulo did not draw. A ``secret`` field is stored encrypted
    and is never shown back; ``choices`` are ``(value, label)`` pairs for ``choice``.
    """

    name: str
    label: str
    type: str = "text"
    help: str = ""
    required: bool = True
    secret: bool = False
    default: str | int | bool | None = None
    choices: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"Unknown field type {self.type!r}; use one of {FIELD_TYPES}.")
        if self.type == "choice" and not self.choices:
            raise ValueError(f"Field {self.name!r} is a choice with no choices.")


@dataclass(frozen=True)
class TestResult:
    """What ``test()`` came back with: whether it worked, and a sentence for the person."""

    ok: bool
    message: str = ""


@runtime_checkable
class TransportPlugin(Protocol):
    """What something that carries a message off this machine must provide.

    A transport is not a notifier and the difference is worth keeping. A *notifier* decides
    that something is worth telling somebody and writes the words; a *transport* gets those
    words to them. One sits on the other. Merging them would make "when should Postulo tell
    me things" and "how does this instance reach the outside world" the same form, and they
    are not: the first is a person's preference, the second is the operator's plumbing.

    That is also why a transport is **not** governed by the per-person plugin policy (#95).
    None of *available*, *unavailable*, *forced on* or *forced off* means anything about
    mail delivery, and *forced off* would mean an account nobody can recover.

    Why the kind exists at all: a self-hoster whose provider blocks outbound 25, 465 and
    587 -- which is most residential connections and several hosts -- has no route today
    except finding a relay that speaks SMTP. A transport lets them install one that speaks
    an HTTP API instead. Postulo ships exactly one, SMTP, and names no vendor.

    ``deliver`` takes Django ``EmailMessage`` objects and returns how many it sent, which
    is the contract an email backend already has, so a transport can be a thin wrapper
    around one where that is the honest implementation.
    """

    name: str
    version: str
    kind: str
    label: str
    description: str

    def config_fields(self) -> list[FieldSpec]:
        """What this transport needs to know. Drawn by Postulo, as a connection's are."""
        ...

    def test(self, config: dict) -> TestResult:
        """Prove the configuration without sending anybody a message."""
        ...

    def deliver(self, messages: list, config: dict) -> int:
        """Send them, and say how many went. Raising is a failure the caller reports."""
        ...


@runtime_checkable
class ConnectedPlugin(Protocol):
    """What a plugin that connects to another service must provide.

    No base class, as with sources: these names are enough. ``kind`` says which group it
    belongs to — ``notifier``, ``store`` or ``sync`` — and the kind's own interface adds
    what that kind does (a notifier sends; a store puts). This is the part they share:
    what to ask the person for, and how to prove the answers work.

    Two more methods are optional, and Postulo looks for them by name:

    - ``validate(config) -> dict[str, list[str]]`` runs when the connection form is
      submitted, with configuration and secrets together, and returns problems keyed by
      field name (an empty key for the form as a whole). A plugin that can tell a typo
      from a token should say so here, at the form, rather than at three in the morning
      when a reminder falls due.
    - ``summary(config) -> str`` is one line for the connections list: which services a
      connection reaches, with every secret part masked. Secrets are never shown back,
      so this is how a person tells two connections to the same plugin apart.
    """

    name: str
    version: str
    kind: str
    label: str

    def config_fields(self) -> list[FieldSpec]:
        """What the connection form asks for."""
        ...

    def test(self, config: dict) -> TestResult:
        """Try the configuration for real — one request, one message — and report."""
        ...


# ------------------------------------------------------------------------- syncs


@dataclass
class SyncReport:
    """What one run of a sync did, in numbers and in sentences.

    ``notes`` are things a person should know — a record removed on the other side, an
    event on the calendar that is not Postulo's — and ``error`` is why the run stopped,
    if it did. Both are shown on the connection.
    """

    pushed: int = 0
    pulled: int = 0
    removed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        parts = []
        if self.pushed:
            parts.append(f"{self.pushed} pushed")
        if self.pulled:
            parts.append(f"{self.pulled} pulled")
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        text = ", ".join(parts) if parts else "nothing to do"
        if self.notes:
            text += " · " + " · ".join(self.notes)
        return text


@runtime_checkable
class SyncPlugin(ConnectedPlugin, Protocol):
    """A connected plugin that keeps records here and records elsewhere the same.

    ``sync`` is given the connection itself — the plugin keeps its
    :class:`~postulo.plugins.models.SyncLink` rows against it — and the configuration
    and secrets together. It compares both sides, pushes and pulls what it must, and
    returns a :class:`SyncReport`. It raises when it cannot run at all; a record it
    cannot handle is a note, not an exception. The scheduler calls it on the interval
    the connection carries; *Sync now* calls it at once.
    """

    def sync(self, connection, config: dict) -> SyncReport: ...
