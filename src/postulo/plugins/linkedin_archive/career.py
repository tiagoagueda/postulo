"""The career files of a LinkedIn archive, as a career record (#1260).

Only the career's files are read: the profile, positions, education, skills, languages,
certifications, projects, honours, courses, publications, volunteering and organisations,
and the person's own first telephone number and email address. The job files (saved jobs,
applications, saved answers) wait for an importer to be allowed to propose listings and
applications, which is another change; until then they are named with everything else that
was not read.

**Other people's data is never opened.** ``Connections.csv`` holds other people's names,
employers and sometimes their addresses, and it is named on the review without being opened
(decision D3 of #1260), as are messages, invitations and the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from . import sheets

# The header rows below are **not taken from a real archive**: LinkedIn publishes no column
# specification, and they are put together from LinkedIn's own help page (which lists the
# categories) and the header rows found in the fixtures and parsers of open-source projects
# that read the archive (Reactive Resume, applyspace, linkedin-csv.rs), each read for the
# names only (#1260, decision D9). A column LinkedIn adds or renames is named on the review
# as not read; a file whose header holds none of the required names is named as not
# recognised. Neither stops the rest of the archive being read.


@dataclass(frozen=True)
class Kind:
    """One career file: the columns it is read by, and the ones it must have."""

    #: Its base name as LinkedIn writes it, for the review.
    name: str
    #: Every column this reader knows, in lower case: read, or known to have no place.
    columns: frozenset[str]
    #: The columns without which the file is not recognised.
    required: frozenset[str]


def _kind(name: str, columns: str, required: str) -> Kind:
    def split(text: str) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in text.split(",") if part.strip())

    return Kind(name, split(columns), split(required))


KINDS: dict[str, Kind] = {
    kind.name.lower(): kind
    for kind in (
        _kind(
            "Profile.csv",
            "First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry,"
            "Zip Code,Geo Location,Twitter Handles,Websites,Instant Messengers",
            "First Name,Last Name",
        ),
        _kind(
            "Positions.csv",
            "Company Name,Title,Description,Location,Started On,Finished On",
            "Company Name,Title",
        ),
        _kind(
            "Education.csv",
            "School Name,Start Date,End Date,Notes,Degree Name,Activities",
            "School Name",
        ),
        _kind("Skills.csv", "Name", "Name"),
        _kind("Languages.csv", "Name,Proficiency", "Name"),
        _kind(
            "Certifications.csv",
            "Name,Url,Authority,Started On,Finished On,License Number",
            "Name",
        ),
        _kind("Projects.csv", "Title,Description,Url,Started On,Finished On", "Title"),
        _kind("Honors.csv", "Title,Description,Issued On,Issuer", "Title"),
        _kind("Courses.csv", "Name,Number", "Name"),
        _kind("Publications.csv", "Name,Published On,Description,Publisher,Url", "Name"),
        _kind(
            "Volunteering.csv",
            "Company Name,Role,Cause,Started On,Finished On,Description",
            "Company Name",
        ),
        _kind("Organizations.csv", "Name,Position,Started On,Finished On,Description", "Name"),
        _kind("PhoneNumbers.csv", "Extension,Number,Type", "Number"),
        _kind("Email Addresses.csv", "Email Address,Confirmed,Primary,Updated On", "Email Address"),
    )
}

#: The files a loose CSV, uploaded on its own, can be recognised as: the ones whose header
#: says what they are. A one-column `Name` could be any of three.
LOOSE = ("profile.csv", "positions.csv", "education.csv", "certifications.csv")

#: The five phrases LinkedIn grades a language by. Only the last is a level Postulo keeps;
#: the other four are not CEFR levels and are not turned into one (decision D6).
NATIVE = "native or bilingual proficiency"

#: Columns that are read for nothing because they say nothing about the career, and are
#: therefore not named on the review either: bookkeeping of LinkedIn's own.
QUIET = frozenset({"confirmed", "primary", "updated on", "type", "extension"})

#: The first web address in the profile's list of websites, ``[COMPANY:https://…,…]``.
_ADDRESS = re.compile(r"https?://[^\s,\]]+")


class Reading:
    """One archive being read into one record."""

    def __init__(self, record):
        self.record = record
        #: Columns that held something and have no place, by file: ``{"Profile.csv": [...]}``.
        self.unmapped: dict[str, list[str]] = {}
        self.bad_dates: list[str] = []

    def keep_unread(self, kind: Kind, row: dict, read: tuple[str, ...]) -> None:
        for column, value in row.items():
            if value and column not in read and column not in QUIET:
                said = self.unmapped.setdefault(kind.name, [])
                title = _title(column)
                if title not in said:
                    said.append(title)

    def date(self, row: dict, column: str, where: str):
        day, read = sheets.date_of(row.get(column, ""))
        if not read:
            self.bad_dates.append(f"{where} “{row[column][:30]}”")
        return day

    def reduced(self, row: dict, column: str, where: str) -> str:
        text, read = sheets.reduced_of(row.get(column, ""))
        if not read:
            self.bad_dates.append(f"{where} “{row[column][:30]}”")
        return text


def _title(column: str) -> str:
    """A column's name as LinkedIn writes it, for the review."""
    return " ".join(word.capitalize() for word in column.split())


def _get(row: dict, column: str) -> str:
    return row.get(column, "")


def read_sheet(reading: Reading, key: str, sheet: sheets.Sheet) -> None:
    """Put one sheet's rows into the record."""
    kind = KINDS[key]
    record = reading.record
    skills: list[str] = []
    for place, row in enumerate(sheet.rows, start=1):
        where = f"{kind.name} {place}"
        if key == "profile.csv":
            if place > 1:
                break
            person = record.person
            for column, field in (
                ("first name", "first_name"),
                ("last name", "last_name"),
                ("headline", "headline"),
                ("geo location", "location"),
            ):
                if _get(row, column):
                    person[field] = _get(row, column)
            website = _ADDRESS.search(_get(row, "websites"))
            if website:
                person["website"] = website.group(0)
            read = ("first name", "last name", "headline", "geo location", "websites")
        elif key == "positions.csv":
            record.experience.append(
                {
                    "role": _get(row, "title"),
                    "organisation": _get(row, "company name"),
                    "location": _get(row, "location"),
                    "start_date": reading.date(row, "started on", where),
                    # An empty end means the position is current.
                    "end_date": reading.date(row, "finished on", where),
                    "summary": _get(row, "description"),
                }
            )
            read = ("title", "company name", "location", "started on", "finished on", "description")
        elif key == "education.csv":
            record.education.append(
                {
                    "qualification": _get(row, "degree name"),
                    "institution": _get(row, "school name"),
                    "location": "",
                    "start_date": reading.date(row, "start date", where),
                    "end_date": reading.date(row, "end date", where),
                    "grade": "",
                    "highlights": "\n".join(
                        filter(None, (_get(row, "notes"), _get(row, "activities")))
                    ),
                }
            )
            read = ("degree name", "school name", "start date", "end date", "notes", "activities")
        elif key == "skills.csv":
            if _get(row, "name"):
                skills.append(_get(row, "name"))
            read = ("name",)
        elif key == "languages.csv":
            phrase = _get(row, "proficiency")
            native = phrase.lower() == NATIVE
            record.languages.append(
                {
                    "name": _get(row, "name"),
                    "proficiency": "native" if native else "",
                    "stated": "" if native else phrase,
                }
            )
            read = ("name", "proficiency")
        elif key == "certifications.csv":
            record.certifications.append(
                {
                    "name": _get(row, "name"),
                    "issuer": _get(row, "authority"),
                    "issued_on": reading.date(row, "started on", where),
                    "expires_on": reading.date(row, "finished on", where),
                    "credential_url": _get(row, "url"),
                }
            )
            read = ("name", "authority", "started on", "finished on", "url")
        elif key == "projects.csv":
            record.projects.append(
                {
                    "name": _get(row, "title"),
                    "summary": _get(row, "description"),
                    "url": _get(row, "url"),
                    "start_date": reading.date(row, "started on", where),
                    "end_date": reading.date(row, "finished on", where),
                }
            )
            read = ("title", "description", "url", "started on", "finished on")
        elif key == "honors.csv":
            record.honours.append(
                {
                    "title": _get(row, "title"),
                    "awarded_by": _get(row, "issuer"),
                    "awarded_on": reading.date(row, "issued on", where),
                    "summary": _get(row, "description"),
                }
            )
            read = ("title", "issuer", "issued on", "description")
        elif key == "courses.csv":
            record.courses.append({"title": _get(row, "name")})
            read = ("name",)
        elif key == "publications.csv":
            record.publications.append(
                {
                    "title": _get(row, "name"),
                    "publisher": _get(row, "publisher"),
                    "date": reading.reduced(row, "published on", where),
                    "url": _get(row, "url"),
                }
            )
            read = ("name", "publisher", "published on", "url")
        elif key in ("volunteering.csv", "organizations.csv"):
            volunteering = key == "volunteering.csv"
            record.memberships.append(
                {
                    "organisation": _get(row, "company name" if volunteering else "name"),
                    "role": _get(row, "role" if volunteering else "position"),
                    "start_date": reading.date(row, "started on", where),
                    "end_date": reading.date(row, "finished on", where),
                    "summary": _get(row, "description"),
                }
            )
            read = (
                "company name" if volunteering else "name",
                "role" if volunteering else "position",
                "started on",
                "finished on",
                "description",
            )
        elif key == "phonenumbers.csv":
            if _get(row, "number") and "phone" not in record.person:
                record.person["phone"] = _get(row, "number")
            read = ("number",)
        elif key == "email addresses.csv":
            address = _get(row, "email address")
            primary = _get(row, "primary").lower() in ("yes", "true")
            if address and ("email" not in record.person or primary):
                # Shown on the review, never written: an address is the account's to verify.
                record.person["email"] = address
            read = ("email address",)
        else:  # pragma: no cover - every key of KINDS is above
            read = ()
        reading.keep_unread(kind, row, read)
    if skills:
        # One group, named *Skills* in the reader's language: an existing heading of that
        # name is reused when the record is written.
        record.skill_groups.append({"name": str(_("Skills")), "skills": skills})
    # A row with nothing in the column that names it says nothing, and is left out.
    record.experience[:] = [row for row in record.experience if row["role"] or row["organisation"]]
    record.education[:] = [
        row for row in record.education if row["institution"] or row["qualification"]
    ]
    for name, column in (
        ("languages", "name"),
        ("certifications", "name"),
        ("projects", "name"),
        ("honours", "title"),
        ("courses", "title"),
        ("publications", "title"),
        ("memberships", "organisation"),
    ):
        rows = getattr(record, name)
        rows[:] = [row for row in rows if row[column]]
