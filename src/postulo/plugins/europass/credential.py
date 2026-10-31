"""What a European Digital Credential states: the title, who awarded it, when, and its level.

A credential is kept as the file it is (`postulo.documents.credentials`, #670). This reads the
handful of facts a career entry needs out of it, so the page that holds the file can say them
beside it: **the title** of the qualification or achievement, **who awarded it**, **when**, and
its **EQF level** where it states one. It is a document's reader, not an import: nothing is
written, and the summary is read again from the file whenever it is shown, so nothing about a
credential is kept apart from the file.

**Only the European Learning Model v3**, the JSON-LD the Europass wallet and issuer write since
2023. The old XML of v2 is refused with a sentence saying where its replacement is: the
wallets converted every one they held.

**Where each fact is**, as the Commission's own samples write it:

* the title is the claim's ``title`` -- a ``LearningAchievement`` first, then any claim -- and
  the display title only where no claim has one;
* who awarded it is ``awardedBy.awardingBody[*].legalName``, one or more. **Not** the
  credential's ``issuer``, which is the organisation that sealed it and is often somebody
  else;
* when is ``awardedBy.awardingDate``, else the credential's ``validFrom``, else when it was
  issued;
* the EQF level is ``specifiedBy.eqfLevel``, a concept whose address ends in the number. It
  is stated for a qualification and for nothing else, and is never guessed.

**JSON-LD read as plain JSON.** No ``@context`` is followed (two addresses for it occur, and
neither is fetched), no JSON-LD processor runs, and compaction's habit of writing a list of
one as a value is undone by reading every value as a list. A language map ``{tag: text}``
gives the reader's language, else English, else the first. **Nothing personal is read**: the
subject's names, identifiers, date of birth and addresses are in the file and stay there.
Bounded as the CV readers are: the file's size, its depth, and the number of claims and
bodies looked at. An embedded picture is a string, and is never decoded.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from postulo.documents import credentials
from postulo.plugins.api import ImportRefused

#: How many claims, and how many awarding bodies of each, are looked at. A credential has
#: one claim, or a handful for a transcript; past this it is not one being read for a CV.
MAX_CLAIMS = 50
MAX_BODIES = 10

#: The address of an EQF level, ending in its number.
_EQF = re.compile(r"^https?://data\.europa\.eu/snb/eqf/([1-8])$")

#: A date as a credential writes one: a day, then perhaps a time.
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class CredentialRefused(ImportRefused):
    """The file is not a credential this reads, and the message says why in plain words."""


#: What an old XML credential is told.
OLD_FORMAT = _(
    "That is a European Digital Credential in the old XML format, which Postulo does not "
    "read. Download it again from your Europass wallet, where it has been converted, and "
    "upload that file."
)


@dataclass
class Summary:
    """What a credential says, and nothing about whom it says it of."""

    title: str = ""
    awarded_by: list[str] = field(default_factory=list)
    awarded_on: dt.date | None = None
    #: 1 to 8, or ``None`` where the credential states no level.
    eqf_level: int | None = None


def _many(value) -> list:
    """A value as a list: JSON-LD compaction writes a list of one as the value itself."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _objects(value, limit: int) -> list[dict]:
    return [item for item in _many(value)[:limit] if isinstance(item, dict)]


def _words(value, language: str) -> str:
    """Text out of a language map, in the reader's language, else English, else the first.

    A plain string is kept as written. Whitespace is collapsed, so a value cannot lay out a
    page; everything shown is escaped by the template that shows it.
    """
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):  # a list of maps, or of strings, as compaction leaves them
        for item in value:
            if found := _words(item, language):
                return found
        return ""
    if not isinstance(value, dict) or not value:
        return ""
    texts = {str(tag).lower(): text for tag, text in value.items() if isinstance(text, str)}
    wanted = (language or "").lower()
    for tag in (wanted, wanted.split("-")[0], "en"):
        if tag and texts.get(tag, "").strip():
            return " ".join(texts[tag].split())
    for tag, text in texts.items():
        if tag.split("-")[0] == wanted.split("-")[0] and text.strip():
            return " ".join(text.split())
    return next((" ".join(text.split()) for text in texts.values() if text.strip()), "")


def _day(value) -> dt.date | None:
    """The day a timestamp names, as written: the issuer's day, not the reader's zone's."""
    if not isinstance(value, str):
        return None
    found = _DAY.match(value.strip())
    if not found:
        return None
    try:
        return dt.date(*(int(part) for part in found.groups()))
    except ValueError:
        return None


def _eqf(specification) -> int | None:
    for level in _many(specification.get("eqfLevel") if specification else None):
        address = level.get("id") if isinstance(level, dict) else level
        if isinstance(address, str) and (found := _EQF.match(address.strip())):
            return int(found.group(1))
    return None


def _claims(credential: dict) -> list[dict]:
    """The claims, the achievement first: that is the qualification a CV names."""
    claims: list[dict] = []
    for subject in _objects(credential.get("credentialSubject"), MAX_CLAIMS):
        claims.extend(_objects(subject.get("hasClaim"), MAX_CLAIMS))
    claims = claims[:MAX_CLAIMS]
    return sorted(
        claims,
        key=lambda claim: "LearningAchievement" not in credentials.types_of(claim.get("type")),
    )


def summarise(credential: dict, language: str = "") -> Summary:
    """What a parsed credential says, read only from the places listed above.

    Of one claim: the first with a title, the achievement before any other. A transcript
    holds many, and the one a CV names is the qualification, not its parts.
    """
    claims = _claims(credential)
    claim = next((one for one in claims if _words(one.get("title"), language)), None)
    claim = claim or (claims[0] if claims else {})

    summary = Summary(title=_words(claim.get("title"), language))
    if not summary.title:
        display = credential.get("displayParameter")
        summary.title = _words(display.get("title"), language) if isinstance(display, dict) else ""
    for process in _objects(claim.get("awardedBy"), MAX_BODIES):
        for body in _objects(process.get("awardingBody"), MAX_BODIES):
            name = _words(body.get("legalName"), language)
            if name and name not in summary.awarded_by:
                summary.awarded_by.append(name)
        summary.awarded_on = summary.awarded_on or _day(process.get("awardingDate"))
    specification = claim.get("specifiedBy")
    summary.eqf_level = _eqf(specification) if isinstance(specification, dict) else None
    summary.awarded_on = (
        summary.awarded_on
        or _day(credential.get("validFrom"))
        or _day(credential.get("issued"))
        or _day(credential.get("issuanceDate"))
    )
    return summary


def read(data: bytes, language: str = "") -> Summary:
    """Read a credential file. Raises :class:`CredentialRefused` with the reason."""
    if credentials.is_old_credential(data):
        raise CredentialRefused(OLD_FORMAT)
    credential = credentials.credential_in(data)
    if credential is None or "EuropeanDigitalCredential" not in credentials.types_of(
        credential.get("type")
    ):
        raise CredentialRefused(
            _(
                "That is not a European Digital Credential Postulo can read. It reads the "
                "JSON-LD the Europass wallet gives you, sealed or not."
            )
        )
    return summarise(credential, language)
