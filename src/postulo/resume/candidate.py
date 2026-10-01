"""One person's own record, read back in from the file Postulo wrote (#181).

The counterpart to `core.export.build_candidate_document`, which writes the file: somebody's
details and their career, and nothing about where they applied. This reads one, says what
is in it, and adds what is new.

**A review, not an action.** `read` turns bytes into something a session can hold, `plan`
says what adding it would do to this account as it stands, and `apply` does that and no
more. Nothing is written by the first two. The page between them is the capture review's
shape for the capture review's reason: reading somebody else's file is guesswork, and a
guess should cost a moment's attention rather than put a job title on somebody's CV.

**It merges, and it never replaces.** Importing into an account that already has fifteen
roles is the case that matters. An entry the account already holds is left exactly as it
is and reported as already there; an entry it does not hold is added. Nothing is changed
and nothing is removed, so importing the same file twice adds nothing the second time.
The one thing of somebody's own that may move is an entry's place in its list, as it does
when an entry is typed by hand: a new role lands by its date, and what was under it is
one lower.

**What makes two entries the same one is what they say.** The ids in a file are local to
it -- they exist so that a skill can name its group and a translation its entry -- and are
never looked up in the database, so a file cannot name somebody else's record, or one of
the person's own. A role is its organisation, its title and its dates; `KINDS` below says
what each of the others is. Case, spacing and the way a letter is composed are folded
before comparing, because they vary in what people type and mean nothing here.

**It tells; it does not refuse** (the stance of #178). A file that is not one of these is
refused, with a sentence naming what is read here. A file that is one, with an entry in it
that cannot be added -- a date that is not a date, a title longer than a title may be, a
skill whose group is missing -- loses that entry and keeps the rest, and the page says
which and why.

**The file is a stranger's.** Its size is bounded before anything is parsed and its rows
are bounded before anything is validated. Every value goes through the form somebody
typing it would have used, so the import accepts what the pages accept and nothing else:
an address has to be one, a length is a length, a choice is one of the choices. What the
file says about who owns a row, whether a number was confirmed or which of them gets
somebody back into their account is not read at all.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from postulo.accounts.forms import PersonIdentifierForm
from postulo.accounts.models import PersonIdentifier, Profile
from postulo.core import (
    addresses,
    export,
    language_field,
    languages,
    phone_numbers,
    phones,
    postal,
    throttle,
)
from postulo.core import web_links as links
from postulo.core.models import PhoneNumber, PostalAddress, WebLink

from . import forms as resume_forms
from . import models as resume
from . import ordering, translating

#: The most a candidate file may be. One person's career with every entry translated three
#: ways is some tens of kilobytes, and there is no picture in it to make it more; a megabyte
#: is room to spare, and the bound is there so that what is handed to the parser is a file
#: and not an appetite.
MAX_BYTES = 1024 * 1024

#: How many rows of one block are read. Nobody has held two hundred posts, and a file that
#: says they have is asking for that many forms to be validated and drawn -- every time
#: the page is, on a machine that may be a Raspberry Pi.
MAX_ROWS = 200
#: Every entry, in every language, field by field.
MAX_TRANSLATIONS = 2000
#: Numbers, addresses, links and identifiers. Low on purpose: whether a telephone number
#: can be added says whether somebody on this instance already has it, and a file is a way
#: of asking that about a great many numbers at once (#142).
MAX_CONTACT_ROWS = 20
#: How many rows go to the database in one statement.
BATCH = 200

#: A date as the file writes one, which is how `date.isoformat()` does. Matched rather than
#: handed to a form, whose date field also reads whatever the reader's language writes --
#: and the same file has to read the same way whoever opens it.
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


#: An `order` is a small whole number. Anything else in that place says nothing.
MAX_ORDER = 2**31 - 1

# --------------------------------------------------------------------- what may happen

ADD = "add"
PRESENT = "present"
KEPT = "kept"
REFUSED = "refused"
REPEATED = "repeated"

#: What the page calls each of them, in the order it counts them.
OUTCOMES = {
    ADD: gettext_lazy("Will be added"),
    PRESENT: gettext_lazy("Already in your record"),
    KEPT: gettext_lazy("Yours is kept"),
    REPEATED: gettext_lazy("In the file twice"),
    REFUSED: gettext_lazy("Cannot be added"),
}


class Refused(Exception):
    """The file will not be read, and the message says why, to the person who chose it."""


class _Ambiguous:
    """Stands where an id names two entries of one block, so that it names neither."""


AMBIGUOUS = _Ambiguous()


# ------------------------------------------------------------------ the kinds of entry


@dataclass(frozen=True)
class Kind:
    """One block of the career: where its entries come from, and what identifies one."""

    #: What the block is called in the file, which is what `core.export` calls it.
    block: str
    model: type
    form: type
    title: Any
    #: What is read of an entry, which is what its form takes.
    fields: tuple[str, ...]
    #: **What makes two entries the same one**: these, folded, and `dates` beside them.
    names: tuple[str, ...]
    dates: tuple[str, ...] = ()
    #: Which of `names` is an address, and is compared as one: with or without its scheme,
    #: its "www." and its trailing slash, it is the same page.
    address: str = ""
    #: What the page prints for an entry: its name, and the line under it.
    label: str = "name"
    sub: str = ""
    #: Whether an entry with a start and no end is still going.
    ongoing: bool = False

    @property
    def held(self) -> tuple[str, ...]:
        """What is kept of an entry between reading the file and confirming it."""
        extra = ("group_id",) if self.block == "skills" else ()
        return ("id", "order", *self.fields, *extra)


#: In the order the review shows them, which is the order the career page does -- and with
#: the groups before the skills that name them.
KINDS: tuple[Kind, ...] = (
    Kind(
        block="experience",
        model=resume.Experience,
        form=resume_forms.ExperienceForm,
        title=gettext_lazy("Experience"),
        fields=(
            "role",
            "organisation",
            "location",
            "start_date",
            "end_date",
            "summary",
            "highlights",
        ),
        names=("organisation", "role"),
        dates=("start_date", "end_date"),
        label="role",
        sub="organisation",
        ongoing=True,
    ),
    Kind(
        block="education",
        model=resume.Education,
        form=resume_forms.EducationForm,
        title=gettext_lazy("Education"),
        fields=(
            "qualification",
            "institution",
            "field_of_study",
            "location",
            "start_date",
            "end_date",
            "grade",
            "highlights",
        ),
        names=("institution", "qualification"),
        dates=("start_date", "end_date"),
        label="qualification",
        sub="institution",
    ),
    Kind(
        block="projects",
        model=resume.Project,
        form=resume_forms.ProjectForm,
        title=gettext_lazy("Projects"),
        fields=("name", "role", "url", "start_date", "end_date", "summary", "highlights"),
        names=("name",),
        dates=("start_date", "end_date"),
        sub="role",
    ),
    Kind(
        block="links",
        model=resume.Link,
        form=resume_forms.LinkForm,
        title=gettext_lazy("Links"),
        fields=("title", "url", "kind", "description"),
        names=("url",),
        address="url",
        label="title",
        sub="url",
    ),
    Kind(
        block="skill_groups",
        model=resume.SkillGroup,
        form=resume_forms.SkillGroupForm,
        # The model's own name for them. The career page heads its groups *Skills*, which
        # here is the block after this one.
        title=capfirst(resume.SkillGroup._meta.verbose_name_plural),
        fields=("name",),
        names=("name",),
    ),
    Kind(
        block="skills",
        model=resume.Skill,
        form=resume_forms.SkillForm,
        title=gettext_lazy("Skills"),
        fields=("name",),
        # And the group it sits under, which `_Planner._entry` adds: *Python* under
        # *Languages* and *Python* under *Tools* are two skills.
        names=("name",),
    ),
    Kind(
        block="certifications",
        model=resume.Certification,
        form=resume_forms.CertificationForm,
        title=gettext_lazy("Certifications"),
        fields=("name", "issuer", "issued_on", "expires_on", "credential_url"),
        names=("name", "issuer"),
        dates=("issued_on", "expires_on"),
        sub="issuer",
    ),
    Kind(
        block="languages",
        model=resume.LanguageSkill,
        form=resume_forms.LanguageSkillForm,
        title=gettext_lazy("Languages"),
        fields=("name", "proficiency"),
        names=("name",),
    ),
)

KINDS_BY_BLOCK = {kind.block: kind for kind in KINDS}

#: What the file writes of an entry and this does not read, and the whole of it. What
#: another instance found when it asked whether a link still answered is not something
#: this one knows: a link arrives unchecked, and *Check* is this instance's to press.
NOT_READ: dict[str, tuple[str, ...]] = {
    "links": ("check_status", "check_detail", "checked_at"),
    # Which ESCO skill a skill's name is follows the name, in the classification this
    # instance holds; another instance's answer is not asked, and `_place` works it out
    # again from the name (#266).
    "skills": ("esco_uri",),
}

#: The rows that hang off the profile, and what is read of each. Taken from what the export
#: writes, less the two things a file may not say: that a number was confirmed, and that it
#: is the way back into an account. An archive is a claim, and a claim of verification made
#: by another instance is one this instance never checked (#142, #144).
NOT_THE_FILES_TO_SAY = ("verified_at", "is_recovery")
CONTACT_BLOCKS: dict[str, tuple[str, ...]] = {
    "phone_numbers": tuple(
        name for name in export.PHONE_NUMBER_FIELDS if name not in NOT_THE_FILES_TO_SAY
    ),
    "postal_addresses": export.POSTAL_ADDRESS_FIELDS,
    "web_links": export.WEB_LINK_FIELDS,
}
IDENTIFIER_FIELDS = ("scheme", "value", "label")
TRANSLATION_FIELDS = ("section", "ref", "language", "field", "text")
#: The two that live on the account and the ones that live on the profile.
NAME_FIELDS = ("first_name", "last_name")
DETAIL_FIELDS = (*NAME_FIELDS, *export.CANDIDATE_PROFILE_FIELDS)

#: Every block a held file has, which is what `is_empty` looks through.
BLOCKS = (*CONTACT_BLOCKS, "identifiers", *KINDS_BY_BLOCK, "translations")


# ----------------------------------------------------------------------- reading a file


def _not_one_of_these() -> str:
    return _(
        "That is not a file this page reads. It reads the JSON file it offers to download "
        "— one person's own record, as Postulo writes it — and nothing else."
    )


def _no_constants(name: str):
    """`NaN` and `Infinity` are not JSON, and nothing a career says is either of them."""
    raise ValueError(name)


def _short(value, limit: int = 40) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _kept(value):
    """A value as it will be held: text, a whole number, a yes or a no, or nothing.

    Anything else -- a list where a title should be, an object, a fraction -- is held as an
    empty list, which says *something was here and it was not text* without holding
    whatever it was. What a session keeps is then as flat as a career is.
    """
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value
    return []


def _prune(entry, names: tuple[str, ...]):
    """One entry, down to what is read of it. ``None`` for a thing that is not an entry."""
    if not isinstance(entry, dict):
        return None
    return {name: _kept(entry[name]) for name in names if name in entry}


def read(data: bytes) -> dict:
    """What is in the file, as something a session can hold. Raises `Refused` with why.

    Nothing here looks at the database, and nothing here is believed: this is the shape of
    the file and no more. What is returned is flat -- a list of entries per block, each
    entry its known fields and nothing else -- with what was wrong with the shape written
    down as facts rather than as sentences, so that the page says them in the language it
    is being read in when it is drawn.
    """
    if not data:
        raise Refused(_("That file is empty."))
    if len(data) > MAX_BYTES:
        raise Refused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )
    try:
        document = json.loads(data.decode("utf-8-sig"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise Refused(_not_one_of_these()) from error

    header = document.get("postulo") if isinstance(document, dict) else None
    if not isinstance(header, dict):
        raise Refused(_not_one_of_these())
    version = header.get("candidate_format")
    if version is None and "format" in header:
        raise Refused(
            _(
                "That is the export of a whole account. This page reads only the file it "
                "offers to download itself: one person's own record, without the "
                "applications."
            )
        )
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise Refused(_not_one_of_these())

    held: dict[str, Any] = {
        "format": min(version, MAX_ORDER),
        "version": _short(header.get("version"), 20),
        "exported_at": _short(header.get("exported_at")),
        # What else the file holds, by name, so the page can say it was not read.
        "also": sorted(
            str(name)[:40] for name in document if name not in ("postulo", "account", "resume")
        )[:12],
        "unreadable": [],
        "cut": {},
    }

    def part(value, name: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            held["unreadable"].append(name)
            return {}
        return value

    def block(value, name: str, names: tuple[str, ...], limit: int) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            held["unreadable"].append(name)
            return []
        if len(value) > limit:
            held["cut"][name] = len(value)
        return [_prune(entry, names) for entry in value[:limit]]

    account = part(document.get("account"), "account")
    profile = part(account.get("profile"), "profile")
    career = part(document.get("resume"), "resume")

    held["details"] = {
        **{name: _kept(account[name]) for name in NAME_FIELDS if name in account},
        **{
            name: _kept(profile[name])
            for name in export.CANDIDATE_PROFILE_FIELDS
            if name in profile
        },
    }
    for name, names in CONTACT_BLOCKS.items():
        held[name] = block(profile.get(name), name, names, MAX_CONTACT_ROWS)
    held["identifiers"] = block(
        account.get("identifiers"), "identifiers", IDENTIFIER_FIELDS, MAX_CONTACT_ROWS
    )
    for kind in KINDS:
        held[kind.block] = block(career.get(kind.block), kind.block, kind.held, MAX_ROWS)
    held["translations"] = block(
        career.get("translations"), "translations", TRANSLATION_FIELDS, MAX_TRANSLATIONS
    )
    return held


def is_empty(held: dict) -> bool:
    """Whether there is nothing in it at all to say something about.

    A part that is there and cannot be read is something to say: a file whose career is a
    sentence where a list should be is told so on the review, not told it was empty.
    """
    said = any(value not in (None, "") for value in (held.get("details") or {}).values())
    odd = held.get("unreadable") or held.get("also")
    return not said and not odd and not any(held.get(name) for name in BLOCKS)


# ------------------------------------------------------------------- what would happen


@dataclass
class Row:
    """One thing the file says, and what adding the file would do about it."""

    label: str
    outcome: str
    #: The line under the name: an employer, an address, the kind of number.
    sub: str = ""
    start: dt.date | None = None
    end: dt.date | None = None
    #: Whether a start with no end means it is still going.
    ongoing: bool = False
    #: Why, in words, where the outcome does not say the whole of it.
    notes: list[str] = field(default_factory=list)

    # What follows is for `apply`, and the page reads none of it.
    #: What would be saved: validated, unsaved, and owned by whoever is importing.
    instance: Any = None
    #: What the account already holds that this is.
    existing: Any = None
    #: The group a skill sits under.
    parent: Row | None = None
    #: A detail to fill in: the field, and what to put in it.
    fill: tuple[str, str] | None = None
    #: What one row of translations is made of: the field, and the text.
    texts: list[tuple[str, str]] = field(default_factory=list)
    language: str = ""
    wants_primary: bool = False
    order: int | None = None
    position: int = 0

    @property
    def outcome_label(self):
        return OUTCOMES[self.outcome]

    @property
    def target(self):
        """The record this row is, once the import has run: the one it matched or made."""
        return self.existing if self.existing is not None else self.instance


@dataclass
class Section:
    """One heading on the review page, and the rows under it."""

    key: str
    title: Any
    rows: list[Row] = field(default_factory=list)
    #: Which of the career's kinds this is, where it is one.
    kind: Kind | None = None
    #: Which kind of web link, where it is those.
    link_kind: str = ""


@dataclass
class Plan:
    """What the file holds and what adding it would do, for the page and for `apply`."""

    sections: list[Section] = field(default_factory=list)
    #: What there is to say about the file as a whole.
    notes: list[str] = field(default_factory=list)
    version: str = ""
    exported_at: dt.datetime | None = None
    #: What the telephone numbers were told once this account had asked about as many as
    #: it may in an hour, or nothing while it has not.
    silenced: str = ""

    def rows(self) -> Iterator[Row]:
        for section in self.sections:
            yield from section.rows

    @property
    def adds(self) -> int:
        return sum(1 for row in self.rows() if row.outcome == ADD)

    @property
    def summary(self) -> list[tuple[Any, int]]:
        """How many rows met each outcome, leaving out the outcomes nothing met."""
        found = Counter(row.outcome for row in self.rows())
        return [(label, found[key]) for key, label in OUTCOMES.items() if found[key]]


@dataclass
class Report:
    """What an import actually added."""

    total: int = 0
    #: Why telephone numbers the page said would be added were not, where that happened:
    #: the allowance for asking about numbers ran out between the page and the button.
    held_back: str = ""


def fold(value) -> str:
    """Text as it is compared: one case, one spacing, one way of composing a letter."""
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _same_address(value) -> str:
    text = str(value or "").strip()
    return addresses.same_url(text) or fold(text)


def _said(value) -> str:
    """Text as it is compared for *does yours say the same*: the ends and the line endings
    are a browser's, and everything between them is the person's."""
    return str(value or "").replace("\r\n", "\n").strip()


def _said_of(translation) -> tuple:
    """What one translation is a translation of: the entry, the language and the field."""
    language = languages.tag(translation.language)
    return (translation.content_type_id, translation.object_id, language, translation.field)


def _whole_number(value, *, most: int = MAX_ORDER) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= most else None


def _problem(label, message) -> str:
    if not label:
        return str(message)
    return _("%(field)s: %(problem)s") % {"field": capfirst(str(label)), "problem": message}


def _not_text() -> str:
    return _("This is not text.")


def _not_a_date() -> str:
    return _("Write the date as year, month and day, like 2021-03-31.")


def _not_a_record() -> Row:
    return Row(
        label=_("Something that is not an entry"),
        outcome=REFUSED,
        notes=[_("It is not written the way an entry is, so it could not be read.")],
    )


class _Planner:
    """Reads a held file against one account as it stands. Writes nothing."""

    def __init__(self, user, held: dict) -> None:
        self.user = user
        self.held = held
        self.profile = Profile.objects.filter(user=user).first()
        #: For every block, which row each id in the file names.
        self.named: dict[str, dict[int, Any]] = {}
        #: Once this account has been told about as many numbers as it may be in an hour,
        #: what every further number is told instead.
        self.silenced = ""

    # ----------------------------------------------------------------- the whole of it

    def draw(self) -> Plan:
        drawn = Plan(
            version=self.held.get("version") or "",
            exported_at=self._when(self.held.get("exported_at")),
            notes=self._notes(),
        )
        sections = [
            self._details(),
            self._numbers(),
            self._addresses(),
            *self._web_links(),
            self._identifiers(),
            *(self._entries(kind) for kind in KINDS),
            self._translations(),
        ]
        drawn.sections = [section for section in sections if section.rows]
        drawn.silenced = self.silenced
        return drawn

    @staticmethod
    def _when(value) -> dt.datetime | None:
        try:
            return parse_datetime(value) if value else None
        except ValueError:
            return None

    def _notes(self) -> list[str]:
        notes = []
        if (self.held.get("format") or 0) > export.CANDIDATE_FORMAT:
            notes.append(
                _(
                    "This file was written by a newer Postulo than this one. What this one "
                    "does not know how to read is left out."
                )
            )
        also = self.held.get("also") or []
        if also:
            notes.append(
                _("The file also holds %(names)s, which this page does not read.")
                % {"names": ", ".join(also)}
            )
        for name in self.held.get("unreadable") or []:
            notes.append(
                _("“%(name)s” is in the file, and is not written the way this page reads it.")
                % {"name": name}
            )
        for name, total in (self.held.get("cut") or {}).items():
            limit = self._limit(name)
            notes.append(
                _(
                    "“%(name)s” has %(total)s rows in the file. The first %(limit)s are "
                    "read, and the rest are not."
                )
                % {"name": name, "total": total, "limit": limit}
            )
        return notes

    @staticmethod
    def _limit(name: str) -> int:
        if name == "translations":
            return MAX_TRANSLATIONS
        return MAX_ROWS if name in KINDS_BY_BLOCK else MAX_CONTACT_ROWS

    # -------------------------------------------------------------- reading one entry

    @staticmethod
    def _posted(entry: dict, names: tuple[str, ...], dates: tuple[str, ...] = ()):
        """The entry as a form would have been posted it, and what could not be."""
        data: dict[str, str] = {}
        wrong: list[tuple[str, str]] = []
        for name in names:
            value = entry.get(name)
            if value is None:
                data[name] = ""
            elif not isinstance(value, str):
                wrong.append((name, _not_text()))
            elif name in dates and value.strip():
                text = value.strip()
                try:
                    if not ISO_DATE.match(text):
                        raise ValueError(text)
                    dt.date.fromisoformat(text)
                except ValueError:
                    wrong.append((name, _not_a_date()))
                else:
                    data[name] = text
            else:
                data[name] = value
        return data, wrong

    @staticmethod
    def _problems(form, wrong: list[tuple[str, str]]) -> list[str]:
        """Everything the form and the reading above had to say, each naming its field."""
        found = []
        for name, message in wrong:
            bound = form.fields.get(name)
            found.append(_problem(bound.label if bound is not None else name, message))
        said = {name for name, _message in wrong}
        for name, errors in form.errors.items():
            if name in said:
                continue
            bound = form.fields.get(name)
            for message in errors:
                found.append(_problem(bound.label if bound is not None else "", message))
        return found

    @staticmethod
    def _only(form, names: tuple[str, ...]):
        """The form, asking only for what is read from the file.

        Every entry's form offers its order number to somebody who asked for it under
        *Settings > Accessibility*, and a skill's offers its group. Neither is read as a
        form field here: where an entry lands and which group a skill sits under are
        settled from what the file says, against what the account holds.
        """
        for name in list(form.fields):
            if name not in names:
                del form.fields[name]
        return form

    def _index(self, block: str) -> None:
        """Which entry each id in one block of the file names, where it names one."""
        entries = [entry for entry in self.held.get(block) or [] if isinstance(entry, dict)]
        met = Counter(
            entry["id"] for entry in entries if _whole_number(entry.get("id")) is not None
        )
        self.named[block] = {name: AMBIGUOUS for name, times in met.items() if times > 1}

    def _name(self, block: str, entry: dict, row: Row) -> None:
        name = _whole_number(entry.get("id"))
        if name is not None:
            self.named[block].setdefault(name, row)

    # ------------------------------------------------------------------- your details

    def _details(self) -> Section:
        section = Section("details", gettext_lazy("Your details"))
        said = self.held.get("details") or {}
        for name in DETAIL_FIELDS:
            raw = said.get(name)
            if raw in (None, ""):
                continue
            model = get_user_model() if name in NAME_FIELDS else Profile
            column = model._meta.get_field(name)
            label = capfirst(str(column.verbose_name))
            if not isinstance(raw, str):
                section.rows.append(Row(label, REFUSED, notes=[_not_text()]))
                continue
            try:
                value = column.formfield(required=False).clean(raw)
                if name == "record_language":
                    value = self._language(value)
            except ValidationError as error:
                section.rows.append(Row(label, REFUSED, sub=raw[:80], notes=error.messages))
                continue
            if not value:
                continue
            shown = languages.native_name(value, value) if name.endswith("language") else value
            holder = self.user if name in NAME_FIELDS else self.profile
            mine = str(getattr(holder, name, "") or "")
            if not mine.strip():
                section.rows.append(Row(label, ADD, sub=shown, fill=(name, value)))
            elif fold(mine) == fold(value):
                section.rows.append(Row(label, PRESENT, sub=shown))
            else:
                section.rows.append(Row(label, KEPT, sub=shown))
        return section

    @staticmethod
    def _language(value: str) -> str:
        """A language code as Postulo writes one, or a `ValidationError` saying it is not one.

        In any case and with either separator, as a file from elsewhere may write it: what
        it is held to is the shape of a tag, the rule the Europass reader holds a locale to.
        """
        if not languages.well_formed(value):
            raise language_field.refusal(value)
        return languages.tag(value)

    # ---------------------------------------------------- numbers, addresses and links

    def _holder(self):
        """The profile to hang a row off while it is being validated.

        An account without a profile has nothing to match against, and gets one when the
        import is confirmed; until then an unsaved one stands in.
        """
        return self.profile if self.profile is not None else Profile(user=self.user)

    def _mine(self, relation: str) -> list:
        return list(getattr(self.profile, relation).all()) if self.profile is not None else []

    def _numbers(self) -> Section:
        section = Section("phone_numbers", gettext_lazy("Telephone numbers"))
        mine = {self._number_key(row.number) for row in self._mine("phone_numbers")}
        seen: set[str] = set()
        for entry in self.held.get("phone_numbers") or []:
            if not isinstance(entry, dict):
                section.rows.append(_not_a_record())
                continue
            data, wrong = self._posted(entry, ("kind", "label", "number"))
            instance = PhoneNumber(owner=self.user)
            form = _NumberForm(data=data, instance=instance)
            if wrong or not form.is_valid():
                label = data.get("number", "").strip()[:60] or _("A number with nothing in it")
                section.rows.append(Row(label, REFUSED, notes=self._problems(form, wrong)))
                continue
            # What the field on the page would have stored, with no country chosen beside
            # it: the international form where the number has one, and as typed otherwise.
            instance.number = phones.combine(form.cleaned_data["number"], "")
            row = Row(
                label=phones.readable(instance.number),
                outcome=ADD,
                sub=instance.kind_label,
                instance=instance,
                wants_primary=entry.get("is_primary") is True,
            )
            key = self._number_key(instance.number)
            if key in mine:
                row.outcome = PRESENT
            elif key in seen:
                row.outcome = REPEATED
            else:
                self._somebody_elses(row)
            seen.add(key)
            section.rows.append(row)
        return section

    @staticmethod
    def _number_key(number: str) -> str:
        return phones.normalise(number) or fold(number)

    def _somebody_elses(self, row: Row) -> None:
        """Refuse a number this instance already holds, within what may be said about it.

        Telephone numbers are unique across the instance, so whether one can be added
        says whether somebody here has it. That disclosure was decided in #90 and bounded
        in #142, and a file is a way of asking it about twenty numbers at a time -- so
        each answer is charged to the account exactly as the form charges it. Once the
        allowance is spent, **no** number is answered for: one that could have been added
        is refused in the same words as one that could not, because telling them apart is
        the answer.
        """
        if self.silenced:
            row.outcome = REFUSED
            row.notes.append(self.silenced)
            return
        if not phone_numbers.taken_elsewhere(row.instance.number):
            return
        row.outcome = REFUSED
        try:
            phone_numbers.collision_noticed(self.user)
        except throttle.TooOften as too_often:
            self.silenced = str(
                phone_numbers.ASKED_TOO_OFTEN
                % {"minutes": max(1, round(too_often.retry_after / 60))}
            )
            row.notes.append(self.silenced)
        else:
            row.notes.append(str(phone_numbers.ALREADY_IN_USE))

    def _addresses(self) -> Section:
        section = Section("postal_addresses", gettext_lazy("Postal addresses"))
        # Every address the account holds, not only the profile's: one address is listed
        # once per account, whoever it is listed for (#92).
        mine = {
            row.comparable: row
            for row in PostalAddress.objects.filter(owner=self.user).exclude(comparable="")
        }
        here = (
            ContentType.objects.get_for_model(Profile).pk,
            self.profile.pk if self.profile is not None else None,
        )
        seen: set[str] = set()
        names = tuple(name for name in export.POSTAL_ADDRESS_FIELDS if name != "is_primary")
        for entry in self.held.get("postal_addresses") or []:
            if not isinstance(entry, dict):
                section.rows.append(_not_a_record())
                continue
            data, wrong = self._posted(entry, names)
            # As the row is stored, which is how the export wrote it; the form offers the
            # codes in capitals.
            data["country"] = data.get("country", "").strip().upper()
            instance = PostalAddress(owner=self.user)
            form = postal.PostalAddressForm(data=data, instance=instance)
            if wrong or not form.is_valid():
                label = " ".join(data.get("street", "").split())[:60]
                label = label or _("An address that could not be read")
                section.rows.append(Row(label, REFUSED, notes=self._problems(form, wrong)))
                continue
            key = instance.comparable_form()
            if not key:
                continue
            named = instance.kind == PostalAddress.Kind.OTHER
            row = Row(
                label=instance.one_line(),
                outcome=ADD,
                sub=instance.label if named else str(instance.get_kind_display()),
                instance=instance,
                wants_primary=entry.get("is_primary") is True,
                # What looks unusual for its country, which is a note and never a refusal:
                # an address that fits no rule is still where somebody lives (#147).
                notes=[str(note) for note in postal.warnings_for(instance, person=self.user)],
            )
            held = mine.get(key)
            if held is not None and (held.content_type_id, held.object_id) == here:
                row.outcome, row.notes = PRESENT, []
            elif held is not None:
                row.outcome = REFUSED
                row.notes = [
                    _(
                        "You already list this address for somebody you deal with, and "
                        "an address is listed once."
                    )
                ]
            elif key in seen:
                row.outcome, row.notes = REPEATED, []
            seen.add(key)
            section.rows.append(row)
        return section

    def _web_links(self) -> list[Section]:
        sections = {
            block.kind: Section("web_links", block.legend, link_kind=block.kind)
            for block in links.BLOCKS
        }
        # An address is listed once under each kind, so the same one may be a repository
        # and a website both (#457): what is already here is asked per kind.
        mine = {(row.kind, _same_address(row.url)) for row in self._mine("web_links")}
        seen: set[tuple[str, str]] = set()
        # Where a row goes that does not say which of the three it is.
        unsorted = Section("web_links", capfirst(str(WebLink._meta.verbose_name_plural)))
        for entry in self.held.get("web_links") or []:
            if not isinstance(entry, dict):
                unsorted.rows.append(_not_a_record())
                continue
            data, wrong = self._posted(entry, ("kind", "label", "url"))
            kind = data.pop("kind", "")
            # The service the file names is a claim (#305): believed where this instance
            # offers it and the address is one of its addresses, *Other* where it says
            # anything else, and where it says nothing the address says. So the form is
            # handed a choice it can only accept, and no link is refused over its service.
            data["service"] = links.chosen_in_a_file(entry, kind, data.get("url", ""))
            shown = data.get("url", "").strip()[:80] or _("A link with no address")
            if kind not in links.KINDS:
                note = _problem(
                    WebLink._meta.get_field("kind").verbose_name,
                    _("“%(kind)s” is not one of the kinds of link.") % {"kind": kind[:40]},
                )
                unsorted.rows.append(Row(shown, REFUSED, notes=[note]))
                continue
            instance = WebLink(owner=self.user, kind=kind)
            form = links.WebLinkForm(data=data, instance=instance)
            if wrong or not form.is_valid():
                sections[kind].rows.append(Row(shown, REFUSED, notes=self._problems(form, wrong)))
                continue
            row = Row(
                label=instance.display,
                outcome=ADD,
                sub=instance.url,
                instance=instance,
                wants_primary=entry.get("is_primary") is True,
            )
            key = (kind, _same_address(instance.url))
            if key in mine:
                row.outcome = PRESENT
            elif key in seen:
                row.outcome = REPEATED
            seen.add(key)
            sections[kind].rows.append(row)
        return [*sections.values(), unsorted]

    def _identifiers(self) -> Section:
        from postulo.accounts import identifiers as schemes

        section = Section("identifiers", gettext_lazy("Identifiers"))
        mine = {(row.scheme, row.value) for row in self._mine("identifiers")}
        # One of each kind per person, which the database holds to: the kinds the account
        # has, and the kinds an earlier row of the file is about to give it.
        taken = {scheme for scheme, _value in mine if scheme != schemes.OTHER}
        claimed: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for entry in self.held.get("identifiers") or []:
            if not isinstance(entry, dict):
                section.rows.append(_not_a_record())
                continue
            data, wrong = self._posted(entry, IDENTIFIER_FIELDS)
            if not any(value.strip() for value in data.values()) and not wrong:
                continue
            instance = PersonIdentifier(profile=self._holder())
            form = PersonIdentifierForm(data=data, instance=instance)
            if wrong or not form.is_valid():
                label = schemes.label_for(data.get("scheme", "")[:20]) or _("An identifier")
                section.rows.append(
                    Row(
                        label,
                        REFUSED,
                        sub=data.get("value", "").strip()[:80],
                        notes=self._problems(form, wrong),
                    )
                )
                continue
            row = Row(instance.display_label, ADD, sub=instance.value, instance=instance)
            key = (instance.scheme, instance.value)
            if key in mine:
                row.outcome = PRESENT
            elif key in seen:
                row.outcome = REPEATED
            elif instance.scheme in taken:
                row.outcome = KEPT
                row.notes.append(_("You already have another of this kind."))
            elif instance.scheme in claimed:
                row.outcome = REFUSED
                row.notes.append(
                    _("The file has another of this kind, and a person has one of each.")
                )
            elif instance.scheme != schemes.OTHER:
                claimed.add(instance.scheme)
            seen.add(key)
            section.rows.append(row)
        return section

    # ---------------------------------------------------------------------- the career

    def _key(self, kind: Kind, get: Callable[[str], Any], group: str = "") -> tuple:
        names = tuple(
            _same_address(get(name)) if name == kind.address else fold(get(name))
            for name in kind.names
        )
        dates = tuple(get(name) or None for name in kind.dates)
        return (group, *names, *dates) if kind.block == "skills" else (*names, *dates)

    def _alike(self, kind: Kind, get: Callable[[str], Any]) -> tuple:
        """The key without its dates: the same post, held at another time."""
        return tuple(fold(get(name)) for name in kind.names)

    def _entries(self, kind: Kind) -> Section:
        section = Section(kind.block, kind.title, kind=kind)
        self._index(kind.block)
        standing = kind.model.objects.for_user(self.user)
        if kind.block == "skills":
            standing = standing.select_related("group")
        mine: dict[tuple, Any] = {}
        alike: set[tuple] = set()
        for item in standing:
            group = fold(item.group.name) if getattr(item, "group_id", None) else ""
            says = partial(getattr, item)
            mine.setdefault(self._key(kind, says, group), item)
            alike.add(self._alike(kind, says))
        seen: dict[tuple, Row] = {}
        for position, entry in enumerate(self.held.get(kind.block) or []):
            if not isinstance(entry, dict):
                section.rows.append(_not_a_record())
                continue
            row = self._entry(kind, entry, mine, alike, seen)
            row.position = position
            row.order = _whole_number(entry.get("order"))
            section.rows.append(row)
        return section

    def _entry(self, kind: Kind, entry: dict, mine: dict, alike: set, seen: dict) -> Row:
        data, wrong = self._posted(entry, kind.fields, kind.dates)
        instance = kind.model(owner=self.user)
        form = self._only(kind.form(data=data, user=self.user, instance=instance), kind.fields)
        valid = form.is_valid() and not wrong
        said = form.cleaned_data if valid else data

        row = Row(
            label=str(said.get(kind.label) or "").strip()[:200] or _("An entry with no name"),
            outcome=ADD,
            sub=str(said.get(kind.sub) or "").strip()[:200] if kind.sub else "",
            ongoing=kind.ongoing,
            instance=instance,
        )
        if kind.block == "languages" and valid:
            row.sub = str(instance.get_proficiency_display()) if instance.proficiency else ""
        group = ""
        if kind.block == "skills":
            group = self._group_of(entry, row)
            row.sub = self._group_name(row)
        if not valid:
            row.outcome = REFUSED
            row.notes = [*self._problems(form, wrong), *row.notes]
        if row.outcome == REFUSED:
            # Named all the same, so that a skill under it, or a translation of it, is told
            # that its entry cannot be added rather than that it is not there.
            self._name(kind.block, entry, row)
            return row

        if kind.dates:
            row.start, row.end = (said.get(name) for name in kind.dates)
        key = self._key(kind, said.get, group)
        if key in seen:
            # The second of two that say the same thing. It names the first, so that a
            # translation of either is a translation of the one entry there will be.
            first = seen[key]
            row.outcome, row.existing, row.instance = REPEATED, first.existing, first.instance
            self._name(kind.block, entry, first)
            return row
        seen[key] = row
        self._name(kind.block, entry, row)
        held = mine.get(key)
        if held is not None:
            row.outcome, row.existing = PRESENT, held
            if any(
                _said(said.get(name)) != _said(getattr(held, name))
                for name in kind.fields
                if name not in kind.names and name not in kind.dates
            ):
                row.notes.append(_("The file words it differently. Yours is left as it is."))
        elif kind.dates and self._alike(kind, said.get) in alike:
            row.notes.append(
                _("You have one like it with other dates. Both will be in your record.")
            )
        return row

    def _group_of(self, entry: dict, row: Row) -> str:
        """Which group a skill sits under, folded, having settled whether it can."""
        named = entry.get("group_id")
        if named is None:
            return ""
        group = self.named.get("skill_groups", {}).get(_whole_number(named))
        if group is None:
            row.outcome = REFUSED
            row.notes.append(_("The group it belongs to is not in the file."))
            return ""
        if group is AMBIGUOUS:
            row.outcome = REFUSED
            row.notes.append(_("The file has two groups under the number it names."))
            return ""
        if group.outcome == REFUSED:
            row.outcome = REFUSED
            row.notes.append(_("The group it belongs to cannot be added."))
            return ""
        row.parent = group
        return fold(group.target.name)

    @staticmethod
    def _group_name(row: Row) -> str:
        return str(row.parent.target.name) if row.parent is not None else ""

    # ---------------------------------------------------------------- the translations

    def _stored(self) -> dict[tuple, str]:
        """Everything this account's entries already say in other languages."""
        return {
            _said_of(each): each.text for each in resume.Translation.objects.for_user(self.user)
        }

    def _translations(self) -> Section:
        section = Section("translations", capfirst(resume.Translation._meta.verbose_name_plural))
        blocks = set(export.TRANSLATION_SECTIONS.values())
        stored: dict[tuple, str] | None = None
        gathered: dict[tuple, Row] = {}
        seen: set[tuple] = set()
        for entry in self.held.get("translations") or []:
            if not isinstance(entry, dict):
                section.rows.append(_not_a_record())
                continue
            data, wrong = self._posted(entry, ("section", "language", "field", "text"))
            text = data.get("text", "").strip()
            if not text and not wrong:
                continue
            block = data.get("section", "")
            kind = KINDS_BY_BLOCK.get(block) if block in blocks else None
            label = _("A translation")
            if wrong or kind is None:
                note = (
                    _problem(wrong[0][0], wrong[0][1])
                    if wrong
                    else _("“%(name)s” is not a part of the career that Postulo translates.")
                    % {"name": block[:40]}
                )
                section.rows.append(Row(label, REFUSED, sub=text[:80], notes=[note]))
                continue
            entry_row = self.named.get(block, {}).get(_whole_number(entry.get("ref")))
            if entry_row is None or entry_row is AMBIGUOUS or entry_row.outcome == REFUSED:
                note = (
                    _("The entry it belongs to cannot be added.")
                    if isinstance(entry_row, Row)
                    else _("The entry it belongs to is not in the file.")
                )
                section.rows.append(Row(label, REFUSED, sub=text[:80], notes=[note]))
                continue
            try:
                language = self._language(data.get("language", ""))
                text = forms.CharField().clean(text)
                if data.get("field") not in translating.fields_for(kind.model):
                    raise ValidationError(
                        _("“%(name)s” is not a part of an entry that Postulo translates.")
                        % {"name": data.get("field", "")[:40]}
                    )
            except ValidationError as error:
                section.rows.append(
                    Row(entry_row.label, REFUSED, sub=text[:80], notes=error.messages)
                )
                continue

            field_name = data["field"]
            outcome = ADD
            if (id(entry_row.target), language, field_name) in seen:
                outcome = REPEATED
            elif entry_row.existing is not None:
                if stored is None:
                    stored = self._stored()
                held = stored.get((*translating.key_of(entry_row.existing), language, field_name))
                # A translation somebody cleared says nothing, and a blank is not an
                # opinion: it is filled. One that says something is theirs and stays.
                if held and held.strip():
                    outcome = PRESENT if _said(held) == _said(text) else KEPT
            seen.add((id(entry_row.target), language, field_name))

            group = (id(entry_row.target), language, outcome)
            row = gathered.get(group)
            if row is None:
                row = gathered[group] = Row(
                    label=entry_row.label,
                    outcome=outcome,
                    parent=entry_row,
                    language=language,
                )
                section.rows.append(row)
            row.texts.append((field_name, text))
            row.sub = _("%(language)s: %(fields)s") % {
                "language": languages.native_name(language, language),
                "fields": ", ".join(
                    translating.field_label(kind.model, name) for name, _text in row.texts
                ),
            }
        return section


class _NumberForm(phone_numbers.PhoneNumberForm):
    """A telephone row as the file writes one: the number whole, and no country beside it.

    The page's own form asks for a country and the rest of the number, because that is how
    somebody types one. A file holds what was stored. Everything else about the row -- the
    kinds it may be, the name an *Other* has to carry -- is the page's rule, inherited.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        column = PhoneNumber._meta.get_field("number")
        self.fields["number"] = forms.CharField(
            label=column.verbose_name, max_length=column.max_length
        )


def plan(user, held: dict) -> Plan:
    """What adding this file to this account would do, as the account stands. Writes nothing."""
    return _Planner(user, held).draw()


# ------------------------------------------------------------------------------ adding it


@transaction.atomic
def apply(user, held: dict) -> Report:
    """Add what is new, and only that. Nothing the account holds is changed or removed.

    The plan is drawn again rather than carried over from the page that showed it: what
    is held between the two is the file, and the account may have changed since -- by a
    second tab, or by this same button pressed twice. So a second confirmation finds
    everything already there and adds nothing.

    In one transaction, so that a file which fails half way leaves the account as it was.
    """
    drawn = plan(user, held)
    profile, _made = Profile.objects.get_or_create(user=user)
    report = Report(held_back=drawn.silenced)
    for section in drawn.sections:
        rows = [row for row in section.rows if row.outcome == ADD]
        if not rows:
            continue
        if section.key == "details":
            _fill(user, profile, rows)
        elif section.key == "phone_numbers":
            _hang(user, profile, rows, profile.phone_numbers.all(), phone_numbers.set_primary)
        elif section.key == "postal_addresses":
            _hang(user, profile, rows, profile.postal_addresses.all(), postal.make_primary)
        elif section.key == "web_links":
            mine = profile.web_links.filter(kind=section.link_kind)
            _hang(user, profile, rows, mine, links.set_primary)
        elif section.key == "identifiers":
            for row in rows:
                row.instance.profile = profile
                row.instance.save()
        elif section.key == "translations":
            _translate(user, rows)
        else:
            _place(user, section.kind, rows)
        report.total += len(rows)
    return report


def _fill(user, profile, rows: list[Row]) -> None:
    """Fill in what was blank. A blank is not an opinion, so nothing is being overruled."""
    names: list[str] = []
    details: list[str] = []
    for row in rows:
        name, value = row.fill
        if name in NAME_FIELDS:
            setattr(user, name, value)
            names.append(name)
        else:
            setattr(profile, name, value)
            details.append(name)
    if names:
        user.save(update_fields=names)
    if details:
        profile.save(update_fields=[*details, "updated_at"])


def _hang(user, profile, rows: list[Row], mine, make_primary) -> None:
    """Add rows to the profile, leaving it with exactly one primary among ``mine``.

    The primary the account has stays the primary: which number a CV prints is not
    something a file gets to change. Where there is none, the first row the file calls
    primary takes it, and failing that the first row there is -- a holder with numbers and
    no primary shows none at all once *Several telephone numbers* is switched off.
    """
    settled = mine.filter(is_primary=True).exists()
    for row in rows:
        row.instance.owner = user
        row.instance.holder = profile
        row.instance.is_primary = row.wants_primary and not settled
        row.instance.save()
        settled = settled or row.instance.is_primary
    if not settled:
        make_primary(mine.order_by("created_at", "pk").first())


def _place(user, kind: Kind, rows: list[Row]) -> None:
    """Add entries to one section of the career, each where it belongs.

    Into a section with nothing in it, in the order the file gives them, which is the
    order their owner put them in somewhere else. Into a section that has entries, as an
    entry typed by hand would land (#203): by its date where the section has one, last
    where it has not, and the person's to move from there.
    """
    standing = list(kind.model.objects.for_user(user).order_by("order", "pk"))
    dated = ordering.DATE_FIELDS.get(kind.model.__name__) if standing else None
    sequence = list(standing)
    for row in sorted(rows, key=lambda row: (row.order is None, row.order or 0, row.position)):
        item = row.instance
        item.owner = user
        if kind.block == "skills":
            item.group = row.parent.target if row.parent is not None else None
            # `bulk_create` below calls no `save`, which is where a skill works out which
            # ESCO skill its name is; so it is asked here, from the name, as a save would.
            item.match()
        place = len(sequence)
        if dated is not None:
            mine = ordering.newest_first_key(item, dated)
            for index, other in enumerate(sequence):
                if ordering.newest_first_key(other, dated) < mine:
                    place = index
                    break
        sequence.insert(place, item)

    # Dense numbers in the order just settled, as `ordering.renumber` leaves them, written
    # in two statements rather than one for each entry: what is new, and the places of
    # what was there and has moved. A file may hold a few hundred entries of a kind, and
    # a request that asked the database about each would hold it for as long as somebody
    # else's file cared to make it.
    now = timezone.now()
    added, moved = [], []
    for place, item in enumerate(sequence):
        if item.pk is None:
            item.order = place
            added.append(item)
        elif item.order != place:
            item.order, item.updated_at = place, now
            moved.append(item)
    kind.model.objects.bulk_create(added, batch_size=BATCH)
    kind.model.objects.bulk_update(moved, ["order", "updated_at"], batch_size=BATCH)


def _translate(user, rows: list[Row]) -> None:
    """Write what the entries say in other languages, where they said nothing.

    A translation somebody cleared is a row that is still there, saying nothing, and it is
    that row which is filled. Everything else is a row of its own.
    """
    cleared = {
        _said_of(each): each
        for each in resume.Translation.objects.for_user(user).exclude(text__regex=r"\S")
    }
    now = timezone.now()
    added, filled = [], []
    for row in rows:
        entry = row.parent.target
        content_type = ContentType.objects.get_for_model(type(entry))
        for name, text in row.texts:
            blank = cleared.get((content_type.pk, entry.pk, row.language, name))
            if blank is not None:
                blank.text, blank.updated_at = text, now
                filled.append(blank)
                continue
            added.append(
                resume.Translation(
                    owner=user,
                    content_type=content_type,
                    object_id=entry.pk,
                    language=row.language,
                    field=name,
                    text=text,
                )
            )
    resume.Translation.objects.bulk_create(added, batch_size=BATCH)
    resume.Translation.objects.bulk_update(filled, ["text", "updated_at"], batch_size=BATCH)
