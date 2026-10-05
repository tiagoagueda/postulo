"""vCard 4.0 (RFC 6350), written and read by hand, once (#660).

**One mapping, no I/O and no model in its signatures**: plain values in, text out; text in,
plain values out. Postulo's own export and import of contacts hand it rows turned into
these values (`jobs.vcards`), and the DAV plugin hands it what it read through the plugin
surface, so the two cannot disagree about what a telephone number or a department looks
like on a card.

**Hand-written, and the library check is recorded.** vobject reads and writes vCard 2.1 and
3.0 and has no 4.0 (its #124 is open); `vcard` validates 3.0 only; `vcfpy` is the genomics
VCF. The project writes vCard and iCalendar by hand today, and the weakness of that is the
copies: iCalendar's content-line injection was fixed in #218 and the DAV plugin's own
`escape` never received it. So this is the one copy. Revisit when a maintained library ships
vCard 4.0.

**Writing**, always 4.0. A control character is removed from every value, a lone carriage
return is a line break like any other, and every line is folded at 75 octets, never inside
a character, so nothing a person typed can end a line and start a property of its own.
`N` is **not invented** from `FN`: a name field has no given and family part, and Portuguese
and many other names do not split at the last space. The person's own card, which has a
first and a last name, writes it.

**Reading**, 3.0 and 4.0; 2.1 and anything else is refused with a sentence. A file is
hostile until read: it is bounded in size, in cards, in properties per card and in the
length of a line, and nothing it names is ever fetched -- a `PHOTO`, a `LOGO`, a `SOURCE`
or a `KEY` address is a name in the list of what is not kept, and nothing else.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field, replace
from urllib.parse import quote

from django.utils.translation import gettext_lazy as _

from postulo import __version__

from . import phones

CONTENT_TYPE = "text/vcard; charset=utf-8"
PRODID = f"-//Postulo//Postulo {__version__}//EN"

#: RFC 6350 §3.2 folds at 75 octets; a continuation starts with one space.
LINE_OCTETS = 75

#: What one file may be, in bytes, and how many cards of it are read.
MAX_BYTES = 2 * 1024 * 1024
MAX_CARDS = 500
#: One unfolded line, in characters. A card's `PHOTO` is the one property that is routinely
#: longer, and it is not kept; a line over this is skipped and named as not kept.
MAX_LINE = 8192
#: What one card may hold before the rest of it is not read.
MAX_PROPERTIES = 200
#: How many telephone numbers, addresses, links and handles of one card are read.
MAX_PER_KIND = 25
#: One text value: a name, a title. A note is allowed more.
MAX_TEXT = 1000
MAX_NOTE = 20000

KIND_INDIVIDUAL = "individual"
KIND_ORG = "org"

TEL_TYPES = {
    "mobile": "cell",
    "work": "work",
    "home": "home",
    "switchboard": "work,voice",
    "fax": "fax",
}
ADR_TYPES = {"home": "home", "work": "work", "postal": "postal"}

#: What a reader keeps, and so never names as left out.
_KEPT = frozenset(
    "BEGIN END VERSION PRODID UID REV FN N KIND ORG TITLE ROLE EMAIL TEL ADR URL "
    "SOCIALPROFILE IMPP NOTE PRONOUNS".split()
)


class VCardRefused(ValueError):
    """A file that is not read at all, with the sentence to say so."""


@dataclass(frozen=True)
class Phone:
    """A number as the contact holds it, and its kind: mobile, work, home, switchboard,
    fax, or nothing."""

    number: str
    kind: str = ""
    primary: bool = False


@dataclass(frozen=True)
class Address:
    """A postal address: its parts, a two-letter country, and its kind (home, work, postal)."""

    street: str = ""
    postcode: str = ""
    municipality: str = ""
    region: str = ""
    country: str = ""
    kind: str = ""
    primary: bool = False


@dataclass(frozen=True)
class Link:
    """An address on the web. ``social`` is how a card said it, with ``SOCIALPROFILE``."""

    url: str
    social: bool = False


@dataclass(frozen=True)
class Card:
    """One card as plain values. Empty means not written, and not read."""

    name: str = ""
    kind: str = KIND_INDIVIDUAL
    #: Only the person's own card has these; a contact's name is one field.
    given: str = ""
    family: str = ""
    company: str = ""
    department: str = ""
    role: str = ""
    email: str = ""
    phones: tuple[Phone, ...] = ()
    addresses: tuple[Address, ...] = ()
    links: tuple[Link, ...] = ()
    #: Instant messaging addresses as URIs (`impp_for`).
    impps: tuple[str, ...] = ()
    notes: str = ""
    pronouns: str = ""
    uid: str = ""
    revised: dt.datetime | None = None
    #: What reading a card left out, by property name. Never written.
    dropped: tuple[str, ...] = ()
    #: The version a card was read as. Never written.
    version: str = ""

    @property
    def is_org(self) -> bool:
        return self.kind == KIND_ORG

    def to_dict(self) -> dict:
        """The card as JSON-able values, for a session to hold what was read of a file."""
        return {
            "name": self.name,
            "kind": self.kind,
            "company": self.company,
            "department": self.department,
            "role": self.role,
            "email": self.email,
            "phones": [[p.number, p.kind, p.primary] for p in self.phones],
            "addresses": [
                [a.street, a.postcode, a.municipality, a.region, a.country, a.kind, a.primary]
                for a in self.addresses
            ],
            "links": [[link.url, link.social] for link in self.links],
            "impps": list(self.impps),
            "notes": self.notes,
            "pronouns": self.pronouns,
            "uid": self.uid,
            "dropped": list(self.dropped),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Card:
        """The card `to_dict` made, or `ValueError` for anything that is not one."""
        try:
            return cls(
                name=str(data["name"]),
                kind=KIND_ORG if data["kind"] == KIND_ORG else KIND_INDIVIDUAL,
                company=str(data["company"]),
                department=str(data["department"]),
                role=str(data["role"]),
                email=str(data["email"]),
                phones=tuple(Phone(str(n), str(k), bool(p)) for n, k, p in data["phones"]),
                addresses=tuple(
                    Address(*(str(part) for part in row[:6]), bool(row[6]))
                    for row in data["addresses"]
                ),
                links=tuple(Link(str(u), bool(s)) for u, s in data["links"]),
                impps=tuple(str(u) for u in data["impps"]),
                notes=str(data["notes"]),
                pronouns=str(data["pronouns"]),
                uid=str(data["uid"]),
                dropped=tuple(str(d) for d in data["dropped"]),
                version=str(data["version"]),
            )
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise ValueError("not a card") from error


@dataclass
class Read:
    """What a file held: its cards, and how many more there were than were read."""

    cards: list[Card] = field(default_factory=list)
    skipped: int = 0


# ------------------------------------------------------------------------- the text

_BREAKS = str.maketrans({"\r": "\n", "\x85": "\n", " ": "\n", " ": "\n"})
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str, *, lines: bool = False) -> str:
    """``text`` with nothing in it that could end a line or start a property.

    Every kind of line break is first the same one, so a lone carriage return and a pair
    cannot differ; then, for a value that is one line, it is a space, and for one that has
    lines (a note, a street) it stays. Every other control character goes (#218, #27).
    """
    text = str(text or "").replace("\r\n", "\n").translate(_BREAKS)
    text = text.replace("\n", "\n" if lines else " ").replace("\t", " ")
    return _CONTROLS.sub("", text).strip()


def escape(text: str, *, lines: bool = False) -> str:
    """Text as a property value (RFC 6350 §3.4): backslash, comma, semicolon, newline."""
    return (
        clean(text, lines=lines)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def unescape(text: str) -> str:
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            out.append("\n" if following in "nN" else following)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def fold(line: str) -> list[str]:
    """Split one content line so no piece exceeds 75 octets, never inside a character."""
    pieces: list[str] = []
    current, used = "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if used + width > LINE_OCTETS:
            pieces.append(current)
            current, used = " ", 1
        current += char
        used += width
    pieces.append(current)
    return pieces


def _line(name: str, value: str, *params: str) -> list[str]:
    head = ";".join((name, *params))
    return fold(f"{head}:{value}")


def uid_for(instance: str, kind: str, pk: int) -> str:
    """A stable ``urn:uuid:`` for one record of one instance, the same every time.

    Derived, because a contact has no stored identifier and a card written twice has to
    name its contact the same way twice. ``instance`` is whatever tells two Postulos
    apart; the plugin that pairs a card with its contact keeps the UID it first stored and
    never calls this again (postulo-dav#7).
    """
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{instance}/{kind}/{pk}')}"


def impp_for(service: str, handle: str) -> str:
    """An instant messaging address as a URI, for the services that have one: XMPP and
    Matrix. Nothing for Signal, Telegram, Threema or anything else, which have none a
    reader would know."""
    handle = clean(handle)
    if not handle:
        return ""
    if service == "xmpp":
        return "xmpp:" + quote(handle, safe="@")
    if service == "matrix":
        return "matrix:u/" + quote(handle.removeprefix("@"), safe=":")
    return ""


def impp_read(uri: str) -> tuple[str, str] | None:
    """The service and the handle of an ``xmpp:`` or ``matrix:`` URI, or nothing."""
    from urllib.parse import unquote

    uri = clean(uri)
    lowered = uri.lower()
    if lowered.startswith("xmpp:"):
        rest = uri[5:].lstrip("/").split("?", 1)[0]
        handle = unquote(rest)
        return ("xmpp", handle) if handle else None
    if lowered.startswith("matrix:u/"):
        rest = uri[9:].split("?", 1)[0]
        handle = unquote(rest)
        return ("matrix", "@" + handle) if handle else None
    return None


# ------------------------------------------------------------------------- writing


def _tel_value(number: str) -> tuple[str, list[str]]:
    """The international form as a ``tel:`` URI, or the number as typed as text."""
    international = phones.normalise(number)
    if international:
        return f"tel:{international}", ["VALUE=uri"]
    return escape(number), ["VALUE=text"]


def card_to_text(card: Card, *, revised: dt.datetime | None = None) -> str:
    """One card, as vCard 4.0 text with CRLF line ends."""
    lines: list[str] = [["BEGIN:VCARD"], ["VERSION:4.0"], fold(f"PRODID:{PRODID}")]
    if card.uid:
        lines.append(_line("UID", clean(card.uid)))
    if card.is_org:
        lines.append(["KIND:org"])
    name = clean(card.name) or clean(card.company) or "?"
    lines.append(_line("FN", escape(name)))
    if card.family or card.given:
        lines.append(_line("N", f"{escape(card.family)};{escape(card.given)};;;"))
    if card.company or card.department:
        org = escape(card.company)
        if card.department:
            org += ";" + escape(card.department)
        lines.append(_line("ORG", org))
    if card.role:
        lines.append(_line("TITLE", escape(card.role)))
    if card.pronouns:
        lines.append(_line("PRONOUNS", escape(card.pronouns)))
    if card.email:
        lines.append(_line("EMAIL", escape(card.email), "TYPE=work"))
    for phone in card.phones:
        value, params = _tel_value(phone.number)
        if phone.kind in TEL_TYPES:
            params.append(f"TYPE={TEL_TYPES[phone.kind]}")
        if phone.primary:
            params.append("PREF=1")
        lines.append(_line("TEL", value, *params))
    for address in card.addresses:
        country = (address.country or "").strip().upper()
        params = []
        if address.kind in ADR_TYPES:
            params.append(f"TYPE={ADR_TYPES[address.kind]}")
        if address.primary:
            params.append("PREF=1")
        if re.fullmatch(r"[A-Z]{2}", country):
            params.append(f"CC={country}")
        parts = [
            "",
            "",
            escape(address.street, lines=True),
            escape(address.municipality),
            escape(address.region),
            escape(address.postcode),
            escape(phones.country_name(country) if country else ""),
        ]
        lines.append(_line("ADR", ";".join(parts), *params))
    for link in card.links:
        if clean(link.url):
            lines.append(_line("URL", clean(link.url).replace(" ", "%20")))
    for impp in card.impps:
        if clean(impp):
            lines.append(_line("IMPP", clean(impp).replace(" ", "%20")))
    if card.notes:
        lines.append(_line("NOTE", escape(card.notes, lines=True)))
    moment = revised or card.revised
    if moment is not None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.UTC)
        lines.append([f"REV:{moment.astimezone(dt.UTC):%Y%m%dT%H%M%SZ}"])
    lines.append(["END:VCARD"])
    return "".join(f"{piece}\r\n" for chunk in lines for piece in chunk)


def cards_to_text(cards) -> str:
    """Every card, one after the other."""
    return "".join(card_to_text(card) for card in cards)


# ------------------------------------------------------------------------- reading

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def _unfolded(text: str):
    """The content lines of ``text``, each as (line, too_long): folded lines joined, and a
    line past `MAX_LINE` cut where it passes it and said to be."""
    current: list[str] | None = None
    length, over = 0, False
    for raw in _LINE_BREAK.split(text):
        if raw[:1] in (" ", "\t") and current is not None:
            if not over:
                length += len(raw) - 1
                if length > MAX_LINE:
                    over = True
                else:
                    current.append(raw[1:])
            continue
        if current is not None:
            yield "".join(current), over
        current, length, over = [raw[:MAX_LINE]], len(raw), len(raw) > MAX_LINE
    if current is not None:
        yield "".join(current), over


def _split_unquoted(text: str, separator: str) -> list[str]:
    parts, current, quoted = [], [], False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == separator and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _split_escaped(text: str, separator: str) -> list[str]:
    """Split on a separator that is not backslash-escaped; the pieces keep their escapes."""
    parts, current, index = [], [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            current.append(text[index : index + 2])
            index += 2
            continue
        if char == separator:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def _property(line: str):
    """(name, params, value) of a content line, or nothing for one that is not."""
    quoted, index = False, -1
    for position, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            index = position
            break
    if index < 0:
        return None
    pieces = _split_unquoted(line[:index], ";")
    name = pieces[0].strip().rsplit(".", 1)[-1].upper()
    if not re.fullmatch(r"[A-Z0-9-]+", name):
        return None
    params: dict[str, list[str]] = {}
    for piece in pieces[1:]:
        key, equals, raw = piece.partition("=")
        if not equals:
            # `TEL;WORK:` is a 2.1 habit that 3.0 files still carry.
            key, raw = "TYPE", piece
        values = [value.strip() for value in raw.replace('"', "").split(",")]
        params.setdefault(key.strip().upper(), []).extend(v.lower() for v in values if v)
    return name, params, line[index + 1 :]


def _preference(params: dict) -> int | None:
    """The `PREF` of a property: a number in 4.0, a ``pref`` type in 3.0."""
    for value in params.get("PREF", ()):
        if value.isdigit():
            return int(value)
    return 1 if "pref" in params.get("TYPE", ()) else None


def _text(value: str, limit: int = MAX_TEXT, *, lines: bool = False) -> str:
    return clean(unescape(value), lines=lines)[:limit]


def _parts(value: str, *, lines: bool = False) -> list[str]:
    """A structured value's components, each unescaped and its own comma list joined."""
    return [
        ", ".join(
            filter(
                None,
                (
                    clean(unescape(item), lines=lines)[:MAX_TEXT]
                    for item in _split_escaped(part, ",")
                ),
            )
        )
        for part in _split_escaped(value, ";")
    ]


def _telephone_kind(types: list[str], version: str) -> str:
    if "fax" in types:
        return "fax"
    if "cell" in types or "mobile" in types:
        return "mobile"
    if "work" in types and "voice" in types and version == "4.0":
        return "switchboard"
    if "work" in types:
        return "work"
    if "home" in types:
        return "home"
    return ""


def _country_code(code: str, name: str) -> str:
    code = (code or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", code) and code in phones.BY_CODE:
        return code
    name = (name or "").strip()
    if re.fullmatch(r"[A-Za-z]{2}", name) and name.upper() in phones.BY_CODE:
        return name.upper()
    wanted = name.casefold()
    if wanted:
        for alpha2, _prefix, english in phones.COUNTRIES:
            if english.casefold() == wanted:
                return alpha2
    return ""


def parse_instant(text: str) -> dt.datetime | None:
    """A `REV` in the basic form (``20260102T030405Z``) or the extended one
    (``2026-01-02T03:04:05+01:00``), or nothing (postulo-dav#25)."""
    match = re.fullmatch(
        r"(\d{4})-?(\d{2})-?(\d{2})(?:T(\d{2}):?(\d{2}):?(\d{2})(?:[.,]\d+)?"
        r"(Z|[+-]\d{2}(?::?\d{2})?)?)?",
        (text or "").strip(),
    )
    if not match:
        return None
    year, month, day, hour, minute, second, zone = match.groups()
    offset = dt.UTC
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = dt.timezone(
            sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:] or 0))
        )
    try:
        return dt.datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=offset,
        )
    except ValueError:
        return None


def read(data: bytes | str) -> Read:
    """The cards in a file, or `VCardRefused` for one that is not read at all.

    Bounded before anything else: by size, then by cards, properties and lines. A card
    whose version is not 3.0 or 4.0 refuses the file, with the version it named.
    """
    if isinstance(data, bytes):
        if len(data) > MAX_BYTES:
            raise VCardRefused(
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": MAX_BYTES // (1024 * 1024)}
            )
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    else:
        text = data
        if len(text) > MAX_BYTES:
            raise VCardRefused(
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": MAX_BYTES // (1024 * 1024)}
            )
    result = Read()
    held: list[tuple[str, dict, str]] | None = None
    dropped: list[str] = []
    for line, too_long in _unfolded(text):
        if not line.strip():
            continue
        found = _property(line) if not too_long else None
        if too_long:
            name = re.match(r"[A-Za-z0-9.-]+", line)
            if name and held is not None:
                dropped.append(name.group().rsplit(".", 1)[-1].upper())
            continue
        if found is None:
            continue
        name, _params, value = found
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            held, dropped = [], []
        elif name == "END" and value.strip().upper() == "VCARD":
            if held is not None:
                if len(result.cards) < MAX_CARDS:
                    result.cards.append(_card(held, dropped))
                else:
                    result.skipped += 1
            held = None
        elif held is not None:
            if len(held) < MAX_PROPERTIES:
                held.append(found)
            else:
                dropped.append(name)
    if held is not None:
        # A card never closed: read as far as it went.
        if len(result.cards) < MAX_CARDS:
            result.cards.append(_card(held, dropped))
        else:
            result.skipped += 1
    return result


def _card(properties: list[tuple[str, dict, str]], dropped: list[str]) -> Card:
    version = next((value.strip() for name, _p, value in properties if name == "VERSION"), "")
    if version not in ("3.0", "4.0"):
        raise VCardRefused(
            _(
                "That file holds a vCard of version %(version)s. Postulo reads versions 3.0 "
                "and 4.0; export the contacts again from where they came from, as vCard 3.0 "
                "or 4.0."
            )
            % {"version": clean(version)[:20] or _("(none)")}
        )
    name = ""
    structured = ""
    kind = KIND_INDIVIDUAL
    company = department = role = title = pronouns = uid = ""
    emails: list[tuple[int, str]] = []
    telephones: list[tuple[int | None, Phone]] = []
    postals: list[tuple[int | None, Address]] = []
    links: list[Link] = []
    impps: list[str] = []
    notes: list[str] = []
    revised = None
    left: list[str] = list(dropped)
    counts = {"TEL": 0, "ADR": 0, "URL": 0, "IMPP": 0, "EMAIL": 0}
    for key, params, value in properties:
        if counts.get(key, 0) >= MAX_PER_KIND:
            left.append(key)
            continue
        if key in counts:
            counts[key] += 1
        if key == "FN" and not name:
            name = _text(value)
        elif key == "N" and not structured:
            family, given, *_rest = [*_parts(value), "", ""]
            structured = " ".join(part for part in (given, family) if part)
        elif key == "KIND":
            kind = KIND_ORG if value.strip().lower() == "org" else KIND_INDIVIDUAL
        elif key == "ORG" and not company:
            pieces = _parts(value)
            company = pieces[0][:200] if pieces else ""
            department = (pieces[1] if len(pieces) > 1 else "")[:120]
        elif key == "TITLE" and not title:
            title = _text(value, 200)
        elif key == "ROLE" and not role:
            role = _text(value, 200)
        elif key == "PRONOUNS" and not pronouns:
            pronouns = _text(value, 100)
        elif key == "EMAIL":
            emails.append((_preference(params) or 1000, clean(unescape(value))[:254]))
        elif key == "TEL":
            number = clean(unescape(value))
            if number.lower().startswith("tel:"):
                number = number[4:]
            number = number.split(";", 1)[0][:60].strip()
            if number:
                kind_of = _telephone_kind(params.get("TYPE", []), version)
                telephones.append((_preference(params), Phone(number, kind_of)))
        elif key == "ADR":
            postals.append((_preference(params), _address(params, value)))
        elif key == "URL":
            address = clean(unescape(value))[:500]
            if address:
                links.append(Link(address))
        elif key == "SOCIALPROFILE":
            address = clean(unescape(value))[:500]
            if re.match(r"https?://", address, re.IGNORECASE):
                links.append(Link(address, social=True))
            else:
                left.append(key)
        elif key == "IMPP":
            impps.append(clean(unescape(value))[:300])
        elif key == "NOTE":
            note = _text(value, MAX_NOTE, lines=True)
            if note:
                notes.append(note)
        elif key == "UID" and not uid:
            uid = clean(unescape(value))[:200]
        elif key == "REV" and revised is None:
            revised = parse_instant(value)
        elif key not in _KEPT:
            left.append(key)
    emails.sort(key=lambda row: row[0])
    if len([e for e in emails if e[1]]) > 1:
        left.append("EMAIL")
    first_email = next((email for _rank, email in emails if email), "")
    full_name = name or structured or company
    return Card(
        name=full_name[:200],
        kind=kind,
        company=company,
        department=department,
        role=title or role,
        email=first_email,
        phones=_with_primary(telephones),
        addresses=_with_primary(postals),
        links=tuple(links),
        impps=tuple(i for i in impps if i),
        notes="\n\n".join(notes)[:MAX_NOTE],
        pronouns=pronouns,
        uid=uid,
        revised=revised,
        dropped=tuple(dict.fromkeys(left)),
        version=version,
    )


def _address(params: dict, value: str) -> Address:
    parts = [*_parts(value, lines=True), "", "", "", "", "", "", ""][:7]
    box, extended, street, locality, region, code, country = parts
    kinds = params.get("TYPE", [])
    kind = next((k for k in ("home", "work", "postal") if k in kinds), "")
    named = _country_code((params.get("CC") or [""])[0], country)
    lines = [part for part in (box, extended, street) if part]
    return Address(
        street="\n".join(lines),
        postcode=code[:20],
        municipality=locality[:120],
        region=region[:120],
        country=named,
        kind=kind,
    )


def _with_primary(rows: list[tuple[int | None, object]]) -> tuple:
    """The items, the one with the lowest `PREF` marked primary, if any asked."""
    asked = [(rank, index) for index, (rank, _item) in enumerate(rows) if rank is not None]
    chosen = min(asked)[1] if asked else None
    return tuple(
        replace(item, primary=True) if index == chosen else item
        for index, (_rank, item) in enumerate(rows)
    )
