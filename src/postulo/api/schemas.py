"""The shapes the API speaks: what goes out, and what may come in.

Output schemas are built from model instances by hand rather than through ModelSchema, so
the API's surface is exactly what is written here and a new model field never leaks into
it by accident. Inputs are validated by pydantic the same way the plugin contract is.

Every row a list can return carries ``updated_at``: it is what the next ``updated_since``
is asked with, and a cursor a client cannot read is no cursor at all (#230).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated

from django.core.exceptions import ValidationError
from django.urls import reverse
from ninja import Field, Schema
from pydantic import AfterValidator, AwareDatetime, BeforeValidator

from postulo.accounts.models import Profile, User
from postulo.core import personal, phones
from postulo.core.addresses import web_address
from postulo.core.postal import printed_location
from postulo.jobs.history import BODY_MAX_CHARS, EXTERNAL_ID_MAX_CHARS, SUMMARY_MAX_CHARS
from postulo.jobs.models import currency_code

#: An address that is going to be stored and later drawn as a link. The routers set these
#: with ``setattr`` rather than through a form, so this annotation is where the check the
#: form would have made happens instead -- and a `javascript:` address is refused with a
#: 422 naming the field rather than kept for a template to render (#218).
WebAddress = Annotated[str, AfterValidator(web_address)]


def _stripped(value):
    return value.strip() if isinstance(value, str) else value


def _without_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Null characters are not allowed.")
    return value


def _line(longest: int, *, shortest: int = 0):
    """One line of text, on the terms a page takes it: a person's name on *Your details*,
    what a link is called on a row.

    The page strips what was typed and then measures it, and refuses a NUL character; this
    does the same in the same order, so the two doors agree about what a value is. Before
    it, a location of 120 characters after a space was saved by the page and refused here,
    and a NUL was refused by the page and stored here -- where PostgreSQL, which cannot
    hold one in text, would have answered with a 500 instead of a 422 naming the field.

    **The length is written first, and that is not a matter of taste.** Each annotation
    wraps the ones before it, so at run time the strip is still the first thing to happen.
    But a length written after the strip is laid over the strip rather than given to the
    string, and a string with no bound of its own is passed through unread: half a
    surrogate pair, which pydantic otherwise refuses, then reaches the database and comes
    back as a 500. Written first, the bound is the string's own, and the string is read.
    """
    return Annotated[
        str,
        Field(min_length=shortest, max_length=longest),
        BeforeValidator(_stripped),
        AfterValidator(_without_nul),
    ]


#: A name the forms require: stripped, then at least one character (#438).
def _required(longest: int):
    return _line(longest, shortest=1)


# ------------------------------------------------------------------ companies


#: The schemes a company's identifier can be under, for both descriptions below. Not an
#: enumeration, deliberately: an instance may define schemes of its own (#311), and a list
#: frozen into the description of the API would call their keys invalid.
_COMPANY_SCHEMES = (
    "wikidata, isni, linkedin, lei, register, crunchbase, opencorporates, other; or the key "
    "of a scheme this instance defines for itself, which an administrator adds under Server "
    "settings and which is taken and checked as one of these is"
)


class IdentifierOut(Schema):
    scheme: str = Field(
        description=(
            f"{_COMPANY_SCHEMES}. A scheme the instance has since deleted keeps its key "
            "here, with no url"
        )
    )
    value: str
    label: str = Field(default="", description="What the identifier is, for scheme 'other'")
    url: str = Field(default="", description="Where the value links, when the scheme has a home")


class IdentifierIn(Schema):
    scheme: str = Field(max_length=20, description=_COMPANY_SCHEMES)
    value: str = Field(
        max_length=200,
        description=(
            "For one of Postulo's own schemes a pasted address is accepted, and the id is "
            "kept. A scheme the instance defines for itself takes the value alone: it has "
            "a pattern and no address to lift a value out of. An identifier sent back "
            "exactly as it was listed is kept as it is, whatever its scheme says now"
        ),
    )
    label: str = Field(default="", max_length=60)


class CompanyOut(Schema):
    id: int
    name: str
    #: ``employer``, or ``employment_service`` for the office the person is registered with.
    kind: str = "employer"
    website: str = ""
    careers_url: str = ""
    location: str = ""
    industries: list[str] = Field(default_factory=list)
    identifiers: list[IdentifierOut] = Field(default_factory=list)
    notes: str = ""
    created_at: dt.datetime
    updated_at: dt.datetime


def _a_country(value: str) -> str:
    """An ISO 3166-1 alpha-2 code the telephone table knows, in capitals; or nothing."""
    code = (value or "").strip().upper()
    if code and code not in phones.BY_CODE:
        raise ValueError(f"{value!r} is not an ISO 3166-1 alpha-2 country code")
    return code


#: The country a national telephone number is in (#304).
PhoneCountry = Annotated[str, AfterValidator(_a_country)]


def _a_currency(value: str) -> str:
    """Three letters in capitals, as `JobPosting.salary_currency` holds them (#446); or nothing."""
    code = (value or "").strip().upper()
    try:
        currency_code(code)
    except ValidationError as refusal:
        raise ValueError(refusal.messages[0]) from refusal
    return code


#: A currency a salary is stated in.
CurrencyCode = Annotated[str, AfterValidator(_a_currency)]


class PhoneNumberOut(Schema):
    kind: str = ""
    label: str = ""
    #: As it is stored: the international form, digits and nothing else, where it has one.
    number: str
    #: The same number grouped the way its own country writes it, for showing to somebody
    #: (#304). Read-only: it is drawn from `number` and is never what a client sends.
    formatted: str = ""
    is_primary: bool = False


class WebLinkOut(Schema):
    kind: str = Field(description="social, repository or website")
    service: str = Field(
        default="",
        description=(
            "The service the address is on, by its key: linkedin, mastodon, github and "
            "the others this instance knows. Blank is Other (#305)."
        ),
    )
    label: str = Field(default="", description="What the link is called, for Other")
    url: str
    is_primary: bool = False


#: What a link is called under *Other*: as long as ``WebLink.label``, and read as the row
#: on the page reads it. A plain string of that length kept a NUL, which PostgreSQL cannot.
_LinkNameLine = _line(60)

#: How many links one contact is handed with. The number a candidate file may hold of them
#: (``resume.candidate.MAX_CONTACT_ROWS``): each is checked against a pattern and written
#: in the request that brings it, and a list with no end is a request with none.
MAX_WEB_LINKS = 20


class WebLinkIn(Schema):
    kind: str = Field(description="social, repository or website")
    url: WebAddress = Field(min_length=1, max_length=500)
    service: str = Field(
        default="",
        max_length=40,
        description=(
            "A service's key, and the address then has to be one of that service's; "
            "'other' for any address; blank to have it worked out from the address's host."
        ),
    )
    label: _LinkNameLine = Field(default="", description="What to call it, for 'other'")
    is_primary: bool = Field(
        default=False, description="The one of its kind to show. The first of a kind otherwise."
    )


class ContactOut(Schema):
    id: int
    company_id: int
    name: str
    role: str = ""
    email: str = ""
    #: The primary number, where the single number has always been, so a client written
    #: against the earlier shape keeps working. `phone_numbers` is the whole list.
    phone: str = ""
    phone_numbers: list[PhoneNumberOut] = Field(default_factory=list)
    #: The primary social profile, where the single LinkedIn address has always been, so a
    #: client written against the earlier shape keeps working. `web_links` is the whole
    #: list, of every kind (#189).
    linkedin_url: str = ""
    web_links: list[WebLinkOut] = Field(default_factory=list)
    notes: str = ""


class CompanyDetailOut(CompanyOut):
    contacts: list[ContactOut]
    listing_ids: list[int]


class CompanyIn(Schema):
    name: _required(200)
    kind: str = Field(
        default="",
        description="employer (the default) or employment_service; anything else is ignored.",
    )
    website: WebAddress = Field(default="", max_length=200)
    careers_url: WebAddress = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    industries: list[str] = Field(
        default_factory=list, description="Names; unknown ones join the owner's vocabulary."
    )
    identifiers: list[IdentifierIn] = Field(
        default_factory=list,
        description="Added to the company; a Wikidata id also matches an existing company.",
    )
    notes: str = ""


class CompanyPatch(Schema):
    name: _required(200) | None = None
    website: WebAddress | None = Field(default=None, max_length=200)
    careers_url: WebAddress | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    industries: list[str] | None = Field(default=None, description="Replaces the whole list")
    identifiers: list[IdentifierIn] | None = Field(
        default=None, description="Replaces the whole list"
    )
    notes: str | None = None


class ContactIn(Schema):
    name: _required(200)
    role: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    #: Checked against its country's numbering plan (#304), as the field on the page is.
    phone: str = Field(
        default="",
        max_length=40,
        description=(
            "The contact's telephone number: in the international form, starting with `+`, "
            "or the way it is written in its own country with that country in "
            "`phone_country`. It is checked against the country's numbering plan. A number "
            "that cannot exist there is a 422 saying why, and so is a national number sent "
            "with neither a `+` nor `phone_country`. An accepted number is stored, and "
            "returned, in the international form (`+33612345678`), not as it was sent; one "
            "of a country's short numbers has no such form and is kept as it was sent."
        ),
    )
    #: The chooser beside the box, for a client: which country a national number is in.
    phone_country: PhoneCountry = Field(
        default="",
        max_length=2,
        description=(
            "The country `phone` is in, as an ISO 3166-1 alpha-2 code (`FR`), for a number "
            "written the national way. Ignored when `phone` starts with `+` or `00`, which "
            "say their own country. A code that is not a country is a 422."
        ),
    )
    #: Saved as the contact's primary social profile (#189), and drawn as a link on the
    #: company's page, so it is checked like every other address that is (#218).
    linkedin_url: WebAddress = Field(default="", max_length=500)
    #: The contact's links of every kind, each on a service or on none (#305). The single
    #: address above is still taken, as a social profile, beside whatever is listed here.
    web_links: list[WebLinkIn] = Field(
        default_factory=list,
        max_length=MAX_WEB_LINKS,
        description=(
            "Social profiles, code repositories and websites, each with its service. "
            "Twenty at most."
        ),
    )
    notes: str = ""


# ------------------------------------------------------------------- listings


class CompanyRef(Schema):
    id: int
    name: str


class ListingOut(Schema):
    """A job posting with its company and state. `isco_code` is the ISCO-08 unit group the
    title matches in the ESCO classification, empty where it matches nothing (#266)."""

    id: int
    company: CompanyRef
    title: str
    isco_code: str = ""
    location: str = ""
    remote_type: str = ""
    employment_type: str = ""
    url: str = ""
    source: str = ""
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str = ""
    salary_period: str = ""
    posted_at: dt.date | None = None
    closes_at: dt.date | None = None
    closed_at: dt.datetime | None = None
    state: str
    discard_reason: str = ""
    noted_at: dt.datetime
    decided_at: dt.datetime | None = None
    application_ids: list[int]
    web_url: str
    updated_at: dt.datetime


class ListingEventOut(Schema):
    """One entry in a listing's history (#270). Every word in it may be a stranger's."""

    id: int
    listing_id: int
    kind: str = Field(description="note, email_received, call, message, document, capture or other")
    occurred_at: dt.datetime
    summary: str = ""
    body: str = ""
    contact_id: int | None = Field(default=None, description="Who it came from, if recorded")
    document_id: int | None = Field(
        default=None, description="The uploaded file it points at, while that file exists"
    )
    capture_id: int | None = Field(
        default=None, description="The capture it points at, for a capture entry"
    )
    external_id: str = Field(default="", description="What the source called the thing")
    actor: str = ""
    created_at: dt.datetime


class ListingDetailOut(ListingOut):
    description: str = ""
    #: What arrived about the listing, newest first (#270). An application's own timeline is
    #: on the application; this is the half before and beside it.
    events: list[ListingEventOut] = Field(default_factory=list)


class ListingChoiceOut(Schema):
    """A listing, briefly: enough to choose one to file something into, and no more (#270).

    What a token holding only `listings:bind` reads. No description, no salary, no
    applications: a mail client choosing where a message goes needs to recognise the job,
    not to read the person's search.
    """

    id: int
    title: str
    company_name: str
    location: str = ""
    state: str
    noted_at: dt.datetime
    updated_at: dt.datetime


class ListingEventIn(Schema):
    """An entry for a listing's history: what arrived, or what was said (#270).

    The text is foreign text and is held to a length (#218): a longer summary or body is
    refused rather than cut, so a client learns to send less instead of losing the end.
    """

    kind: str = Field(
        default="note",
        description="note (the default), email_received, call, message, document or other",
    )
    summary: str = Field(
        default="", max_length=SUMMARY_MAX_CHARS, description="The line the history shows"
    )
    body: str = Field(
        default="",
        max_length=BODY_MAX_CHARS,
        description="The message, the email's text, or what was said. At most 40,000 characters.",
    )
    occurred_at: AwareDatetime | None = Field(default=None, description="Now, if unset")
    contact_id: int | None = Field(
        default=None, description="Who it came from: one of your contacts"
    )
    document_id: int | None = Field(
        default=None,
        description="A file of yours it is about. Needed for a document entry.",
    )
    external_id: str = Field(
        default="",
        max_length=EXTERNAL_ID_MAX_CHARS,
        description=(
            "What the source calls the thing -- a message id, say. Sent again with the same "
            "one for the same listing, the first entry is the answer (200) and nothing is "
            "added."
        ),
    )


class ListingIn(Schema):
    """The posting half of intake: company by name, and what the listing says."""

    company_name: _required(200)
    company_wikidata: str = Field(
        default="",
        max_length=200,
        description="The employer's Wikidata id, when known: a stronger match than the name.",
    )
    title: _required(250)
    url: WebAddress = Field(default="", max_length=500)
    location: str = Field(default="", max_length=200)
    remote_type: str = Field(default="", max_length=20)
    employment_type: str = Field(default="", max_length=20)
    source: str = Field(default="", max_length=120)
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: CurrencyCode = Field(default="EUR", max_length=3)
    salary_period: str = Field(default="year", max_length=10)
    closes_at: dt.date | None = None
    description: str = ""

    def posting_data(self) -> dict:
        """Only the listing's own fields — the one-step schema below adds more."""
        return {
            name: getattr(self, name)
            for name in ListingIn.model_fields
            if name not in ("company_name", "company_wikidata")
        }


class DiscardIn(Schema):
    reason: str = Field(default="other", max_length=20)


# --------------------------------------------------------------- applications


class ApplicationDetailsIn(Schema):
    """The person's side of an application."""

    status: str = "applied"
    channel: str = ""
    priority: int = 2
    deadline: dt.date | None = None
    applied_on: dt.date | None = Field(
        default=None,
        description=(
            "When it was actually sent. Omitted, it is today — which is wrong for a search "
            "already under way, and every figure measured from this date with it (#222)."
        ),
    )
    tags: list[str] = Field(default_factory=list, description="Tag names; unknown ones are made.")
    referred_by_id: int | None = Field(
        default=None,
        description="The contact who referred you: one of your own, at any company or none.",
    )
    through_agency_id: int | None = Field(
        default=None,
        description=(
            "The recruitment agency it went through: one of your own companies. The "
            "listing's company stays the employer."
        ),
    )

    def application_data(self) -> dict:
        from postulo.applications.services import moment_for

        return {
            "status": self.status,
            "channel": self.channel,
            "priority": self.priority,
            "deadline": self.deadline,
            "applied_at": moment_for(self.applied_on),
        }


class ApplicationIn(ListingIn, ApplicationDetailsIn):
    """Record an application in one step: the listing and the person's side together."""


class EventOut(Schema):
    id: int
    kind: str
    occurred_at: dt.datetime
    summary: str = ""
    body: str = ""
    from_status: str = ""
    to_status: str = ""
    end_reason: str = Field(
        default="",
        description=(
            "Why the application ended, on the entry that ended it: pay, location, "
            "not_a_match, filled_internally, my_choice or other. Empty on every other entry."
        ),
    )
    actor: str = ""


class ReminderOut(Schema):
    id: int
    application_id: int | None = None
    summary: str
    due_at: dt.datetime
    done_at: dt.datetime | None = None
    notified_at: dt.datetime | None = None
    updated_at: dt.datetime


class ListingRef(Schema):
    id: int
    title: str
    company: CompanyRef


class InterviewOut(Schema):
    id: int
    uid: str = Field(description="Stable across edits; what a calendar keys the meeting by")
    application_id: int
    kind: str
    starts_at: dt.datetime
    ends_at: dt.datetime
    location: str = ""
    contact_ids: list[int]
    notes: str = ""
    outcome: str
    reminder_id: int | None = None
    web_url: str
    calendar_url: str
    updated_at: dt.datetime


class InterviewIn(Schema):
    application_id: int
    kind: str = "video"
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = Field(
        default=None, description="An hour after the start if unset"
    )
    location: str = Field(default="", max_length=500)
    contact_ids: list[int] = Field(default_factory=list)
    notes: str = ""
    remind: bool = Field(default=True, description="Make a reminder for the day before")


class InterviewPatch(Schema):
    """Only the fields sent change; a `null` means "leave it", as on every other patch."""

    kind: str | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    location: str | None = Field(default=None, max_length=500)
    contact_ids: list[int] | None = None
    notes: str | None = None


class InterviewOutcomeIn(Schema):
    outcome: str = Field(description="done, cancelled or no_show")
    note: str = ""


class ApplicationOut(Schema):
    id: int
    listing: ListingRef
    status: str
    channel: str = ""
    priority: int
    applied_at: dt.datetime | None = None
    deadline: dt.date | None = None
    closed_at: dt.datetime | None = None
    contact_id: int | None = None
    referred_by_id: int | None = None
    through_agency_id: int | None = None
    #: How it ended, read from the timeline and stored nowhere else (#239). All three are
    #: empty while the application is live, and on one that was accepted.
    end_reason: str = Field(
        default="",
        description=(
            "Why it ended, for an application that is rejected, withdrawn or ghosted: the "
            "latest reason on its timeline since it ended. Empty where none was given."
        ),
    )
    end_note: str = Field(default="", description="What was written beside the ending.")
    last_stage: str = Field(
        default="",
        description=(
            "The status it had reached when it ended, read from the timeline. Empty where "
            "the timeline does not say."
        ),
    )
    tags: list[str]
    next_interview_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    web_url: str


# --------------------------------------------------------------------- offers


class OfferOut(Schema):
    id: int
    application_id: int
    base_amount: Decimal | None = None
    currency: str = ""
    period: str
    yearly_amount: Decimal | None = Field(
        None, description="The base pay as a year's worth, within its currency"
    )
    variable_pay: str = ""
    equity: str = ""
    benefits: str = ""
    location: str = ""
    holidays: int | None = None
    starts_on: dt.date | None = None
    answer_by: dt.date | None = None
    notes: str = ""
    reminder_id: int | None = None
    web_url: str
    created_at: dt.datetime
    updated_at: dt.datetime


class ApplicationDetailOut(ApplicationOut):
    events: list[EventOut]
    reminders: list[ReminderOut]
    interviews: list[InterviewOut]
    offers: list[OfferOut]
    sent_document_ids: list[int]


class StatusIn(Schema):
    status: str
    note: str = ""
    end_reason: str = Field(
        default="",
        description=(
            "Why it ended, with rejected, withdrawn or ghosted: pay, location, not_a_match, "
            "filled_internally, my_choice or other. Sent with the status the application "
            "already has, it is a reason learnt afterwards and is added to the timeline."
        ),
    )


class EventIn(Schema):
    kind: str = "note"
    summary: str = Field(default="", max_length=250)
    body: str = ""
    occurred_at: AwareDatetime | None = None


class ReminderIn(Schema):
    application_id: int | None = None
    summary: _required(250)
    due_at: AwareDatetime


class ReminderPatch(Schema):
    """What may be changed about a reminder, and nothing more (#238).

    Every field optional and read with ``exclude_unset``, so a client that sends only
    ``due_at`` moves the reminder and leaves the rest alone -- the difference between "no
    application" and "do not touch the application" is the difference between a `PATCH` and
    a replacement, and `application_id: None` means the first.

    ``done_at`` is not here. Marking one done goes through ``/complete``, which is a verb
    rather than a column, and a client that could write the stamp directly could write one
    in the future or in the wrong order.
    """

    application_id: int | None = None
    summary: _required(250) | None = None
    due_at: AwareDatetime | None = None


# ------------------------------------------------------------------ documents


class CVOut(Schema):
    id: int
    name: str
    headline: str = ""
    summary: str = ""
    theme: str
    language: str = Field(
        description="The language it is written in, as a BCP 47 tag in its canonical form "
        "(`pt-BR`); blank follows the profile",
    )
    item_count: int
    updated_at: dt.datetime


class CVItemOut(Schema):
    kind: str
    label: str
    included: bool


class CVRowOut(Schema):
    """One of the owner's own rows a CV may be told to print (#308)."""

    id: int
    label: str = Field(
        description=(
            "As *Your details* names it: a number with its kind, an address with the name "
            "it was given, an identifier with its scheme"
        )
    )
    value: str = Field(description="What a document prints for it")


class CVPrintsOneOut(Schema):
    """A kind a CV prints at most one of: a number, an address, a link of one kind."""

    choice: str = Field(
        description=(
            "`default` follows your details (the primary, or the account's address); "
            "`chosen` pins the row named by `id`; `none` prints none of this kind"
        )
    )
    id: int | None = Field(
        default=None,
        description=(
            "The row pinned while `choice` is `chosen`. Null with `chosen` means the row "
            "has since been deleted from your details, or is kept back and not offered "
            "at present: the CV prints none of this kind"
        ),
    )
    printed: str = Field(default="", description="What the CV prints as things stand; empty: none")
    offered: list[CVRowOut] = Field(
        default_factory=list,
        description=(
            "Your own rows of this kind, any of which may be pinned. Left out of the "
            "answer to a `PATCH` made with a token that does not hold `read`"
        ),
    )


class CVPrintsIdentifiersOut(Schema):
    choice: str = Field(
        description=(
            "`default` prints every identifier, including one added later; `chosen` "
            "prints exactly the rows in `ids`; `none` prints none"
        )
    )
    ids: list[int] = Field(default_factory=list)
    printed: list[str] = Field(default_factory=list)
    offered: list[CVRowOut] = Field(
        default_factory=list,
        description=(
            "Your own identifiers, any of which may be pinned. Left out of the answer to "
            "a `PATCH` made with a token that does not hold `read`"
        ),
    )


class CVPrintsOut(Schema):
    """Which of the owner's details a CV prints (#308). Read when the document is drawn, so
    a version already sent is not changed by changing any of it."""

    phone: CVPrintsOneOut
    email: CVPrintsOneOut
    social: CVPrintsOneOut
    repository: CVPrintsOneOut
    website: CVPrintsOneOut
    identifiers: CVPrintsIdentifiersOut
    location: bool = Field(
        description="Whether the location is printed: the profile's `printed_location`"
    )
    form_of_address: bool = Field(description="Whether it is printed before the name")
    pronouns: bool = Field(description="Whether they are printed after the name")
    birth_date: bool = Field(description="Whether the date of birth is printed (#679)")
    birth_place: bool = Field(description="Whether the place of birth is printed (#679)")


class CVDetailOut(CVOut):
    items: list[CVItemOut]
    show_contact_details: bool = Field(
        description="The master switch: false prints no name and none of `prints`"
    )
    prints: CVPrintsOut


class CVPrintsOneIn(Schema):
    choice: str = Field(description="default, chosen or none")
    id: int | None = Field(
        default=None,
        description=(
            "With `chosen`: one of the ids `offered` lists, which `GET /cvs/{id}` gives. "
            "Left out otherwise"
        ),
    )


class CVPrintsIdentifiersIn(Schema):
    choice: str = Field(description="default, chosen or none")
    ids: list[int] = Field(
        default_factory=list,
        max_length=200,
        description="With `chosen`: ids `offered` lists. Left out otherwise",
    )


class CVPrintsIn(Schema):
    """A kind left out is left alone."""

    phone: CVPrintsOneIn | None = None
    email: CVPrintsOneIn | None = None
    social: CVPrintsOneIn | None = None
    repository: CVPrintsOneIn | None = None
    website: CVPrintsOneIn | None = None
    identifiers: CVPrintsIdentifiersIn | None = None
    location: bool | None = None
    form_of_address: bool | None = None
    pronouns: bool | None = None
    birth_date: bool | None = None
    birth_place: bool | None = None


class CVPatch(Schema):
    """What may be changed about a CV through the API: what it prints of its owner's
    details. A field left out is left alone."""

    show_contact_details: bool | None = None
    prints: CVPrintsIn | None = None


class LetterOut(Schema):
    id: int
    name: str
    subject: str = ""
    is_template: bool
    theme: str
    #: As `CVOut` has carried all along; the field has been on the model since the
    #: beginning and only the schema had forgotten it (#283). It has no default, so a
    #: router that forgets the key fails its tests instead of sending a plausible blank
    #: (#437).
    language: str = Field(
        description="The language it is written in, as a BCP 47 tag in its canonical form "
        "(`pt-BR`); blank follows the profile",
    )
    created_at: dt.datetime
    updated_at: dt.datetime


class LetterDetailOut(LetterOut):
    body: str


class LetterIn(Schema):
    name: _required(120)
    subject: str = Field(default="", max_length=250)
    body: str
    is_template: bool = False


class DocumentOut(Schema):
    id: int
    source: str = Field(description="'upload' for a file you had; 'rendered' for a snapshot")
    kind: str
    title: str
    #: Empty means nobody has said, for an upload, or that the snapshot predates the
    #: column, for a render (#283). Never a guess.
    language: str = Field(
        default="",
        description="The language it is in, as a BCP 47 tag in its canonical form (`pt-BR`); "
        "blank where nobody has said",
    )
    application_id: int | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    download_url: str


class SearchHitOut(Schema):
    id: int
    title: str
    subtitle: str = ""
    excerpt: str = ""
    web_url: str


class SearchGroupOut(Schema):
    kind: str
    label: str
    total: int
    hits: list[SearchHitOut]


class TokenOut(Schema):
    name: str
    owner: str
    scopes: list[str]
    time_zone: str = Field(
        description="The owner's zone, so a client can build the offset a time must carry"
    )
    expires_at: dt.datetime | None = None
    last_used_at: dt.datetime | None = None


# -------------------------------------------------------------------- profile


def _longest(model, name: str) -> int:
    return model._meta.get_field(name).max_length


#: The profile's own bounds, read from the columns rather than written out a second time, so
#: the API refuses with a 422 what the database would have refused less politely.
_NAME = _longest(User, "first_name")
_HEADLINE = _longest(Profile, "headline")
_LOCATION = _longest(Profile, "location")
_ADDRESSING = _longest(Profile, "form_of_address")
_BIRTH_PLACE = _longest(Profile, "birth_place")


def _held_to(rule):
    """A pydantic check that runs one of `core.personal`'s validators, the very function the
    column holds the page to, and says what it said as a 422 naming the field (#679)."""

    def check(value):
        try:
            rule(value)
        except ValidationError as refused:
            raise ValueError(" ".join(refused.messages)) from refused
        return value

    return AfterValidator(check)


def _capitals(value):
    return value.upper() if isinstance(value, str) else value


class ProfileOut(Schema):
    """Your details: the name and the contact block a CV prints (#309)."""

    first_name: str
    last_name: str
    form_of_address: str = Field(
        default="",
        description=(
            "Written before the name (Mr, Mme, Eng.ª), as the text itself; blank when not "
            "given. Printed only on a CV whose `prints.form_of_address` is true."
        ),
    )
    pronouns: str = Field(
        default="",
        description=(
            "How to refer to the person (she/her, iel), as the text itself; blank when not "
            "given. Printed only on a CV whose `prints.pronouns` is true."
        ),
    )
    birth_date: str = Field(
        default="",
        description=(
            "When the person was born, as an ISO 8601 reduced date -- `1990`, `1990-03` or "
            "`1990-03-12` -- or blank when not given. Never worked out, never an age. Printed "
            "only on a CV whose `prints.birth_date` is true."
        ),
    )
    birth_place: str = Field(
        default="",
        description=(
            "The town or city of birth, as typed; blank when not given. Printed only on a CV "
            "whose `prints.birth_place` is true."
        ),
    )
    birth_country: str = Field(
        default="",
        description="The country of birth, as a two-letter code from the address form's list",
    )
    headline: str = ""
    location: str = Field(
        default="",
        description=(
            "As typed, and stored. Blank means the town and country of the primary postal "
            "address; `printed_location` is what that comes to."
        ),
    )
    printed_location: str = Field(
        default="",
        description=(
            "Read-only: what a CV prints as where you are. `location` when it says "
            "anything, otherwise the town and country of the primary postal address, "
            "otherwise nothing. Never the street."
        ),
    )
    record_language: str = Field(
        default="",
        description="The language the career record is written in, as a BCP 47 tag in its "
        "canonical form (`pt-BR`); blank is the interface's",
    )
    updated_at: dt.datetime


_NameLine = _line(_NAME)
_AddressingLine = _line(_ADDRESSING)
_HeadlineLine = _line(_HEADLINE)
_LocationLine = _line(_LOCATION)
_BirthDateLine = Annotated[
    _line(personal.BIRTH_DATE_LENGTH), _held_to(personal.validate_birth_date)
]
_BirthPlaceLine = _line(_BIRTH_PLACE)
_CountryCode = Annotated[
    str,
    Field(max_length=2),
    BeforeValidator(_stripped),
    BeforeValidator(_capitals),
    _held_to(personal.validate_country_code),
]


class ProfilePatch(Schema):
    """What may be changed. A field left out is left alone; `printed_location` is read-only
    and ignored if sent. Each is stripped of the space around it before it is measured."""

    first_name: _NameLine | None = None
    last_name: _NameLine | None = None
    form_of_address: _AddressingLine | None = Field(
        default=None,
        description="Any text, including one no list offers; empty clears it",
    )
    pronouns: _AddressingLine | None = Field(
        default=None,
        description="Any text, including one no list offers; empty clears it",
    )
    birth_date: _BirthDateLine | None = Field(
        default=None,
        description=(
            "`1990`, `1990-03` or `1990-03-12`: a real date, not in the future and not before "
            "1900; empty clears it"
        ),
    )
    birth_place: _BirthPlaceLine | None = Field(
        default=None, description="A town or city as typed; empty clears it"
    )
    birth_country: _CountryCode | None = Field(
        default=None, description="A country's two-letter code from the list; empty clears it"
    )
    headline: _HeadlineLine | None = None
    location: _LocationLine | None = Field(
        default=None,
        description="Empty goes back to the primary postal address's town and country",
    )


def profile_out(profile) -> dict:
    user = profile.user
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "form_of_address": profile.form_of_address,
        "pronouns": profile.pronouns,
        "birth_date": profile.birth_date,
        "birth_place": profile.birth_place,
        "birth_country": profile.birth_country,
        "headline": profile.headline,
        "location": profile.location,
        "printed_location": printed_location(profile),
        "record_language": profile.record_language,
        "updated_at": profile.updated_at,
    }


# ------------------------------------------------------------------- helpers


def company_ref(company) -> dict:
    return {"id": company.pk, "name": company.name}


def listing_out(request, posting, *, detail: bool = False) -> dict:
    data = {
        "id": posting.pk,
        "company": company_ref(posting.company),
        "title": posting.title,
        "isco_code": posting.isco_code,
        "location": posting.location,
        "remote_type": posting.remote_type,
        "employment_type": posting.employment_type,
        "url": posting.url,
        "source": posting.source,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_currency": posting.salary_currency,
        "salary_period": posting.salary_period,
        "posted_at": posting.posted_at,
        "closes_at": posting.closes_at,
        "closed_at": posting.closed_at,
        "state": posting.derived_state,
        "discard_reason": posting.discard_reason,
        "noted_at": posting.noted_at,
        "decided_at": posting.decided_at,
        "application_ids": [a.pk for a in posting.applications.all()],
        "web_url": request.build_absolute_uri(posting.get_absolute_url()),
        "updated_at": posting.updated_at,
    }
    if detail:
        from postulo.jobs.history import history_of

        data["description"] = posting.description
        data["events"] = [listing_event_out(event) for event in history_of(posting)]
    return data


def listing_event_out(event) -> dict:
    """One entry in a listing's history (#270). What it points at is named only while it is
    there and the listing's owner's, which is `ListingEvent.points_at`'s rule."""
    document = event.bound_document
    capture = event.bound_capture
    return {
        "id": event.pk,
        "listing_id": event.posting_id,
        "kind": event.kind,
        "occurred_at": event.occurred_at,
        "summary": event.summary,
        "body": event.body,
        "contact_id": event.contact_id,
        "document_id": document.pk if document is not None else None,
        "capture_id": capture.pk if capture is not None else None,
        "external_id": event.external_id,
        "actor": event.actor,
        "created_at": event.created_at,
    }


def listing_choice_out(request, posting) -> dict:
    """A listing, briefly, as `ListingChoiceOut` says (#270)."""
    return {
        "id": posting.pk,
        "title": posting.title,
        "company_name": posting.company.name,
        "location": posting.location,
        "state": posting.derived_state,
        "noted_at": posting.noted_at,
        "updated_at": posting.updated_at,
    }


def event_out(event) -> dict:
    """One timeline entry. Written once, because a list and a single call both answer it."""
    return {
        "id": event.pk,
        "kind": event.kind,
        "occurred_at": event.occurred_at,
        "summary": event.summary,
        "body": event.body,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "end_reason": event.end_reason,
        "actor": event.actor,
    }


def application_out(request, application, *, detail: bool = False) -> dict:
    posting = application.posting
    # Read from the timeline the caller loaded beside the row -- `with_status_log` for a
    # list, the whole of it for one application -- so a page of fifty asks once (#239).
    ending = application.ending
    data = {
        "id": application.pk,
        "listing": {
            "id": posting.pk,
            "title": posting.title,
            "company": company_ref(posting.company),
        },
        "status": application.status,
        "channel": application.channel,
        "priority": application.priority,
        "applied_at": application.applied_at,
        "deadline": application.deadline,
        "closed_at": application.closed_at,
        "contact_id": application.contact_id,
        "referred_by_id": application.referred_by_id,
        "through_agency_id": application.through_agency_id,
        "end_reason": ending.reason if ending else "",
        "end_note": ending.note if ending else "",
        "last_stage": ending.last_stage if ending else "",
        "tags": [tag.name for tag in application.tags.all()],
        "next_interview_at": getattr(application, "next_interview_at", None),
        "created_at": application.created_at,
        "updated_at": application.updated_at,
        "web_url": request.build_absolute_uri(application.get_absolute_url()),
    }
    if detail:
        data["events"] = [event_out(event) for event in application.events.all()]
        data["reminders"] = [reminder_out(r) for r in application.reminders.all()]
        data["interviews"] = [interview_out(request, i) for i in application.interviews.all()]
        data["offers"] = [offer_out(request, o) for o in application.offers.all()]
        data["sent_document_ids"] = [d.pk for d in application.rendered_documents.all()]
    return data


def interview_out(request, interview) -> dict:
    return {
        "id": interview.pk,
        "uid": interview.uid,
        "application_id": interview.application_id,
        "kind": interview.kind,
        "starts_at": interview.starts_at,
        "ends_at": interview.ends_at,
        "location": interview.location,
        "contact_ids": [c.pk for c in interview.contacts.all()],
        "notes": interview.notes,
        "outcome": interview.outcome,
        "reminder_id": interview.reminder_id,
        "web_url": request.build_absolute_uri(interview.get_absolute_url()),
        "calendar_url": request.build_absolute_uri(
            reverse("postulo-api:interview_calendar", kwargs={"pk": interview.pk})
        ),
        "updated_at": interview.updated_at,
    }


def reminder_out(reminder) -> dict:
    return {
        "id": reminder.pk,
        "application_id": reminder.application_id,
        "summary": reminder.summary,
        "due_at": reminder.due_at,
        "done_at": reminder.done_at,
        "notified_at": reminder.notified_at,
        "updated_at": reminder.updated_at,
    }


def company_out(company, *, detail: bool = False) -> dict:
    data = {
        "id": company.pk,
        "name": company.name,
        "kind": company.kind,
        "website": company.website,
        "careers_url": company.careers_url,
        "location": company.location,
        "industries": [industry.name for industry in company.industries.all()],
        "identifiers": [
            {"scheme": i.scheme, "value": i.value, "label": i.label, "url": i.url}
            for i in company.identifiers.all()
        ],
        "notes": company.notes,
        "created_at": company.created_at,
        "updated_at": company.updated_at,
    }
    if detail:
        data["contacts"] = [contact_out(c) for c in company.contacts.all()]
        data["listing_ids"] = [p.pk for p in company.postings.all()]
    return data


def _primary_number(holder) -> str:
    row = next((n for n in holder.phone_numbers.all() if n.is_primary), None)
    return row.number if row else ""


def _primary_link(holder, kind: str) -> str:
    row = next((n for n in holder.web_links.all() if n.is_primary and n.kind == kind), None)
    return row.url if row else ""


def contact_out(contact) -> dict:
    return {
        "id": contact.pk,
        "company_id": contact.company_id,
        "name": contact.name,
        "role": contact.role,
        "email": contact.email,
        # The primary stays where the single number always was, so a client written
        # against the old shape keeps working, and the whole list sits beside it.
        "phone": _primary_number(contact),
        "phone_numbers": [
            {
                "kind": row.kind,
                "label": row.label,
                "number": row.number,
                "formatted": phones.readable(row.number),
                "is_primary": row.is_primary,
            }
            for row in contact.phone_numbers.all()
        ],
        "linkedin_url": _primary_link(contact, "social"),
        "web_links": [
            {
                "kind": row.kind,
                "service": row.service,
                "label": row.label,
                "url": row.url,
                "is_primary": row.is_primary,
            }
            for row in contact.web_links.all()
        ],
        "notes": contact.notes,
    }


def document_out(request, document, *, source: str) -> dict:
    name = "postulo-api:document_download"
    return {
        "id": document.pk,
        "source": source,
        "kind": document.kind,
        "title": document.title,
        "language": getattr(document, "language", "") or "",
        "application_id": getattr(document, "application_id", None),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "download_url": request.build_absolute_uri(
            reverse(name, kwargs={"source": source, "pk": document.pk})
        ),
    }


def offer_out(request, offer) -> dict:
    return {
        "id": offer.pk,
        "application_id": offer.application_id,
        "base_amount": offer.base_amount,
        "currency": offer.currency,
        "period": offer.period,
        "yearly_amount": offer.yearly_amount,
        "variable_pay": offer.variable_pay,
        "equity": offer.equity,
        "benefits": offer.benefits,
        "location": offer.location,
        "holidays": offer.holidays,
        "starts_on": offer.starts_on,
        "answer_by": offer.answer_by,
        "notes": offer.notes,
        "reminder_id": offer.reminder_id,
        "web_url": request.build_absolute_uri(offer.get_absolute_url()),
        "created_at": offer.created_at,
        "updated_at": offer.updated_at,
    }
