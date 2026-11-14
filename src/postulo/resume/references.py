"""Publications as reference-manager files: BibTeX, BibLaTeX, RIS and CSL JSON (#879).

Zotero, Mendeley, JabRef, EndNote and a LaTeX bibliography all exchange references in a
handful of text formats, and a publication is already shaped like a BibTeX entry (#687, with
`publications.TYPES` the table). This is the one place that reads those files into the
shape of a publication and writes publications out as them.

**Libraries, not parsers.** BibTeX and BibLaTeX go through `bibtexparser` 2 (MIT) and the
LaTeX in a value through `pylatexenc` (MIT); RIS goes through `rispy` (MIT); CSL JSON is JSON,
read with the standard library, and its values by `lookups.from_csl`, which already holds a
record from a registry to the same rules. What this module adds is the mapping to and from a
publication's fields, and the limits.

**A file is hostile until read.** At most `MAX_BYTES`, at most `MAX_ENTRIES` entries, no
value longer than the column it lands in (a value is cut before it is decoded, so a
megabyte of LaTeX is never walked), a document nested too deep is refused, not crashed on.
Nothing in a file is executed, fetched or expanded beyond what the same file defines: a BibTeX
``@string`` is resolved by its own definition, as BibTeX does, and a file that names a
program, a URL to fetch or an entity to include has nothing here that acts on it. Every
value comes out as plain text on one line (no markup, no control or formatting characters),
and each field is then held to the rule the form holds a person to (`publications.sanitise`).

**What a person is told.** Entries that could not be read are counted, and the names of the
fields found that a publication has no place for (an abstract, keywords, an ISBN) are listed,
so that leaving them out is said and not silent.

**Offline.** Nothing here asks anybody anything. The reading of a DOI list, which does, is
`views_references` calling `lookups.from_doi` behind its own switch.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from . import lookups, publications

#: A bibliography of a long career is some hundreds of entries and some hundreds of
#: kilobytes. Two mebibytes and five hundred entries are a file somebody meant.
MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500
#: Only these are written to the review's session: a file is held parsed and capped, and
#: never as the file.
SESSION_KEY = "publication-references"

BIBTEX = "bibtex"
BIBLATEX = "biblatex"
RIS = "ris"
CSL_JSON = "csl-json"


class ReferencesRefused(Exception):
    """Why a file could not be read, in a sentence."""


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    content_type: str
    help: str


#: In the order the page offers them. The labels are the formats' own names: never translated.
FORMATS: dict[str, Format] = {
    BIBTEX: Format(
        BIBTEX, "BibTeX", "bib", "application/x-bibtex", "LaTeX, JabRef, Zotero, Overleaf"
    ),
    BIBLATEX: Format(
        BIBLATEX, "BibLaTeX", "bib", "application/x-bibtex", "biblatex and Biber, Zotero"
    ),
    RIS: Format(
        RIS, "RIS", "ris", "application/x-research-info-systems", "EndNote, Mendeley, Zotero"
    ),
    CSL_JSON: Format(
        CSL_JSON, "CSL JSON", "json", "application/vnd.citationstyles.csl+json", "Zotero, Pandoc"
    ),
}


@dataclass
class Reading:
    """What a file held: the entries as publication values, and what was not taken."""

    format: str
    entries: list[dict[str, str]] = field(default_factory=list)
    #: Entries that were in the file and could not be read, or had no title.
    skipped: int = 0
    #: Entries past `MAX_ENTRIES`, which were not read.
    beyond: int = 0
    #: Names of fields the file had and a publication has no place for, sorted.
    left_out: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- values as plain text


def _limit(name: str) -> int:
    return lookups._limit(name)


def text(value, limit: int) -> str:
    """A value as plain text on one line: control and formatting characters taken out.

    Not `lookups.clean`: a title in a ``.bib`` may hold a ``<`` that is a less-than sign, and
    ``&`` that is an ampersand, which `lookups.clean` would read as markup and as an entity.
    The page draws every value escaped, so nothing here needs to be stripped for safety;
    what is dropped is what hides text (a bidirectional override) or breaks a line.
    """
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    kept = "".join(
        ""
        if unicodedata.category(ch) == "Cf"
        else " "
        if unicodedata.category(ch) in ("Cc", "Zl", "Zp")
        else ch
        for ch in value
    )
    return " ".join(kept.split())[:limit].strip()


_LATEX = None


def _decoder():
    global _LATEX
    if _LATEX is None:
        from pylatexenc.latex2text import LatexNodes2Text

        # Math is kept as it was written: a formula in a title is not ours to evaluate.
        _LATEX = LatexNodes2Text(math_mode="verbatim", strict_latex_spaces=False)
    return _LATEX


def latex(value: str, limit: int) -> str:
    """The text a LaTeX value stands for: ``M{\\"u}ller`` is ``Müller``, braces are gone.

    Cut before it is decoded, to a few times the column, so a hostile value costs what a
    column costs.
    """
    value = value[: limit * 4]
    try:
        decoded = _decoder().latex_to_text(value)
    except Exception:
        # A malformed macro the decoder gave up on: the value as typed, braces dropped.
        decoded = value.replace("{", "").replace("}", "")
    return text(decoded, limit)


def _verbatim(value: str, limit: int) -> str:
    """A DOI or a link as written in a ``.bib``: LaTeX's escapes of ``_ % & # $`` undone."""
    value = re.sub(r"\\([_%&#$])", r"\1", value.replace("{", "").replace("}", ""))
    value = value.removeprefix("\\url").strip()
    return text(value, limit)


def _name_list(names: list[str], limit: int) -> str:
    """Names one per line, held to `lookups.MAX_AUTHORS` and the column, a whole name at a time."""
    found = [name for name in names if name]
    budget = limit - len("\n" + lookups.OTHERS)
    kept: list[str] = []
    used = -1
    for name in found[: lookups.MAX_AUTHORS]:
        if used + 1 + len(name) > budget:
            break
        kept.append(name)
        used += 1 + len(name)
    if len(kept) < len(found):
        kept.append(lookups.OTHERS)
    return "\n".join(kept)


def _split_and(value: str) -> list[str]:
    """BibTeX's ``a and b and c`` at brace depth zero: ``{Smith and Sons}`` is one name."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    words = re.split(r"(\s+)", value)
    for word in words:
        if word.isspace() or not word:
            current.append(word)
            continue
        if word.lower() == "and" and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        depth += word.count("{") - word.count("}")
        depth = max(depth, 0)
        current.append(word)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _month(value: str) -> str:
    """``5``, ``05``, ``may``, ``May`` as a two-digit month; nothing if it is not one."""
    value = value.strip().lower().strip("{}")
    names = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    if value.isdigit() and 1 <= int(value) <= 12:
        return f"{int(value):02d}"
    if value[:3] in names and value.isalpha():
        return f"{names.index(value[:3]) + 1:02d}"
    return ""


def _iso_date(*parts: str) -> str:
    """A year, year-month or year-month-day from what is there, if the rules take it."""
    digits = [part for part in parts if part]
    if not digits or not re.fullmatch(r"[0-9]{4}", digits[0]):
        return ""
    candidate = "-".join(digits)
    try:
        publications.validate_date(candidate)
    except ValidationError:
        candidate = digits[0]
        try:
            publications.validate_date(candidate)
        except ValidationError:
            return ""
    return candidate


def _date_text(value: str) -> str:
    """``2024``, ``2024-05``, ``2024-05-17``, ``2024/05/17/`` or ``2024///`` as an ISO date."""
    pieces = [piece for piece in re.split(r"[-/\s]+", value.strip()) if piece]
    if not pieces or not re.fullmatch(r"[0-9]{4}", pieces[0]):
        return ""
    rest: list[str] = []
    for piece in pieces[1:3]:
        if not piece.isdigit():
            break
        rest.append(f"{int(piece):02d}")
    return _iso_date(pieces[0], *rest)


_LANGUAGE_NAMES = {
    "english": "en",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "portuguese": "pt",
    "italian": "it",
    "dutch": "nl",
}


def _language(value: str) -> str:
    value = text(value, 40).lower()
    return lookups.language_from(_LANGUAGE_NAMES.get(value, value))


def _web_address(value: str) -> str:
    """A link that is an ``http`` or ``https`` address; anything else is nothing."""
    address = text(value, 500)
    parts = urlsplit(address)
    if parts.scheme in ("http", "https") and parts.netloc and " " not in address:
        return address
    return ""


def _pages(value: str) -> str:
    """``12--15`` and ``12-15`` as ``12–15``, as a person types them; one page stays one."""
    return re.sub(r"\s*(?:-{1,3}|–|—)\s*", "–", value)


# ------------------------------------------------------------------------ BibTeX types

#: BibTeX's and biblatex's entry types onto `publications.TYPES`; an unknown one is *Other*.
BIBTEX_TYPES = {
    "article": "article",
    "inproceedings": "inproceedings",
    "conference": "inproceedings",
    "book": "book",
    "mvbook": "book",
    "collection": "book",
    "mvcollection": "book",
    "inbook": "inbook",
    "bookinbook": "inbook",
    "suppbook": "inbook",
    "incollection": "incollection",
    "inreference": "incollection",
    "proceedings": "proceedings",
    "mvproceedings": "proceedings",
    "phdthesis": "phdthesis",
    "mastersthesis": "mastersthesis",
    "techreport": "techreport",
    "report": "techreport",
    "manual": "manual",
    "booklet": "booklet",
    "unpublished": "unpublished",
    "online": "online",
    "electronic": "online",
    "www": "online",
    "dataset": "dataset",
    "software": "software",
    "misc": "misc",
}

#: What a publication calls each, going out. BibTeX proper has no home for the last three.
BIBTEX_OUT = {"online": "misc", "dataset": "misc", "software": "misc"}

#: Fields of a ``.bib`` that map onto nothing of a publication and are worth naming when found.
_KNOWN_FIELDS = {
    "author",
    "editor",
    "title",
    "journal",
    "journaltitle",
    "booktitle",
    "publisher",
    "institution",
    "school",
    "organization",
    "address",
    "location",
    "year",
    "month",
    "date",
    "volume",
    "number",
    "issue",
    "pages",
    "edition",
    "series",
    "chapter",
    "doi",
    "url",
    "note",
    "language",
    "langid",
    "type",
    "key",
}


def _first(fields: dict[str, str], *names: str) -> str:
    return next((fields[name] for name in names if fields.get(name)), "")


def _from_bibtex_entry(entry) -> tuple[dict[str, str], set[str]]:
    fields = {
        field_.key.lower(): field_.value for field_ in entry.fields if isinstance(field_.value, str)
    }
    kind = BIBTEX_TYPES.get(entry.entry_type.lower(), "misc")
    if entry.entry_type.lower() == "thesis":
        kind = "mastersthesis" if "master" in fields.get("type", "").lower() else "phdthesis"
    values = {"entry_type": kind}

    def one(name: str, *keys: str, decode=latex) -> None:
        raw = _first(fields, *keys)
        if raw:
            values[name] = decode(raw, _limit(name))

    one("title", "title")
    for name, key in (("authors", "author"), ("editors", "editor")):
        if raw := fields.get(key):
            people = [
                lookups.OTHERS if part.lower() == "others" else latex(part, 200)
                for part in _split_and(raw[:20000])
            ]
            values[name] = _name_list(people, _limit(name))
    one("container_title", "journaltitle", "journal", "booktitle")
    one("publisher", "publisher")
    one("institution", "institution", "school", "organization")
    one("location", "address", "location")
    one("volume", "volume")
    one("number", "number", "issue")
    one("edition", "edition")
    one("series", "series")
    one("chapter", "chapter")
    one("note", "note")
    if raw := fields.get("pages"):
        values["pages"] = _pages(latex(raw, _limit("pages")))
    one("doi", "doi", decode=lambda raw, n: lookups.doi_from(_verbatim(raw, n)))
    one("url", "url", decode=lambda raw, n: _web_address(_verbatim(raw, n)))
    if fields.get("date"):
        values["date"] = _date_text(text(fields["date"], 40))
    else:
        values["date"] = _iso_date(
            text(fields.get("year", ""), 4), _month(text(fields.get("month", ""), 20))
        )
    one("language", "langid", "language", decode=lambda raw, _n: _language(raw))
    if entry.key:
        values["cite_key"] = text(entry.key, publications.CITE_KEY_MAX_LENGTH)
    left_out = {name for name in fields if name not in _KNOWN_FIELDS}
    return values, left_out


def read_bibtex(raw: bytes) -> Reading:
    import bibtexparser
    from bibtexparser.model import Entry

    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Many a ``.bib`` is Latin-1; every byte is a letter there, so this cannot fail.
        source = raw.decode("latin-1")
    try:
        library = bibtexparser.parse_string(source)
    except RecursionError as error:
        raise ReferencesRefused(_("That file is not BibTeX.")) from error
    except Exception as error:
        raise ReferencesRefused(_("That file is not BibTeX.")) from error
    reading = Reading(format=BIBTEX)
    reading.skipped = len(library.failed_blocks)
    left_out: set[str] = set()
    seen = 0
    for block in library.blocks:
        if not isinstance(block, Entry):
            continue
        seen += 1
        if seen > MAX_ENTRIES:
            reading.beyond += 1
            continue
        try:
            values, ignored = _from_bibtex_entry(block)
        except Exception:
            reading.skipped += 1
            continue
        left_out |= ignored
        if not values.get("title"):
            reading.skipped += 1
            continue
        reading.entries.append({name: value for name, value in values.items() if value})
    reading.left_out = sorted(left_out)
    return reading


# --------------------------------------------------------------------------------- RIS

RIS_TYPES = {
    "JOUR": "article",
    "JFULL": "article",
    "MGZN": "article",
    "NEWS": "article",
    "EJOUR": "article",
    "CONF": "proceedings",
    "CPAPER": "inproceedings",
    "BOOK": "book",
    "EBOOK": "book",
    "EDBOOK": "book",
    "CHAP": "incollection",
    "ECHAP": "incollection",
    "THES": "phdthesis",
    "RPRT": "techreport",
    "DATA": "dataset",
    "COMP": "software",
    "ELEC": "online",
    "WEB": "online",
    "BLOG": "online",
    "UNPB": "unpublished",
    "MANSCPT": "unpublished",
    "GEN": "misc",
}

RIS_OUT = {
    "article": "JOUR",
    "inproceedings": "CPAPER",
    "proceedings": "CONF",
    "book": "BOOK",
    "inbook": "CHAP",
    "incollection": "CHAP",
    "phdthesis": "THES",
    "mastersthesis": "THES",
    "techreport": "RPRT",
    "manual": "GEN",
    "booklet": "GEN",
    "unpublished": "UNPB",
    "online": "ELEC",
    "dataset": "DATA",
    "software": "COMP",
    "misc": "GEN",
}

#: RIS tags worth naming when a file has them and a publication has nowhere to keep them.
_RIS_NAMES = {
    "abstract": "abstract",
    "notes_abstract": "abstract",
    "keywords": "keywords",
    "issn": "ISBN/ISSN",
    "author_address": "author address",
    "file_attachments1": "attached files",
    "file_attachments2": "attached files",
}


def _ris_list(record: dict, *keys: str) -> list[str]:
    found: list[str] = []
    for key in keys:
        value = record.get(key)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, str))
        elif isinstance(value, str):
            found.append(value)
    return found


def _from_ris_record(record: dict) -> tuple[dict[str, str], set[str]]:
    kind = RIS_TYPES.get(str(record.get("type_of_reference", "")).upper(), "misc")
    if kind == "phdthesis" and "master" in str(record.get("type_of_work", "")).lower():
        kind = "mastersthesis"
    values: dict[str, str] = {"entry_type": kind}
    # TI and T1 both are "the title"; for a chapter or a paper T2 is the book or the journal.
    values["title"] = text(record.get("title") or record.get("primary_title"), _limit("title"))
    values["authors"] = _name_list(
        [text(n, 200) for n in _ris_list(record, "authors", "first_authors")], _limit("authors")
    )
    values["editors"] = _name_list(
        [text(n, 200) for n in _ris_list(record, "secondary_authors")], _limit("editors")
    )
    values["container_title"] = text(
        record.get("journal_name")
        or record.get("alternate_title3")
        or record.get("secondary_title"),
        _limit("container_title"),
    )
    if kind == "proceedings" and not values["title"]:
        # A CONF record may name the proceedings only in T2.
        values["title"] = values["container_title"]
    values["publisher"] = text(record.get("publisher"), _limit("publisher"))
    values["location"] = text(record.get("place_published"), _limit("location"))
    values["volume"] = text(record.get("volume"), _limit("volume"))
    values["number"] = text(record.get("number"), _limit("number"))
    values["edition"] = text(record.get("edition"), _limit("edition"))
    values["note"] = text(
        " ".join(_ris_list(record, "notes")) or record.get("note"), _limit("note")
    )
    start, end = text(record.get("start_page"), 20), text(record.get("end_page"), 20)
    values["pages"] = _pages(f"{start}-{end}" if start and end else start or end)
    values["date"] = _date_text(text(record.get("date") or "", 40)) or _date_text(
        text(record.get("year") or "", 40)
    )
    doi = text(record.get("doi"), publications.DOI_MAX_LENGTH)
    values["doi"] = lookups.doi_from(doi)
    for address in _ris_list(record, "urls"):
        if link := _web_address(address):
            values["url"] = link
            break
    values["language"] = _language(str(record.get("language") or ""))
    values["cite_key"] = text(record.get("id"), publications.CITE_KEY_MAX_LENGTH)
    left_out = {label for key, label in _RIS_NAMES.items() if record.get(key)}
    return values, left_out


def read_ris(raw: bytes) -> Reading:
    import rispy

    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = raw.decode("latin-1")
    try:
        records = rispy.loads(source)
    except Exception as error:
        raise ReferencesRefused(_("That file is not RIS.")) from error
    reading = Reading(format=RIS)
    left_out: set[str] = set()
    for index, record in enumerate(records):
        if index >= MAX_ENTRIES:
            reading.beyond += 1
            continue
        try:
            values, ignored = _from_ris_record(record)
        except Exception:
            reading.skipped += 1
            continue
        left_out |= ignored
        if not values.get("title"):
            reading.skipped += 1
            continue
        reading.entries.append({name: value for name, value in values.items() if value})
    reading.left_out = sorted(left_out)
    return reading


# -------------------------------------------------------------------------- CSL JSON

#: CSL's ``type`` onto ours, on top of `lookups.TYPE_MAP`.
_CSL_OUT = {
    "article": "article-journal",
    "inproceedings": "paper-conference",
    "proceedings": "book",
    "book": "book",
    "inbook": "chapter",
    "incollection": "chapter",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "manual": "book",
    "booklet": "pamphlet",
    "unpublished": "manuscript",
    "online": "webpage",
    "dataset": "dataset",
    "software": "software",
    "misc": "document",
}


def _csl_names(people) -> list[str]:
    if not isinstance(people, list):
        return []
    return [name for name in (lookups._name(person) for person in people[:2000]) if name]


def _from_csl_record(record: dict) -> dict[str, str]:
    values = lookups.from_csl(record)
    values["authors"] = _name_list(_csl_names(record.get("author")), _limit("authors")) or ""
    values["editors"] = _name_list(_csl_names(record.get("editor")), _limit("editors")) or ""
    if record.get("type") == "thesis" and "master" in str(record.get("genre") or "").lower():
        values["entry_type"] = "mastersthesis"
    kind = values.get("entry_type")
    # A thesis or a report is held by an institution, which CSL calls its publisher.
    if kind in ("phdthesis", "mastersthesis", "techreport") and values.get("publisher"):
        values["institution"] = values.pop("publisher")
    values["location"] = lookups.clean(
        record.get("publisher-place") or record.get("event-place"), _limit("location")
    )
    values["edition"] = lookups.clean(record.get("edition"), _limit("edition"))
    values["series"] = lookups.clean(record.get("collection-title"), _limit("series"))
    values["chapter"] = lookups.clean(record.get("chapter-number"), _limit("chapter"))
    values["note"] = lookups.clean(record.get("note"), _limit("note"))
    values["url"] = _web_address(lookups.clean(record.get("URL"), 500))
    ident = record.get("id")
    if isinstance(ident, (str, int)) and not isinstance(ident, bool):
        values["cite_key"] = text(ident, publications.CITE_KEY_MAX_LENGTH)
    return {name: value for name, value in values.items() if value}


def read_csl_json(raw: bytes) -> Reading:
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ReferencesRefused(_("That file is not CSL JSON.")) from error
    if isinstance(document, dict) and isinstance(document.get("items"), list):
        document = document["items"]
    elif isinstance(document, dict):
        document = [document]
    if not isinstance(document, list):
        raise ReferencesRefused(_("That file is not CSL JSON."))
    reading = Reading(format=CSL_JSON)
    for index, record in enumerate(document):
        if index >= MAX_ENTRIES:
            reading.beyond += 1
            continue
        if not isinstance(record, dict):
            reading.skipped += 1
            continue
        try:
            values = _from_csl_record(record)
        except Exception:
            reading.skipped += 1
            continue
        if not values.get("title"):
            reading.skipped += 1
            continue
        reading.entries.append(values)
        reading.left_out = sorted(
            {*reading.left_out, *(n for n in ("abstract", "keyword") if record.get(n))}
        )
    return reading


# ------------------------------------------------------------------------- detection


def detect(raw: bytes, name: str = "") -> str:
    """Which format a file is, from its name and then its first line; ``""`` if none."""
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    head = raw[:4096].decode("utf-8-sig", errors="replace").lstrip()
    if head.startswith(("{", "[")):
        return CSL_JSON
    if re.match(r"TY\s{1,2}-", head) or suffix == "ris":
        return RIS
    if head.startswith("@") or suffix in ("bib", "bibtex", "biblatex") or "\n@" in head:
        return BIBTEX
    return ""


def read(raw: bytes, name: str = "") -> Reading:
    """The publications in a file, in whichever of the formats it is. Refuses with the reason."""
    if len(raw) > MAX_BYTES:
        raise ReferencesRefused(
            _("That file is larger than %(limit)s MB, so it was not read.")
            % {"limit": MAX_BYTES // (1024 * 1024)}
        )
    fmt = detect(raw, name)
    if not fmt:
        raise ReferencesRefused(
            _("That file is not one of BibTeX, BibLaTeX, RIS or CSL JSON, so it was not read.")
        )
    reader = {BIBTEX: read_bibtex, RIS: read_ris, CSL_JSON: read_csl_json}[fmt]
    return reader(raw)


# ------------------------------------------------------------------ the session's copy

#: The fields a held entry may carry: a publication's own, nothing else.
FIELDS = (
    "entry_type",
    "title",
    "authors",
    "editors",
    "container_title",
    "publisher",
    "institution",
    "location",
    "date",
    "volume",
    "number",
    "pages",
    "edition",
    "series",
    "chapter",
    "doi",
    "url",
    "note",
    "language",
    "cite_key",
)


def held(raw) -> list[dict[str, str]]:
    """What the session holds, read as untrusted: a list of dicts of text, of known fields."""
    if not isinstance(raw, list):
        return []
    kept = []
    for entry in raw[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        kept.append(
            {
                name: entry[name]
                for name in FIELDS
                if isinstance(entry.get(name), str) and entry[name]
            }
        )
    return kept


# ------------------------------------------------------------------------- writing


def _escape(value: str) -> str:
    """A value for a ``.bib``: LaTeX's special characters protected, braces balanced.

    A brace is dropped and not escaped, so no value can close the entry it is in. Letters
    are written as they are, in Unicode, which BibLaTeX with Biber, XeLaTeX, LuaLaTeX and
    LaTeX with ``\\usepackage[utf8]{inputenc}`` read.
    """
    value = " ".join(value.split())
    value = value.replace("{", "").replace("}", "")
    value = value.replace("\\", "\\textbackslash{}")
    for char in "&%$#_":
        value = value.replace(char, "\\" + char)
    return value.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}")


def _bib_names(value: str) -> str:
    people = []
    for line in publications.names(value):
        if line == lookups.OTHERS:
            people.append("others")
            continue
        line = _escape(line)
        # ``{Smith and Sons}``: a name that holds the word "and" is one name.
        people.append("{" + line + "}" if re.search(r"\sand\s", line, re.IGNORECASE) else line)
    return " and ".join(people)


def _month_name(date: str) -> str:
    parts = date.split("-")
    if len(parts) >= 2:
        return "jan feb mar apr may jun jul aug sep oct nov dec".split()[int(parts[1]) - 1]
    return ""


def bib_fields(item, dialect: str) -> list[tuple[str, str]]:
    """One publication's fields as a ``.bib`` names them, in BibTeX's or BibLaTeX's words."""
    biblatex = dialect == BIBLATEX
    out: list[tuple[str, str]] = []

    def put(name: str, value: str, raw: bool = False) -> None:
        if value:
            out.append((name, value if raw else _escape(value)))

    # Protected in braces so that BibTeX's styles keep the capitals the title was typed with.
    out.append(("title", "{" + _escape(item.title) + "}" if not biblatex else _escape(item.title)))
    put("author", _bib_names(item.authors), raw=True)
    put("editor", _bib_names(item.editors), raw=True)
    if item.container_title:
        if item.entry_type == "article":
            put("journaltitle" if biblatex else "journal", item.container_title)
        elif item.entry_type in ("inproceedings", "inbook", "incollection"):
            put("booktitle", item.container_title)
        else:
            put("titleaddon" if biblatex else "howpublished", item.container_title)
    put("publisher", item.publisher)
    if item.institution:
        if item.entry_type in ("phdthesis", "mastersthesis") and not biblatex:
            put("school", item.institution)
        elif item.entry_type in ("inproceedings", "proceedings", "manual") and not biblatex:
            put("organization", item.institution)
        else:
            put("institution", item.institution)
    put("location" if biblatex else "address", item.location)
    if biblatex:
        put("date", item.date, raw=True)
    else:
        put("year", item.date[:4], raw=True)
        put("month", _month_name(item.date))
    put("volume", item.volume)
    put("number", item.number)
    put("pages", item.pages.replace("–", "--").replace("—", "--"))
    put("edition", item.edition)
    put("series", item.series)
    put("chapter", item.chapter)
    put("doi", item.doi.replace("{", "").replace("}", ""), raw=True)
    put("url", item.url.replace("{", "").replace("}", ""), raw=True)
    put("langid" if biblatex else "language", item.language, raw=True)
    put("note", item.note)
    return out


def write_bibtex(items, dialect: str = BIBTEX) -> str:
    import bibtexparser
    from bibtexparser.model import Entry, Field

    library = bibtexparser.Library()
    taken: set[str] = set()
    for item in items:
        kind = (
            item.entry_type
            if dialect == BIBLATEX
            else BIBTEX_OUT.get(item.entry_type, item.entry_type)
        )
        key = publications.free_key(item.cite_key or f"publication{item.pk}", taken)
        taken.add(key)
        library.add(
            Entry(
                entry_type=kind,
                key=key,
                fields=[Field(name, value) for name, value in bib_fields(item, dialect)],
            )
        )
    return bibtexparser.write_string(library)


def _ris_name(line: str) -> str:
    return "" if line == lookups.OTHERS else line


def _ris_date(date: str) -> str:
    parts = [*date.split("-"), "", "", ""][:3]
    return "/".join(parts) + "/" if date else ""


def write_ris(items) -> str:
    import rispy

    records = []
    for item in items:
        record: dict = {"type_of_reference": RIS_OUT.get(item.entry_type, "GEN")}
        record["title"] = item.title
        if authors := [n for n in map(_ris_name, publications.names(item.authors)) if n]:
            record["authors"] = authors
        if editors := [n for n in map(_ris_name, publications.names(item.editors)) if n]:
            record["secondary_authors"] = editors
        if item.container_title:
            key = "journal_name" if item.entry_type == "article" else "secondary_title"
            record[key] = item.container_title
        if item.publisher or item.institution:
            record["publisher"] = item.publisher or item.institution
        if item.location:
            record["place_published"] = item.location
        if item.date:
            record["year"] = _ris_date(item.date)
        if item.volume:
            record["volume"] = item.volume
        if item.number:
            record["number"] = item.number
        if item.pages:
            span = re.fullmatch(r"\s*(.+?)\s*[–—-]+\s*(.+?)\s*", item.pages)
            first, last = span.groups() if span else (item.pages, "")
            record["start_page"] = first
            if last:
                record["end_page"] = last
        if item.edition:
            record["edition"] = item.edition
        if item.doi:
            record["doi"] = item.doi
        if item.url:
            record["urls"] = [item.url]
        if item.language:
            record["language"] = item.language
        if item.note:
            record["notes"] = [item.note]
        if item.cite_key:
            record["id"] = item.cite_key
        if item.entry_type == "mastersthesis":
            record["type_of_work"] = "Master's thesis"
        records.append(record)
    return rispy.dumps(records)


def _csl_person(line: str) -> dict | None:
    if line == lookups.OTHERS:
        return None
    if "," in line:
        family, given = (part.strip() for part in line.split(",", 1))
        return {"family": family, "given": given}
    words = line.split()
    if len(words) > 1:
        return {"family": words[-1], "given": " ".join(words[:-1])}
    return {"literal": line}


def csl_record(item) -> dict:
    record: dict = {"id": item.cite_key or f"publication{item.pk}"}
    record["type"] = _CSL_OUT.get(item.entry_type, "document")
    record["title"] = item.title
    for key, value in (("author", item.authors), ("editor", item.editors)):
        people = [p for p in map(_csl_person, publications.names(value)) if p]
        if people:
            record[key] = people
    if item.entry_type == "mastersthesis":
        record["genre"] = "Master's thesis"
    elif item.entry_type == "phdthesis":
        record["genre"] = "PhD thesis"
    if item.container_title:
        record["container-title"] = item.container_title
    holder = item.publisher or (
        item.institution if item.entry_type in ("phdthesis", "mastersthesis", "techreport") else ""
    )
    if holder:
        record["publisher"] = holder
    if item.location:
        record["publisher-place"] = item.location
    if item.date:
        record["issued"] = {"date-parts": [[int(part) for part in item.date.split("-")]]}
    for source, target in (
        (item.volume, "volume"),
        (item.number, "issue"),
        (item.pages, "page"),
        (item.edition, "edition"),
        (item.series, "collection-title"),
        (item.chapter, "chapter-number"),
        (item.doi, "DOI"),
        (item.url, "URL"),
        (item.language, "language"),
        (item.note, "note"),
    ):
        if source:
            record[target] = source
    return record


def write_csl_json(items) -> str:
    return json.dumps([csl_record(item) for item in items], ensure_ascii=False, indent=2) + "\n"


def write(fmt: str, items) -> str:
    """The publications as one file in ``fmt``."""
    items = list(items)
    if fmt in (BIBTEX, BIBLATEX):
        return write_bibtex(items, fmt)
    if fmt == RIS:
        return write_ris(items)
    if fmt == CSL_JSON:
        return write_csl_json(items)
    raise KeyError(fmt)
