"""What a publication is, in BibTeX's terms: the types, the fields each uses, and the rules (#687).

A career record has a place for a paper, a chapter or a thesis, and it is shaped like the
entry a bibliography holds, so that a plugin which reads or writes a ``.bib`` file later has
a table to map and not a free-text box to guess at. One model with a type, as BibTeX has it,
and **one table** below that says what each type is: its label, the fields BibTeX asks of
it and the fields it merely allows. The form, the hint about what is missing, the candidate
file and the tests all read that table; nothing else decides which type uses which field.

The names are biblatex's data model (3.22a), which is maintained and reads classic BibTeX:
``container_title`` stands for ``journal``/``journaltitle``, ``booktitle`` and the title of a
series of proceedings, and ``institution`` for ``school`` and ``organization`` as well. The
column names are Postulo's; the mapping to BibTeX's lives here with the type.

**Nothing here is looked up.** A DOI is checked for its shape and stored bare; it is linked
as ``https://doi.org/<doi>`` and never resolved by this code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from postulo.core import personal

#: How long a citation key may be, and what it may hold: ASCII letters and digits and
#: ``- _ : . + /``, which is Postulo's own bound (BibTeX documents none).
CITE_KEY_MAX_LENGTH = 80
CITE_KEY = re.compile(r"[A-Za-z0-9_:.+/-]+")

#: A DOI as the DOI Handbook gives it: ``10.``, a registrant code of four to nine digits and
#: optional sub-divisions, a slash, and a suffix with no whitespace in it.
DOI = re.compile(r"10\.[0-9]{4,9}(?:\.[0-9]+)*/\S+")
DOI_MAX_LENGTH = 200
#: What a DOI is often pasted with: the resolver's address, or the label.
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

#: The fields every type offers, whatever BibTeX says of it: BibTeX ignores what a type does
#: not name, and biblatex allows all of these on every entry.
COMMON = ("doi", "url", "language", "note", "cite_key")
#: The fields the form always shows. ``order`` is the form's own business.
ALWAYS = ("entry_type", "title", "date")


@dataclass(frozen=True)
class EntryType:
    """One BibTeX type: what it is called, and what it asks of an entry and allows."""

    label: object
    #: What BibTeX asks of it. A tuple within is a choice: ``("authors", "editors")`` is "an
    #: author or an editor". Asked as a hint only: somebody may hold nothing but a title.
    required: tuple[tuple[str, ...], ...]
    optional: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        """Every field the form shows for this type, required first."""
        found: list[str] = []
        for group in self.required:
            found.extend(group)
        found.extend(self.optional)
        return tuple(dict.fromkeys(found))


def _t(label, *required: str | tuple[str, ...], optional: tuple[str, ...] = ()) -> EntryType:
    return EntryType(
        label,
        tuple((one,) if isinstance(one, str) else one for one in required),
        optional,
    )


#: The BibTeX types, less the ``conference`` alias of ``inproceedings``, and biblatex's
#: ``online``, ``dataset`` and ``software``, which are what a researcher lists and BibTeX has
#: no home for (a BibTeX-only writer sets them as ``@misc``). In the order the menu offers
#: them; the key is what is stored and what BibTeX calls the type.
TYPES: dict[str, EntryType] = {
    "article": _t(
        gettext_lazy("Journal article"),
        "authors",
        "title",
        "container_title",
        "date",
        optional=("volume", "number", "pages"),
    ),
    "inproceedings": _t(
        gettext_lazy("Paper in proceedings"),
        "authors",
        "title",
        "container_title",
        "date",
        optional=(
            "editors",
            "volume",
            "number",
            "series",
            "pages",
            "location",
            "institution",
            "publisher",
        ),
    ),
    "book": _t(
        gettext_lazy("Book"),
        ("authors", "editors"),
        "title",
        "publisher",
        "date",
        optional=("volume", "number", "series", "location", "edition"),
    ),
    "inbook": _t(
        gettext_lazy("Part of a book"),
        ("authors", "editors"),
        "title",
        "container_title",
        "date",
        optional=(
            "publisher",
            "chapter",
            "pages",
            "volume",
            "number",
            "series",
            "location",
            "edition",
        ),
    ),
    "incollection": _t(
        gettext_lazy("Chapter in a collection"),
        "authors",
        "title",
        "container_title",
        "publisher",
        "date",
        optional=(
            "editors",
            "volume",
            "number",
            "series",
            "chapter",
            "pages",
            "location",
            "edition",
        ),
    ),
    "proceedings": _t(
        gettext_lazy("Proceedings"),
        "title",
        "date",
        optional=(
            "editors",
            "volume",
            "number",
            "series",
            "location",
            "institution",
            "publisher",
        ),
    ),
    "phdthesis": _t(
        gettext_lazy("PhD thesis"),
        "authors",
        "title",
        "institution",
        "date",
        optional=("location",),
    ),
    "mastersthesis": _t(
        gettext_lazy("Master's thesis"),
        "authors",
        "title",
        "institution",
        "date",
        optional=("location",),
    ),
    "techreport": _t(
        gettext_lazy("Technical report"),
        "authors",
        "title",
        "institution",
        "date",
        optional=("number", "location"),
    ),
    "manual": _t(
        gettext_lazy("Manual"),
        "title",
        optional=("authors", "institution", "location", "edition", "date"),
    ),
    "booklet": _t(
        gettext_lazy("Booklet"),
        "title",
        optional=("authors", "publisher", "location", "date"),
    ),
    "unpublished": _t(
        gettext_lazy("Unpublished"),
        "authors",
        "title",
        "note",
        optional=("date",),
    ),
    "online": _t(
        gettext_lazy("Online"),
        ("authors", "editors"),
        "title",
        "date",
        "url",
        optional=("container_title", "publisher"),
    ),
    "dataset": _t(
        gettext_lazy("Dataset"),
        ("authors", "editors"),
        "title",
        "date",
        optional=("publisher", "edition", "location"),
    ),
    "software": _t(
        gettext_lazy("Software"),
        "authors",
        "title",
        "date",
        optional=("publisher", "edition", "location"),
    ),
    "misc": _t(
        gettext_lazy("Other"),
        optional=("authors", "publisher", "location", "date"),
    ),
}

DEFAULT_TYPE = "article"
CHOICES = [(key, entry_type.label) for key, entry_type in TYPES.items()]

#: Every field a type may use, in the order the form draws them, `ALWAYS` and `COMMON` aside.
FIELD_ORDER = (
    "authors",
    "editors",
    "container_title",
    "publisher",
    "institution",
    "location",
    "volume",
    "number",
    "pages",
    "edition",
    "series",
    "chapter",
)


def fields_for(entry_type: str) -> tuple[str, ...]:
    """The fields the form offers for a type: the ones every entry has, and the type's own.

    An unknown type offers what *Other* does. The answer never decides what is **kept**: a
    value in a field the type does not use stays where it is, so that changing the type and
    changing it back loses nothing.
    """
    own = TYPES.get(entry_type, TYPES["misc"]).fields
    return tuple(
        name
        for name in (*ALWAYS, *FIELD_ORDER, *COMMON)
        if name in ALWAYS or name in COMMON or name in own
    )


def types_using(name: str) -> tuple[str, ...]:
    """Every type whose form offers this field."""
    return tuple(key for key in TYPES if name in fields_for(key))


def missing_for(entry_type: str, said) -> list[str]:
    """What BibTeX asks of this type that the entry does not say, as field names.

    ``said`` is anything with a value for each field name. A choice (``authors`` or
    ``editors``) counts as said when either is. Only ever a hint: an entry with a title and
    nothing else is a perfectly good entry.
    """
    lacking = []
    for group in TYPES.get(entry_type, TYPES["misc"]).required:
        if not any(str(said(name) or "").strip() for name in group):
            lacking.append(group[0])
    return lacking


# ------------------------------------------------------------------ the rules of a value


def normalise_doi(value) -> str:
    """A DOI as it is stored: bare, without the resolver's address or a label before it."""
    return _DOI_PREFIX.sub("", str(value or "").strip()).strip()


def validate_doi(value) -> None:
    """The shape of a DOI, and nothing more: whether it exists is not asked here."""
    if value and not DOI.fullmatch(value):
        raise ValidationError(
            _("A DOI looks like 10.1000/182: “10.”, a number, a slash and the rest."),
            code="doi",
        )


def doi_url(doi: str) -> str:
    """Where a DOI is linked: the resolver's address, never fetched by Postulo."""
    return f"https://doi.org/{doi}" if doi else ""


def validate_cite_key(value) -> None:
    if value and not CITE_KEY.fullmatch(value):
        raise ValidationError(
            _("A key holds letters, digits and the characters - _ : . + / and nothing else."),
            code="cite_key",
        )


def validate_date(value) -> None:
    """A year, a year and month, or a whole date, as ISO 8601 writes them.

    The reading a date of birth is held to (#679), without the rule that it be in the past:
    a paper may be forthcoming.
    """
    if value and personal.reduced_date_parts(value) is None:
        raise ValidationError(
            _(
                "Give a year, a year and month, or a whole date, such as 2024, 2024-05 or "
                "2024-05-17."
            ),
            code="publication_date",
        )


def year_of(date: str) -> str:
    return (date or "")[:4]


# -------------------------------------------------------------------------- the names


def names(text: str) -> list[str]:
    """Names as typed, one per line, blank lines left out."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _family_name(name: str) -> str:
    """The part of a typed name that a key is made of: before the comma, or the last word."""
    if "," in name:
        return name.split(",", 1)[0]
    return name.split()[-1] if name.split() else ""


def _ascii_word(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "", folded).lower()


#: Words a key does not take from a title.
_SMALL_WORDS = frozenset(
    "a an the of on in for and to with from at by as is are into its their"
    " o os um uma de da do das dos em no na e para por com sobre".split()
)


def suggest_key(authors: str, editors: str, date: str, title: str) -> str:
    """A key from the first author, the year and the first word of the title that counts.

    ``knuth1974art``: never printed, and the person's to change. Empty where there is
    nothing to make one from.
    """
    people = names(authors) or names(editors)
    family = _ascii_word(_family_name(people[0])) if people else ""
    word = next(
        (
            ascii_word
            for raw in re.split(r"\s+", title or "")
            if (ascii_word := _ascii_word(raw)) and ascii_word not in _SMALL_WORDS
        ),
        "",
    )
    return f"{family}{year_of(date)}{word}"[: CITE_KEY_MAX_LENGTH - 3]


def free_key(wanted: str, taken) -> str:
    """The key itself where nobody holds it, else with ``a``, ``b``... after it."""
    if not wanted:
        return ""
    if wanted not in taken:
        return wanted
    for letter in "abcdefghijklmnopqrstuvwxyz":
        if f"{wanted}{letter}" not in taken:
            return f"{wanted}{letter}"
    number = 2
    while f"{wanted}-{number}" in taken:
        number += 1
    return f"{wanted}-{number}"


def sanitise(values: dict, taken) -> dict:
    """What an archive says of a publication, held to the rules the form holds a person to.

    A value that breaks one is left out, and the row is restored without it: an unknown type
    is *Other*, a date or a DOI that is not shaped like one and a key that is not a key, or
    is one of this person's already, are blank (a blank key is made from the entry on save).
    A file is the one place a value arrives that no page has looked at (#687).
    """
    kept = dict(values)
    # A file may say a number or a list where a page would have sent text.
    for name in ("entry_type", "date", "cite_key"):
        if not isinstance(kept.get(name, ""), str):
            kept[name] = ""
    if not isinstance(kept.get("doi", ""), (str, int, float)):
        kept["doi"] = ""
    if kept.get("entry_type") not in TYPES:
        kept["entry_type"] = "misc"
    kept["doi"] = normalise_doi(kept.get("doi"))
    for name, check in (
        ("date", validate_date),
        ("doi", validate_doi),
        ("cite_key", validate_cite_key),
    ):
        try:
            check(kept.get(name))
        except ValidationError:
            kept[name] = ""
    if kept.get("cite_key") in taken:
        kept["cite_key"] = ""
    return kept


# ------------------------------------------------------------------- the line a CV prints


def citation_parts(item) -> tuple[str, str]:
    """The neutral line a CV prints for a publication, and the DOI's address that ends it.

    Authors, title, where it appeared, volume and pages, year, and then the DOI: no named
    citation style (that is a later plugin's), in the document's language through `gettext`,
    which the caller has set. The address is returned apart so that a page may link it and a
    text file may write it, and the two say the same words.
    """
    people = names(item.authors) or names(item.editors)
    if people and not names(item.authors):
        people[-1] = _("%(name)s (ed.)") % {"name": people[-1]}
    volume = _joined(
        "",
        _("vol. %(volume)s") % {"volume": item.volume} if item.volume else "",
        f"({item.number})" if item.number else "",
    )
    where = ", ".join(
        part
        for part in (
            item.container_title,
            volume.strip(),
            _("pp. %(pages)s") % {"pages": item.pages} if item.pages else "",
            item.publisher or item.institution,
        )
        if part
    )
    text = ". ".join(
        part
        for part in (
            "; ".join(people),
            item.title,
            where,
            year_of(item.date),
        )
        if part
    )
    address = doi_url(item.doi)
    # The full stop that stands before the address, so that a page which links the address
    # and a file which writes it say the same words.
    return (f"{text}." if text and address else text), address


def _joined(separator: str, *parts: str) -> str:
    return separator.join(part for part in parts if part)


def citation(item) -> str:
    """`citation_parts` as one string, the DOI's address last."""
    text, address = citation_parts(item)
    return f"{text} {address}" if text and address else text or address
