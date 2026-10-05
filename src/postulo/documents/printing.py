"""Which of its owner's details one CV prints (#308).

A profile holds several telephone numbers, several addresses on the web of three kinds,
several email addresses and any number of identifiers, and until this every CV printed the
same ones: the primary of each, and all the identifiers. A CV now answers for itself, kind
by kind, and this module is the one place those answers are read and written.

**Read in one place.** `resolve` turns a CV's answers into what is printed, and
`rendering.contact_details` is its only caller on the way to a document -- so the CV, the
portfolio, the preview, the plain text, the Word file and a theme nobody here has seen are
all handed the same details, and none of them can disagree about a choice.

**Written through one door.** The form, the API and the importer each have their own way of
naming a row -- a `<select>`'s value, an id, a number as it is written -- and all three end
in `choose` and `choose_identifiers`, which accept a row only from `offered`. That list is
the owner's own rows and nothing else, so another person's row cannot be pinned by any
route; and `resolve` asks the same list again at every render, so a pin that reached the
column some other way still prints nothing.

**Offered means what *Your details* offers.** A number kept back because *Several telephone
numbers* is switched off does not exist as far as a document is concerned
(`core.phone_numbers` says why), and neither does a link of a kind whose feature is off. A
pin on such a row prints as a pin on a deleted one does: the kind prints none, and the page
says so. It differs in one thing, which is that it is still a pin: it prints again when the
row is offered again, so saving the CV's settings does not turn it into an answer of *none*
(`is_kept_back`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from postulo.accounts.identifiers import OTHER
from postulo.core import personal
from postulo.core.identifiers import PERSON, as_restored, find

from .models import CV, Prints


class NotOffered(ValueError):
    """What was asked for is not a choice this owner has: a row that is not theirs, or is
    not there, or an answer that is not one of the three.

    ``field`` is which part of the answer is refused -- ``choice``, ``id`` or ``ids`` -- so
    that the API can name it. The words are a whole sentence with a place for that name,
    translated like every other refusal the API makes (#393): a fragment to be glued
    after a path reads wrongly in most of the languages Postulo speaks.
    """

    def __init__(self, field: str, sentence, **values):
        super().__init__(field)
        self.field = field
        self._sentence = sentence
        self._values = values

    def sentence(self, named: str) -> str:
        """The refusal, with ``named`` as what the field is called where it was sent."""
        return str(self._sentence % {"field": named, **self._values})

    def __str__(self) -> str:
        return self.sentence(repr(self.field))


@dataclass(frozen=True)
class Detail:
    """One kind of detail a CV prints at most one of, and every sentence about it.

    Whole sentences per kind rather than one sentence with the kind dropped into a slot: a
    noun declined for a slot reads wrongly in most of the languages Postulo speaks (#168).
    """

    #: What the form, the API and the archive call it.
    key: str
    label: str
    #: The first choice: whatever the profile marks today.
    follow: str
    none: str
    #: What the page says when the pinned row is no longer there.
    gone: str
    #: What an API client is told when it names a row that is not the owner's.
    not_yours: str
    #: The kind of `core.WebLink`, for the three that are links.
    link_kind: str = ""

    @property
    def choice_field(self) -> str:
        return f"{self.key}_choice"

    @property
    def pin_field(self) -> str:
        return f"pinned_{self.key}"


DETAILS: tuple[Detail, ...] = (
    Detail(
        key="phone",
        label=_("Telephone number"),
        follow=_("Your primary number"),
        none=_("No telephone number"),
        gone=_(
            "The telephone number chosen for this CV is no longer in your details, so it "
            "prints none."
        ),
        not_yours=_("%(field)s is not one of your telephone numbers."),
    ),
    Detail(
        key="email",
        label=_("Email address"),
        follow=_("Your account's address"),
        none=_("No email address"),
        gone=_(
            "The email address chosen for this CV is no longer one of your confirmed "
            "addresses, so it prints none."
        ),
        not_yours=_("%(field)s is not one of your confirmed email addresses."),
    ),
    Detail(
        key="social",
        label=_("Social profile"),
        follow=_("Your primary social profile"),
        none=_("No social profile"),
        gone=_(
            "The social profile chosen for this CV is no longer in your details, so it prints none."
        ),
        not_yours=_("%(field)s is not one of your social profiles."),
        link_kind="social",
    ),
    Detail(
        key="repository",
        label=_("Code repository"),
        follow=_("Your primary code repository"),
        none=_("No code repository"),
        gone=_(
            "The code repository chosen for this CV is no longer in your details, so it "
            "prints none."
        ),
        not_yours=_("%(field)s is not one of your code repositories."),
        link_kind="repository",
    ),
    Detail(
        key="website",
        label=_("Website"),
        follow=_("Your primary website"),
        none=_("No website"),
        gone=_("The website chosen for this CV is no longer in your details, so it prints none."),
        not_yours=_("%(field)s is not one of your websites."),
        link_kind="website",
    ),
)

#: The same, by key.
BY_KEY: dict[str, Detail] = {detail.key: detail for detail in DETAILS}

#: The yes-or-no answers, as the form, the API and the archive call them, and the
#: column each is kept in.
SWITCHES: dict[str, str] = {
    "location": "show_location",
    "form_of_address": "show_form_of_address",
    "pronouns": "show_pronouns",
    "birth_date": "show_birth_date",
    "birth_place": "show_birth_place",
    "nationality": "show_nationality",
    "gender": "show_gender",
}


def _profile(owner):
    """This person's own profile, or nothing.

    **A profile that is not the owner's is no profile.** Every row here is reached through
    it -- a number by its holder, an identifier by its profile -- so whatever profile the
    account object happens to carry decides whose rows are offered, followed and printed.
    An import has been seen to leave another account's there (#354: a file whose profile
    names another `id` and `user_id`), after which a choice named by content was looked up
    among that account's rows and pinned. So it is asked whose it is, each time, and one
    that answers with somebody else is treated as none: nothing offered, nothing followed.
    """
    profile = getattr(owner, "profile", None)
    if profile is None or profile.user_id != getattr(owner, "pk", None):
        return None
    return profile


# ------------------------------------------------------------ the owner's own rows


def offered(owner, key: str) -> list:
    """The rows of one kind this person may pin: their own, as *Your details* offers them.

    Everything that accepts a row asks this, and so does everything that prints one. From
    a profile that is not this person's it lists nothing (`_profile` says why).
    """
    profile = _profile(owner)
    if key == "email":
        return confirmed_addresses(owner)
    if profile is None or profile.pk is None:
        return []
    if key == "phone":
        from postulo.core import phone_numbers

        return phone_numbers.numbers_for(profile, owner)
    if key == "identifiers":
        from postulo.core import identifier_order

        # In the order the person arranged the schemes in (#672). Every row is offered,
        # switched off or not: hiding is for drawing summaries, and a CV's choice is its own.
        return identifier_order.arranged(profile.identifiers.all(), profile)
    from postulo.core import web_links

    return web_links.links_for(profile, owner, BY_KEY[key].link_kind)


def confirmed_addresses(owner) -> list:
    """The account's addresses somebody has answered at, the primary first.

    The rule the built-in notifier already keeps (`plugins.email.verified_addresses`): an
    address is this person's once allauth has confirmed it, and until then it is something
    they typed.
    """
    from allauth.account.models import EmailAddress

    if owner is None or not getattr(owner, "pk", None):
        return []
    return list(
        EmailAddress.objects.filter(user=owner, verified=True).order_by("-primary", "email")
    )


def has_unconfirmed_addresses(owner) -> bool:
    """Whether the account holds an address `confirmed_addresses` leaves out, so that the
    page can say why its list is shorter than the one under *Your details*."""
    from allauth.account.models import EmailAddress

    if owner is None or not getattr(owner, "pk", None):
        return False
    return EmailAddress.objects.filter(user=owner, verified=False).exists()


def value_of(row, key: str) -> str:
    """What a document prints for this row."""
    if row is None:
        return ""
    if key == "phone":
        return row.number
    if key == "email":
        return row.email
    return row.url


def name_of(row, key: str) -> str:
    """What a chooser calls this row: the way *Your details* names it.

    A number with its kind, an address on the web with the name it was given, an
    identifier with its scheme. Text, for an `<option>` or a label; the template escapes.
    """
    if key == "phone":
        return f"{row.number} ({row.kind_label})" if row.kind_label else row.number
    if key == "email":
        return row.email
    if key == "identifiers":
        return f"{row.display_label}: {row.value}"
    return f"{row.url} ({row.label})" if row.label else row.url


def followed(owner) -> dict[str, str]:
    """What the default prints of each kind today, by key: the account's address, the
    primary number, the primary link of each kind. Exactly what every CV printed before
    #308, read the way it was read then.

    The primary number whether or not *Several telephone numbers* is on for this person:
    the document has always shown one, and the primary is what "one" means.
    """
    from postulo.core import phone_numbers, web_links

    found = dict.fromkeys(BY_KEY, "")
    found["email"] = getattr(owner, "email", "") or ""
    profile = _profile(owner)
    if profile is None:
        return found
    primary = phone_numbers.primary_for(profile)
    found["phone"] = primary.number if primary else ""
    links = web_links.primaries_for(profile)
    for detail in DETAILS:
        row = links.get(detail.link_kind) if detail.link_kind else None
        if row is not None:
            found[detail.key] = row.url
    return found


# ------------------------------------------------------------------- what is printed


@dataclass
class Printed:
    """What one CV prints of its owner's details, worked out. Empty means not printed."""

    phone: str = ""
    email: str = ""
    social: str = ""
    repository: str = ""
    website: str = ""
    identifiers: list = field(default_factory=list)
    location: str = ""
    form_of_address: str = ""
    pronouns: str = ""
    #: The date as stored (an ISO 8601 reduced form) and the place with its country, each only
    #: where the CV says so (#679); the renderer words the date in the document's language.
    birth_date: str = ""
    birth_place: str = ""
    #: The countries' names, in the order held, or none and then the scope's key (#680).
    nationalities: list = field(default_factory=list)
    nationality_scope: str = ""
    #: The person's own word, only where the CV says so (#681).
    gender: str = ""
    #: The kinds whose pinned row is no longer there, for the page to say so.
    gone: tuple[Detail, ...] = ()


def pinned(cv: CV, detail: Detail):
    """The row this CV pins of one kind, if it is still the owner's to print; else nothing.

    Looked up among `offered` rather than read off the column, which is the check that
    holds whatever wrote the column.
    """
    wanted = getattr(cv, f"{detail.pin_field}_id")
    if wanted is None:
        return None
    for row in offered(cv.owner, detail.key):
        if row.pk == wanted:
            return row
    return None


def is_gone(cv: CV, detail: Detail) -> bool:
    """Whether this CV chose a row of this kind that is no longer there to print."""
    return getattr(cv, detail.choice_field) == Prints.CHOSEN and pinned(cv, detail) is None


def is_kept_back(cv: CV, detail: Detail) -> bool:
    """Whether the row this CV chose is still there and is not offered at present.

    The two ways a chosen row prints nothing are not the same. One that was deleted has
    left the column empty and will not come back. One that is kept back -- a second number
    while *Several telephone numbers* is off, an address nobody has confirmed again -- is
    still pinned, and prints again the day it is offered again: the pin deletes nothing,
    and nothing that merely saves the CV may take it for an answer of *none*.
    """
    return is_gone(cv, detail) and getattr(cv, f"{detail.pin_field}_id") is not None


def gone(cv: CV | None) -> tuple[Detail, ...]:
    if cv is None or cv.pk is None:
        return ()
    return tuple(detail for detail in DETAILS if is_gone(cv, detail))


def resolve(owner, cv: CV | None = None) -> Printed:
    """What is printed: for this CV, or with every answer at its default when there is none.

    No CV is a letter's sender block, and a CV nobody has opened the choice on answers the
    same: the account's address, the primary number, the primary link of each kind, every
    identifier, the location, and neither the form of address nor the pronouns -- what was
    printed before any of this could be chosen.
    """
    from postulo.core import postal

    profile = _profile(owner)
    printed = Printed()
    missing: list[Detail] = []
    defaults = followed(owner)
    for detail in DETAILS:
        answer = getattr(cv, detail.choice_field) if cv is not None else Prints.DEFAULT
        if answer == Prints.DEFAULT:
            value = defaults[detail.key]
        elif answer == Prints.CHOSEN:
            row = pinned(cv, detail)
            if row is None:
                missing.append(detail)
            value = value_of(row, detail.key)
        else:
            value = ""
        setattr(printed, detail.key, value)

    answer = cv.identifiers_choice if cv is not None else Prints.DEFAULT
    if profile is not None and answer == Prints.DEFAULT:
        printed.identifiers = list(profile.identifiers.all())
    elif profile is not None and answer == Prints.CHOSEN:
        # Through the profile's own rows, in their order: the set is narrowed to what is
        # still the owner's, never trusted for it.
        printed.identifiers = list(
            profile.identifiers.filter(pk__in=cv.pinned_identifiers.values("pk"))
        )

    if cv is None or cv.show_location:
        printed.location = postal.printed_location(profile)
    if cv is not None and profile is not None:
        if cv.show_form_of_address:
            printed.form_of_address = (profile.form_of_address or "").strip()
        if cv.show_pronouns:
            printed.pronouns = (profile.pronouns or "").strip()
        if cv.show_birth_date:
            printed.birth_date = (profile.birth_date or "").strip()
        if cv.show_birth_place:
            printed.birth_place = personal.place_text(profile.birth_place, profile.birth_country)
        if cv.show_nationality:
            printed.nationalities = personal.country_names(profile.nationalities)
            if not printed.nationalities and profile.nationality_scope in personal.SCOPES:
                printed.nationality_scope = profile.nationality_scope
        if cv.show_gender:
            printed.gender = (profile.gender or "").strip()
    printed.gone = tuple(missing)
    return printed


def is_default(cv: CV) -> bool:
    """Whether every answer is the one a CV starts with, so nobody has chosen anything."""
    if any(getattr(cv, detail.choice_field) != Prints.DEFAULT for detail in DETAILS):
        return False
    if cv.identifiers_choice != Prints.DEFAULT:
        return False
    return cv.show_location and not any(
        getattr(cv, column) for name, column in SWITCHES.items() if name != "location"
    )


# -------------------------------------------------------------------- choosing


def _answer(raw) -> str:
    if raw not in Prints.values:
        raise NotOffered(
            "choice",
            _("%(field)s must be one of %(choices)s; got %(value)s."),
            choices=sorted(Prints.values),
            value=repr(raw),
        )
    return raw


def choose(cv: CV, key: str, answer: str, row_id: int | None = None, *, owner=None) -> None:
    """Set one kind's answer on ``cv``, without saving it.

    *Chosen* names a row by its id, and the row has to be one `offered` lists for the CV's
    owner: somebody else's is refused in the same words as one that does not exist, so the
    refusal confirms nothing about what another account holds. An id beside any other
    answer is refused too, rather than stored where nothing would read it.

    ``owner`` is for a CV that is being created and has not been given its owner yet: it
    is whoever the form was built for, which is who the CV is about to belong to.
    """
    detail = BY_KEY[key]
    answer = _answer(answer)
    if answer != Prints.CHOSEN:
        if row_id is not None:
            raise NotOffered(
                "id", _("%(field)s goes with %(choice)s and with nothing else."), choice="'chosen'"
            )
        setattr(cv, detail.choice_field, answer)
        setattr(cv, detail.pin_field, None)
        return
    owner = owner if owner is not None else cv.owner
    row = next((row for row in offered(owner, key) if row.pk == row_id), None)
    if row is None:
        raise NotOffered("id", detail.not_yours)
    setattr(cv, detail.choice_field, Prints.CHOSEN)
    setattr(cv, detail.pin_field, row)


def choose_identifiers(cv: CV, answer: str, row_ids=(), *, owner=None) -> list:
    """Set which identifiers ``cv`` prints, and return the rows to pin once it is saved.

    The set is many-to-many, so it cannot be written before the CV has a key: the caller
    saves, then hands what this returned to `cv.pinned_identifiers.set`. Every id has to be
    one of the owner's own identifiers, on the terms `choose` gives.
    """
    answer = _answer(answer)
    wanted = list(dict.fromkeys(row_ids or ()))
    if answer != Prints.CHOSEN:
        if wanted:
            raise NotOffered(
                "ids", _("%(field)s goes with %(choice)s and with nothing else."), choice="'chosen'"
            )
        cv.identifiers_choice = answer
        return []
    owner = owner if owner is not None else cv.owner
    mine = {row.pk: row for row in offered(owner, "identifiers")}
    if any(row_id not in mine for row_id in wanted):
        raise NotOffered("ids", _("%(field)s names an identifier that is not one of yours."))
    cv.identifiers_choice = Prints.CHOSEN
    return [mine[row_id] for row_id in wanted]


#: Every column `choose`, `choose_identifiers` and the three switches write, for a caller
#: that saves with `update_fields`.
COLUMNS: tuple[str, ...] = (
    *(detail.choice_field for detail in DETAILS),
    *(detail.pin_field for detail in DETAILS),
    "identifiers_choice",
    *SWITCHES.values(),
)


# ------------------------------------------------------------ in a file, by content


def reference(row, key: str) -> dict:
    """How the archive names a row: by what it says, never by an id.

    An id is this instance's row number and means nothing in the file -- the profile's
    numbers, links and identifiers are written there without one -- whereas a number as it
    is written, an address, or a scheme and its value can be found again among the rows an
    import has just made.

    An identifier of no known scheme is named by what it is called as well. *Other* is the
    one scheme a person may hold several of, and two of them can share a value -- a staff
    number and a library card that happen to match -- so scheme and value alone would find
    whichever came first.
    """
    if key == "phone":
        return {"number": row.number}
    if key == "email":
        return {"email": row.email}
    if key == "identifiers":
        named = {"scheme": row.scheme, "value": row.value}
        if row.scheme == OTHER:
            named["label"] = row.label
        return named
    return {"url": row.url}


def _matches(row, key: str, named: dict) -> bool:
    """Whether ``row`` is the one an archive names. Only text names a row: a number where
    a string belongs is a file somebody edited, and it matches nothing."""

    def said(name: str) -> str:
        value = named.get(name)
        return value.strip() if isinstance(value, str) else ""

    if key == "phone":
        return bool(said("number")) and row.number == said("number")
    if key == "email":
        return bool(said("email")) and row.email.casefold() == said("email").casefold()
    if key == "identifiers":
        scheme, name = as_restored(said("scheme"), subject=PERSON, value=said("value"))
        if scheme != said("scheme"):
            # A scheme only the other instance defined, or a value the scheme here
            # refuses: the import made the row *Other*, named by the key, and that is the
            # row this names (#311).
            return bool(said("value")) and (row.scheme, row.label, row.value) == (
                scheme,
                name,
                said("value"),
            )
        if not said("value") or row.scheme != said("scheme"):
            return False
        # As the file wrote it, or as the import wrote it down: in the scheme's own
        # spelling here, where that is another one.
        known = find(row.scheme, PERSON)
        if row.value not in {said("value"), known.normalise(said("value")) if known else ""}:
            return False
        # An archive written before the name was carried has none, and matches as it did.
        # Compared as it is stored: the import keeps a name as the file gives it.
        if row.scheme == OTHER and "label" in named:
            return row.label == (named["label"] or "")
        return True
    return bool(said("url")) and row.url == said("url")


def as_archived(cv: CV) -> dict:
    """What this CV prints, as the archive carries it with the CV.

    **What is written is what the CV prints**, not the columns: a pin on a row that has
    gone is written as *none*, which is what it comes to, so an archive restored elsewhere
    prints what the original did rather than something the original never printed.
    """
    block: dict = {}
    for detail in DETAILS:
        answer = getattr(cv, detail.choice_field)
        if answer == Prints.CHOSEN:
            row = pinned(cv, detail)
            block[detail.key] = (
                {"choice": Prints.CHOSEN.value, **reference(row, detail.key)}
                if row is not None
                else {"choice": Prints.NONE.value}
            )
        else:
            block[detail.key] = {"choice": answer}
    block["identifiers"] = {"choice": cv.identifiers_choice}
    if cv.identifiers_choice == Prints.CHOSEN:
        mine = {row.pk for row in offered(cv.owner, "identifiers")}
        block["identifiers"]["rows"] = [
            reference(row, "identifiers") for row in cv.pinned_identifiers.all() if row.pk in mine
        ]
    for name, column in SWITCHES.items():
        block[name] = bool(getattr(cv, column))
    return block


def _answer_archived(said) -> str | None:
    """The answer an archive gives for one kind, if it is one of the three; else nothing."""
    answer = said.get("choice") if isinstance(said, dict) else None
    return answer if isinstance(answer, str) and answer in Prints.values else None


def _identifiers_named(owner, said: dict) -> list | None:
    """The owner's identifiers an archive names as a chosen set, or nothing if the set
    cannot be read whole.

    One that cannot be found takes the whole answer with it, as it does for a kind that
    prints one: half a chosen set is a set nobody chose. And so does a set that is not a
    list of rows -- a number, a word, a list with something in it that is not a row --
    because what was left of it after the unreadable part was dropped would be exactly
    such a half. An empty list is a set: one whose every identifier has since been deleted.
    """
    named = said.get("rows")
    if not isinstance(named, list) or not all(isinstance(one, dict) for one in named):
        return None
    mine = offered(owner, "identifiers")
    found = [
        next((row for row in mine if _matches(row, "identifiers", one)), None) for one in named
    ]
    return None if any(row is None for row in found) else found


def restore(cv: CV, block) -> list[str]:
    """Put an archive's answers on a CV that was just created, and say what could not be.

    An archive written before the block existed has none, and that is every answer at its
    default with nothing to say: it is what the CV printed before there was a choice. So
    is an answer the block leaves out.

    One somebody edited can hold anything, and **an answer that is there and cannot be
    read is treated as one that cannot be resolved**: the kind goes back to its default and
    is named in what this returns, so the import's report says it. That covers a row this
    account does not have, an answer that is not one of the three, one that is not an
    answer at all, a set of identifiers that is not a list of rows, and a yes-or-no that is
    neither. Nothing here raises on what a file holds: an import is one transaction, and a
    stray number in this block would otherwise take the whole account back with it.

    The rows are found by what they say among `offered` -- the owner's own, just imported
    -- so a file cannot name anybody else's. Returns the kinds, and the yes-or-no answers,
    that fell back.
    """
    if block is None:
        return []
    if not isinstance(block, dict):
        return [*BY_KEY, "identifiers", *SWITCHES]
    fell_back: list[str] = []
    for detail in DETAILS:
        if detail.key not in block:
            continue
        said = block[detail.key]
        answer = _answer_archived(said)
        if answer in (Prints.DEFAULT, Prints.NONE):
            choose(cv, detail.key, answer)
            continue
        row = None
        if answer == Prints.CHOSEN:
            row = next(
                (row for row in offered(cv.owner, detail.key) if _matches(row, detail.key, said)),
                None,
            )
        if row is None:
            fell_back.append(detail.key)
            continue
        choose(cv, detail.key, Prints.CHOSEN, row.pk)

    rows: list = []
    if "identifiers" in block:
        said = block["identifiers"]
        answer = _answer_archived(said)
        if answer in (Prints.DEFAULT, Prints.NONE):
            choose_identifiers(cv, answer)
        else:
            found = _identifiers_named(cv.owner, said) if answer == Prints.CHOSEN else None
            if found is None:
                fell_back.append("identifiers")
            else:
                rows = choose_identifiers(cv, Prints.CHOSEN, [row.pk for row in found])
    for name, column in SWITCHES.items():
        if name not in block:
            continue
        if isinstance(block[name], bool):
            setattr(cv, column, block[name])
        else:
            fell_back.append(name)
    cv.save(update_fields=list(COLUMNS))
    cv.pinned_identifiers.set(rows)
    return fell_back
