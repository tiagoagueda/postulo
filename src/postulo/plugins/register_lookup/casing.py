"""Write what a register publishes in capitals the way a person would write it (#1244).

The Annuaire des Entreprises answers ``15 AV DES CHAMPS ELYSEES`` and ``DUPONT SERVICES``. This
module turns the text of a lookup into ``15 Avenue des Champs Elysees`` and ``Dupont Services``.
It is pure (text in, text out) and touches only text that is wholly in capitals: a register that
already writes ``Forusbeen 50`` is left as it is, and so is anything a person typed.

Rules, in the order they apply to each word:

* A word held in capitals on purpose stays: one in ``keep`` (the company's own sigle, or a word
  the register puts in brackets), a legal form (``SAS``, ``SARL``, ``ASA``), a roman numeral
  (``XIV``), and, as the one rule for acronyms, **any word of two or more letters with no
  vowel** (``SNCF``, ``BNP``) and **a name that is a single word of at most four letters**
  (``AXA``, ``EDF``). Everything else that is short and has a vowel becomes a word
  (``Orange``), because the other way round would leave every ``DUPONT`` in capitals.
* ``ST`` and ``STE`` become ``Saint`` and ``Sainte`` (French text only).
* The small words ``de``, ``du``, ``des``, ``la``, ``le``, ``les``, ``et``, ``sur``, ``sous``,
  ``en``, ``au``, ``aux`` stay in lower case, except as the first word of the text.
* ``l'`` and ``d'`` (and the other elisions) are kept in lower case before a word, and what
  follows takes a capital. A hyphen starts a new capital (``Saint-Denis``).
* Accents are never guessed: the register publishes capitals without them, and a letter that is
  not there is left as it is.
"""

from __future__ import annotations

import re
import unicodedata

#: The street type codes of Sirene's ``type_voie`` list, spelled out.
STREET_TYPES = {
    "ALL": "Allée",
    "AV": "Avenue",
    "BD": "Boulevard",
    "CAR": "Carrefour",
    "CHE": "Chemin",
    "CHS": "Chaussée",
    "CITE": "Cité",
    "COR": "Corniche",
    "CRS": "Cours",
    "DOM": "Domaine",
    "DSC": "Descente",
    "ECA": "Écart",
    "ESP": "Esplanade",
    "FG": "Faubourg",
    "GR": "Grande Rue",
    "HAM": "Hameau",
    "HLE": "Halle",
    "IMP": "Impasse",
    "LD": "Lieu-dit",
    "LOT": "Lotissement",
    "MAR": "Marché",
    "MTE": "Montée",
    "PAS": "Passage",
    "PL": "Place",
    "PLN": "Plaine",
    "PLT": "Plateau",
    "PRO": "Promenade",
    "PRV": "Parvis",
    "QUA": "Quartier",
    "QUAI": "Quai",
    "RES": "Résidence",
    "RLE": "Ruelle",
    "ROC": "Rocade",
    "RPT": "Rond-point",
    "RTE": "Route",
    "RUE": "Rue",
    "SEN": "Sentier",
    "SQ": "Square",
    "TPL": "Terre-plein",
    "TRA": "Traverse",
    "VLA": "Villa",
    "VLGE": "Village",
    "VOIE": "Voie",
    "ZA": "Zone d'activités",
    "ZAC": "Zone d'aménagement concerté",
    "ZAD": "Zone d'aménagement différé",
    "ZI": "Zone industrielle",
    "ZONE": "Zone",
}

PARTICLES = frozenset(
    {"de", "du", "des", "la", "le", "les", "et", "sur", "sous", "en", "au", "aux", "lès", "lez"}
)

#: Legal forms that are written in capitals (those without a vowel are kept anyway).
LEGAL_FORMS = frozenset(
    {
        "SA",
        "SAS",
        "SASU",
        "SARL",
        "EURL",
        "SCI",
        "SNC",
        "SCP",
        "SEL",
        "SELARL",
        "SELAS",
        "GIE",
        "GAEC",
        "EARL",
        "SCOP",
        "SCEA",
        "AS",
        "ASA",
        "ANS",
        "DA",
        "NUF",
        "AB",
        "OY",
        "OYJ",
        "AG",
        "GMBH",
        "LLC",
        "LTD",
        "PLC",
    }
)

# A roman numeral from 2 to 39, written with I, V and X only: ``XIV`` but never ``MIX`` or ``DIX``.
_ROMAN = re.compile(r"(?=[IVX]{2,}$)X{0,3}(IX|IV|V?I{0,3})$")
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_VOWELS = set("AEIOUYÆØÅŒ")
_ELISION = re.compile(r"(['’])")


def _has_vowel(word: str) -> bool:
    plain = unicodedata.normalize("NFD", word.upper())
    return any(letter in _VOWELS for letter in plain)


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def _piece(word: str, *, first: bool, keep: frozenset, french: bool, lone: bool) -> str:
    if word in keep or word in LEGAL_FORMS or (_ROMAN.match(word) and len(word) > 1):
        return word
    if french and word in ("ST", "STE"):
        return "Saint" if word == "ST" else "Sainte"
    if len(word) > 1 and not _has_vowel(word):
        return word
    if lone and len(word) <= 4:
        return word
    if word.lower() in PARTICLES and not first:
        return word.lower()
    return _cap(word)


def _run(run: str, *, first: bool, keep: frozenset, french: bool, lone: bool) -> str:
    done = []
    for index, part in enumerate(run.split("-")):
        bits = _ELISION.split(part)
        out = []
        for position in range(0, len(bits), 2):
            word = bits[position]
            elided = position + 1 < len(bits)
            start = first and index == 0 and position == 0
            if elided:
                out.append(_cap(word) if start else word.lower())
            else:
                out.append(
                    _piece(
                        word,
                        first=start,
                        keep=keep,
                        french=french,
                        lone=lone and len(run) == len(word),
                    )
                )
            if elided:
                out.append(bits[position + 1])
        done.append("".join(out))
    return "-".join(done)


def _write(text, *, keep, french, lone, first):
    if not isinstance(text, str) or not text or not text.isupper():
        return text or ""
    kept = frozenset(word.upper() for word in keep)
    begin = [True] if first else [False]

    def replace(match: re.Match) -> str:
        before = text[match.start() - 1 : match.start()]
        after = text[match.end() : match.end() + 1]
        if before.isdigit() or after.isdigit():
            return match.group()
        at_start, begin[0] = begin[0], False
        return _run(match.group(), first=at_start, keep=kept, french=french, lone=lone)

    return _WORD.sub(replace, text)


def name(text, *, keep=(), french=False):
    """A company's name; a name of one short word, as ``AXA``, is an acronym and stays."""
    text = text if isinstance(text, str) else ""
    words = _WORD.findall(text)
    brackets = frozenset(re.findall(r"\(([^()]+)\)", text))
    return _write(
        text,
        keep=tuple(keep) + tuple(brackets),
        french=french,
        lone=len(words) == 1,
        first=True,
    )


def place(text, *, french=False, first=True):
    """A street or a municipality (``first=False`` for a street that follows its type)."""
    return _write(text, keep=(), french=french, lone=False, first=first)


def street_type(code):
    """The street type spelled out from a Sirene code; a word already spelled out is cased."""
    if not isinstance(code, str) or not code.strip():
        return ""
    code = code.strip()
    return STREET_TYPES.get(code.upper()) or place(code, french=True)
