"""Everything a plugin may import from Postulo. Nothing else is a contract.

> a plugin, even a internal must be a self-contained as possible having is own manifest,
> is own locale, etc, and dont depende on the core

"Do not depend on the core" could not be enforced, or even checked, while there was no
written answer to *depend on what, then*. This module is that answer: the names below are a
promise, everything else in `postulo` is this month's internals, and a test walks every
plugin Postulo ships and fails on one that reaches past this (#126).

**Total independence is not the goal and cannot be.** A plugin holding one person's data
must scope it with `for_user()` or it breaks the project's first promise. One that renders
must extend the base template or it renders unstyled. One that redirects must go through
`safe_next()`, one that fetches must go through the client that checks where it is dialling.
Each of those is a *reason* to depend on Postulo, and the imperative is served by making
them a small, named, stable set rather than by pretending they are avoidable.

**This is a promise about breakage, made deliberately and now.** Once a surface exists,
changing it breaks third-party plugins between releases — which is the cost the project
avoided so far by having no such surface. It is made now because the alternative is worse:
#129 moves every shipped plugin into its own package, and a package outside `src/postulo`
has to know what it may import. Deferring the promise means either deferring that work or
doing it against an undeclared surface, which is how a surface gets set by accident.

**What that promise is.** These names keep working across a minor release. A change to any
of them is a `### ⚠️ Deprecated` entry first and a `### 🗑️ Removed` entry in a later
release, never a silent rename. Anything reached through `postulo.plugins.base`,
`postulo.core` or any other module may move without warning, whatever it looks like today.

**What is deliberately not here yet.** What a dashboard widget is handed (#125) is
unsettled; a plugin needing it is reaching past this on purpose, and
`tests/test_plugin_surface.py` records the ones that do with the reason. That list is the
map of what #129 still has to move. What a *document* template may be is settled: `Theme`
and `ThemeKind` below, plus a ``templates/`` directory beside the package (#132).

**The surface was too small for the plugins that existed (#229).** Every official Python
plugin imported past it and the wiki taught those imports -- not out of carelessness, but
because a notifier is handed a `Notification` that was not here, and a sync plugin cannot
keep two sides the same without the records and the `SyncLink` that ties them. A promise
nobody can keep is not a promise, so the missing half is here now: the notifier contract,
the records a sync works on, the calls that write to a timeline, the details that hang off
a contact, and the calendar text. It is a wider surface, deliberately, because the
alternative was a narrow one that was routinely ignored.

**A listing has a history now (#270)**, and one call writes to it: `record_listing_event`,
for a plugin that sees a mail or a message about a job before anybody has applied. It is
handed the listing, or an application whose listing it is, and takes the owner from that
record; the plugin never says whose history it writes.

**A web link is on a service now (#305)**, and the list of services is a registry a plugin
may add to: `LinkService` is one entry, `LinkServicePlugin` what holds them, and
`link_service_of` the read a plugin makes of a link it was handed.

**A messaging handle is on a service too (#682)**, a registry of its own: `MessagingService`
is one entry, `MessagingServicePlugin` what holds them, `messaging_service_of` the read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - the names `__getattr__` resolves at run time
    # Listed here so a reader, an editor and a linter all see the whole surface in one
    # place. They cannot be imported at module load: several are Django models and this
    # module is reachable before the app registry is ready.
    from postulo.applications.ical import calendar_status, event_lines
    from postulo.applications.models import (
        Application,
        EventKind,
        Interview,
        InterviewOutcome,
        Suggestion,
    )
    from postulo.applications.services import (
        get_or_create_company,
        record_event,
        reschedule_interview,
        settle_interview,
    )
    from postulo.applications.suggestions import suggest
    from postulo.core.currencies import CURRENCY_CODES
    from postulo.core.languages import match as language_match
    from postulo.core.languages import matches as language_matches
    from postulo.core.languages import override as language_override
    from postulo.core.languages import tag as language_tag
    from postulo.core.languages import well_formed as is_language_tag
    from postulo.core.link_services import Service as LinkService
    from postulo.core.link_services import find as link_service_of
    from postulo.core.messaging_services import Service as MessagingService
    from postulo.core.messaging_services import find as messaging_service_of
    from postulo.core.models import OwnedModel, OwnedQuerySet
    from postulo.core.phone_numbers import primary_for as primary_phone_number
    from postulo.core.phone_numbers import save_only_number as save_phone_number
    from postulo.core.phone_numbers import taken_by_asking as phone_number_is_taken
    from postulo.core.phones import COUNTRY_CODES
    from postulo.core.redirects import safe_next
    from postulo.core.vcard import Address as VCardAddress
    from postulo.core.vcard import Card as VCard
    from postulo.core.vcard import Link as VCardLink
    from postulo.core.vcard import Phone as VCardPhone
    from postulo.core.vcard import VCardRefused
    from postulo.core.vcard import card_to_text as vcard_from_values
    from postulo.core.vcard import read as vcards_from_text
    from postulo.core.web_links import Kind as LinkKind
    from postulo.core.web_links import primary_for as primary_web_link
    from postulo.core.web_links import save_only_link as save_web_link
    from postulo.documents.formats import Format as DocumentFormat
    from postulo.documents.formats import register as register_document_format
    from postulo.documents.outline import Outline
    from postulo.documents.themes import Kind as ThemeKind
    from postulo.documents.themes import Theme
    from postulo.jobs.history import record_listing_event
    from postulo.jobs.models import Contact
    from postulo.jobs.places import resolve as place_of
    from postulo.notifications.base import EVENTS, Notification, NotifierPlugin
    from postulo.resume.importing import Record

    from .consent import ACCESS_TOKEN, access_token
    from .http import (
        DestinationRefused,
        approve_host,
        check_destination,
        client,
        public_only_client,
    )
    from .models import SyncLink

from .base import (
    # ------------------------------------------ what a transport carries, and how
    MAIL,
    MAX_IMPORT_BYTES,
    MEDIUMS,
    TEXT,
    # ------------------------------------------------- the protocols to satisfy
    ConnectedPlugin,
    # ------------------------------- what a plugin raises when the other side has finished
    ConnectionUnusable,
    # -------------------------------------------------- what a plugin declares
    Consent,
    # ------------------------------------------- what a store is handed, and gives back
    DocumentMetadata,
    ExternalRef,
    FeaturePlugin,
    FieldSpec,
    ImporterPlugin,
    # ---------------------------------------------------- reading a file safely
    ImportRefused,
    JobPostingData,
    LinkServicePlugin,
    Manifest,
    MessagingServicePlugin,
    OutboxPlugin,
    SourcePlugin,
    StorePlugin,
    SyncPlugin,
    SyncReport,
    # ----------------------------------------------------- what it hands back
    TestResult,
    TextMessage,
    TransportPlugin,
    declares,
    description_of,
    label_of,
    manifest_of,
    medium_of,
    refuse_unreadable,
    shipped,
)

__all__ = [
    "ACCESS_TOKEN",
    "COUNTRY_CODES",
    "CURRENCY_CODES",
    "EVENTS",
    "MAIL",
    "MAX_IMPORT_BYTES",
    "MEDIUMS",
    "TEXT",
    "Application",
    "ConnectedPlugin",
    "ConnectionUnusable",
    "Consent",
    "Contact",
    "DestinationRefused",
    "DocumentFormat",
    "DocumentMetadata",
    "EventKind",
    "ExternalRef",
    "FeaturePlugin",
    "FieldSpec",
    "ImportRefused",
    "ImporterPlugin",
    "Interview",
    "InterviewOutcome",
    "JobPostingData",
    "LinkKind",
    "LinkService",
    "LinkServicePlugin",
    "Manifest",
    "MessagingService",
    "MessagingServicePlugin",
    "Notification",
    "NotifierPlugin",
    "OutboxPlugin",
    "Outline",
    "OwnedModel",
    "OwnedQuerySet",
    "Record",
    "SourcePlugin",
    "StorePlugin",
    "Suggestion",
    "SyncLink",
    "SyncPlugin",
    "SyncReport",
    "TestResult",
    "TextMessage",
    "Theme",
    "ThemeKind",
    "TransportPlugin",
    "VCard",
    "VCardAddress",
    "VCardLink",
    "VCardPhone",
    "VCardRefused",
    "access_token",
    "approve_host",
    "calendar_status",
    "check_destination",
    "client",
    "declares",
    "description_of",
    "event_lines",
    "get_or_create_company",
    "is_language_tag",
    "label_of",
    "language_match",
    "language_matches",
    "language_override",
    "language_tag",
    "link_service_of",
    "manifest_of",
    "medium_of",
    "messaging_service_of",
    "phone_number_is_taken",
    "place_of",
    "primary_phone_number",
    "primary_web_link",
    "public_only_client",
    "record_event",
    "record_listing_event",
    "refuse_unreadable",
    "register_document_format",
    "reschedule_interview",
    "safe_next",
    "save_phone_number",
    "save_web_link",
    "settle_interview",
    "shipped",
    "suggest",
    "vcard_from_values",
    "vcards_from_text",
]

#: Where a plugin author reads what the names above are for, and why nothing else is (#170).
_GUIDE = "https://source.tiagoagueda.com/postulo/postulo/wiki/Writing-a-plugin"


#: The rest of the surface, as a table: the name a plugin asks for, and where it lives
#: (#229). A branch apiece the way the ones below are written would be twenty-three
#: paragraphs restating twenty-three one-line imports; what is worth saying about these is
#: said once per group, above the group.
_ELSEWHERE: dict[str, tuple[str, str]] = {
    # ------------------------------------------------------------- the notifier contract
    # What `send()` is handed, and which events a person may switch on per connection.
    # Until now every notifier there is -- Postulo's own included -- reached past the
    # surface for these, because they were never on it. `Notification` also carries a
    # `key`, a `language`, an `occurred_at` and a `data` mapping, which is what a notifier
    # needs to deduplicate a retry, render around the words, and file what it is about.
    "EVENTS": ("postulo.notifications.base", "EVENTS"),
    "Notification": ("postulo.notifications.base", "Notification"),
    "NotifierPlugin": ("postulo.notifications.base", "NotifierPlugin"),
    # -------------------------------------------- the records a sync keeps two sides of
    # A sync plugin is the one kind that cannot be handed what it needs: it walks one
    # person's records, compares them with another service's, and writes both ways. Scoped
    # with `for_user()` like anything else -- these are `OwnedModel`s, and the promise
    # about ownership is the same one.
    "Application": ("postulo.applications.models", "Application"),
    "Contact": ("postulo.jobs.models", "Contact"),
    "EventKind": ("postulo.applications.models", "EventKind"),
    "Interview": ("postulo.applications.models", "Interview"),
    "InterviewOutcome": ("postulo.applications.models", "InterviewOutcome"),
    "Suggestion": ("postulo.applications.models", "Suggestion"),
    # What ties a local record to its twin on the other side: the remote address, the
    # identifier, the version tag, a hash of what was last pushed. Kept beside the record
    # rather than on it, so a contact stays a contact.
    "SyncLink": ("postulo.plugins.models", "SyncLink"),
    # ------------------------------------------------- and the five ways to write to one
    # Never by saving a model: the timeline has to read the same whoever wrote to it, and
    # an automatism has to be undoable by hand, which is what the `actor` on each of these
    # is for. `suggest` is the one to reach for first -- a plugin reading a mailbox or a
    # calendar is guessing, and a guess belongs in a queue a person answers, not in the
    # record.
    "get_or_create_company": ("postulo.applications.services", "get_or_create_company"),
    "record_event": ("postulo.applications.services", "record_event"),
    "reschedule_interview": ("postulo.applications.services", "reschedule_interview"),
    "settle_interview": ("postulo.applications.services", "settle_interview"),
    "suggest": ("postulo.applications.suggestions", "suggest"),
    # ------------------------------------------------ and the one way into a listing's
    # history (#270). What arrives about a job before anybody applies -- a message, an
    # email, a forwarded description -- goes on the listing, and the application's page
    # reads it from there. The call takes a record the plugin already holds, a listing or
    # an application whose listing it is, and the owner is that record's: a plugin never
    # says whose history it writes, and a contact or a file it names that belongs to
    # anybody else is refused. Given an `external_id` it is idempotent, as `suggest` is.
    "record_listing_event": ("postulo.jobs.history", "record_listing_event"),
    # ------------------------------------------------ the details that hang off a contact
    # A number and a profile are not columns on a contact: they are rows of their own, one
    # of them primary, and an instance may allow several. A plugin writing one directly
    # would have to know that; these are the two reads and the two writes it actually
    # needs, and they keep the primary flag and the uniqueness rules with Postulo.
    "LinkKind": ("postulo.core.web_links", "Kind"),
    # `phone_number_is_taken(number, asked_by=account)` charges the answer to the account the
    # plugin works for (#333). Called without `asked_by` it is the bare question and
    # warns: that form is deprecated and goes in a later release.
    "phone_number_is_taken": ("postulo.core.phone_numbers", "taken_by_asking"),
    "primary_phone_number": ("postulo.core.phone_numbers", "primary_for"),
    "primary_web_link": ("postulo.core.web_links", "primary_for"),
    "save_phone_number": ("postulo.core.phone_numbers", "save_only_number"),
    "save_web_link": ("postulo.core.web_links", "save_only_link"),
    # ------------------------------------------------- the service a web link is on (#305)
    # LinkedIn, a Mastodon server, somebody's Forgejo: a link's `service` is a key into a
    # registry, and a `link-service` plugin adds entries to it. `LinkService` is one entry
    # -- a key, a name, the kind of link, the hosts, the pattern an address's path has
    # there, an icon and an example -- and holds no model, so a plugin builds its table at
    # import. `link_service_of` reads one back by key, or `None` for *Other* and for a key
    # nothing installed knows.
    "LinkService": ("postulo.core.link_services", "Service"),
    "link_service_of": ("postulo.core.link_services", "find"),
    # ------------------------------------------- the service a messaging handle is on (#682)
    # Matrix, XMPP, Signal, an IRC network: a handle's `service` is a key into a registry a
    # `messaging-service` plugin adds entries to. `MessagingService` is one entry -- a key,
    # a name, the pattern a handle has there, how it is normalised, an icon and an example
    # -- and holds no model, so a plugin builds its table at import. `messaging_service_of`
    # reads one back by key, or `None` for *Other* and for a key nothing installed knows.
    "MessagingService": ("postulo.core.messaging_services", "Service"),
    "messaging_service_of": ("postulo.core.messaging_services", "find"),
    # ------------------------------------------------------------------- vCard text (#660)
    # RFC 6350 for a contact, written and read by the one mapping Postulo's own export and
    # import use, so a card a plugin writes and one Postulo writes are the same card and the
    # two cannot disagree. Plain values in and text out, and text in and plain values out:
    # `VCard` is a card as values (with `VCardPhone`, `VCardAddress` and `VCardLink` for the
    # rows on it), `vcard_from_values(card)` its vCard 4.0 text, `vcards_from_text(text)` the
    # cards of a file -- 3.0 and 4.0, bounded, and `VCardRefused` for one it will not read.
    # Nothing a card names is fetched. Writing 3.0 for a book that lists no 4.0 stays the
    # plugin's own small step down.
    "VCard": ("postulo.core.vcard", "Card"),
    "VCardAddress": ("postulo.core.vcard", "Address"),
    "VCardLink": ("postulo.core.vcard", "Link"),
    "VCardPhone": ("postulo.core.vcard", "Phone"),
    "VCardRefused": ("postulo.core.vcard", "VCardRefused"),
    "vcard_from_values": ("postulo.core.vcard", "card_to_text"),
    "vcards_from_text": ("postulo.core.vcard", "read"),
    # ------------------------------------------------------------------- calendar text
    # RFC 5545 for one interview, written the way Postulo's own feed writes it, so an event
    # pushed to somebody's calendar by a plugin and one they subscribed to are the same
    # event. `alarm=True` adds the interview's reminder as a VALARM.
    "calendar_status": ("postulo.applications.ical", "calendar_status"),
    "event_lines": ("postulo.applications.ical", "event_lines"),
    # ------------------------------------------------------- what a page is read against
    # A source reading a page with no structured data has two questions only Postulo can
    # answer (#267): is this three-letter word a currency a job advert carries -- the list
    # the spreadsheet importer reads salaries with, one list rather than two -- and is this
    # part of a title a place, which the offline city table the map uses answers, or answers
    # with ``None`` where it does not know or has not been downloaded.
    "COUNTRY_CODES": ("postulo.core.phones", "COUNTRY_CODES"),
    "CURRENCY_CODES": ("postulo.core.currencies", "CURRENCY_CODES"),
    "place_of": ("postulo.jobs.places", "resolve"),
    # ------------------------------------------------------------- what a language is
    # A BCP 47 tag in its canonical form, `pt-BR`, and never anything else (#337). A plugin
    # meets a code wherever the outside does -- a page's `lang`, a file's locale, a
    # mailbox's header -- and these are Postulo's own writer, its check of the shape, and
    # its comparison, so that a plugin does not grow a fifth way of lower-casing one.
    "is_language_tag": ("postulo.core.languages", "well_formed"),
    "language_match": ("postulo.core.languages", "match"),
    "language_matches": ("postulo.core.languages", "matches"),
    "language_override": ("postulo.core.languages", "override"),
    "language_tag": ("postulo.core.languages", "tag"),
}


def __getattr__(name: str):
    """The half of the surface that cannot be imported at module load.

    `OwnedModel` and `OwnedQuerySet` are Django models, and this module is imported from
    `plugins/base.py`'s neighbourhood — long before the app registry is ready. The rest are
    cheap but kept here for one rule rather than two: everything below is looked up when it
    is asked for, so importing this module costs nothing and never touches the database.
    """
    if name in _ELSEWHERE:
        import importlib

        module, attribute = _ELSEWHERE[name]
        return getattr(importlib.import_module(module), attribute)
    if name in ("OwnedModel", "OwnedQuerySet"):
        from postulo.core import models

        return getattr(models, name)
    if name == "Record":
        # What an importer's `read` fills in: a career in Postulo's terms rather than the
        # file's, so that every importer fills the same one and the review screen and the
        # writer need to know about none of them. A Django-free dataclass, but it lives in
        # the resume app, which is why it is looked up here rather than imported (#105).
        from postulo.resume.importing import Record

        return Record
    if name == "safe_next":
        # Every redirect a plugin makes goes through this, or a plugin becomes a way to
        # bounce somebody off the instance.
        from postulo.core.redirects import safe_next

        return safe_next
    if name == "client":
        # Every outbound request goes through this, or a plugin becomes a way to make the
        # server dial an address it should not. `docs/THREAT-MODEL.md` rule 5.
        from .http import client

        return client
    if name == "public_only_client":
        # For what is public by definition -- a posting, a portfolio, a logo, a browser's push
        # service. The operator's decision about *connections* is not an answer for those, so
        # this one refuses a private address however that switch is set (#215, #216).
        from .http import public_only_client

        return public_only_client
    if name in ("approve_host", "check_destination", "DestinationRefused"):
        # The same rule for a plugin that does not speak HTTP: `approve_host` resolves a name,
        # holds every address it answers with to the instance's policy, and hands back the one
        # to dial, so a mailbox or a queue connects where an HTTP client would have been
        # allowed to. `check_destination` answers the question without connecting, and
        # `DestinationRefused` is what both raise (#215).
        from . import http

        return getattr(http, name)
    if name in ("Theme", "ThemeKind"):
        # How a plugin sets a document: one `Theme` per way of setting it, declaring which
        # kinds it can set by having a template for each. The templates live in a
        # `templates/` directory beside the package and go on Django's search path when the
        # plugin is registered -- which is the only way markup reaches the renderer (#132).
        from postulo.documents import themes

        return themes.Kind if name == "ThemeKind" else themes.Theme
    if name in ("DocumentFormat", "register_document_format", "Outline"):
        # A format a document can be downloaded in beside the PDF: a key, a label, an
        # extension, a media type and a function from an `Outline` -- the document's words,
        # not its page -- to bytes. Registering one puts a download beside the others on
        # every CV and letter, with the properties choice already applied to the outline it
        # is handed (#480).
        from postulo.documents import formats, outline

        return {
            "DocumentFormat": formats.Format,
            "register_document_format": formats.register,
            "Outline": outline.Outline,
        }[name]
    if name == "access_token":
        # For a connection that authenticates by consent: ask for the token at the moment of
        # use, never keep one, because refreshing is the part that has to happen then (#150).
        from .consent import access_token

        return access_token
    if name == "ACCESS_TOKEN":
        # Where a consent plugin finds its token in the settings it is handed. A plugin whose
        # `send` is given settings rather than the connection cannot call `access_token`
        # itself, so Postulo renews the token first -- before a send and before a test -- and
        # this is the key it is then under (#151).
        from .consent import ACCESS_TOKEN

        return ACCESS_TOKEN
    raise AttributeError(f"{name!r} is not part of the plugin surface. See {_GUIDE}")
