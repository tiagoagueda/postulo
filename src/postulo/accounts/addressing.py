"""How a person is addressed and referred to, and what gender they say: the lists *Your details*
offers, per language (#309, #681).

Two fields, and deliberately two. A **form of address** is written before a name -- Mr,
Mme, *Eng.ª* -- in a formal letter's greeting and, in some countries, at the top of a CV.
**Pronouns** say how to refer to somebody, and are written apart from the name. Somebody
may give either, both or neither; nothing is assumed when one is blank, and neither is ever
worked out from a name.

A third list sits in the same card and follows the same promises (#681): **gender**, in the
person's own words or not at all. It is a short list of what is in common use, and *Other…* for
anything else; setting it never sets, narrows or suggests a form of address or pronouns, and
the reverse. No code reads one of the three to decide another, and none reads any of them to
decide anything but what a CV prints.

**What is stored is a concept, not a word** (#921): ``he``, ``doctor_f``, ``non_binary``, the
same whatever language the page or the CV is in. The words below are how each language
*writes* a concept, and are looked up when something is drawn, so a profile switched from
English to French shows *il* for ``he`` without a byte of it changing, and a CV in Portuguese
prints *Dr.ª* for ``doctor_f`` whichever language the person typed it in. **Other…** is the
one exception: it is free text that the person typed in their own words, stored as typed and
shown as typed in every language, and nothing is ever stored for it that is not what they
wrote. A value that matches the word of a concept in any language this module knows is that
concept (`concept_of`), so typing *Mme* under *Other…* stores ``mrs``.

**The lists are data, not translations.** Portuguese *Eng.* is not the translation of
anything in English, French *iel* is not a translation of *they*, and English has no *Me*
for a lawyer. Each language offers what is in common use in it and nothing more, written
here as text in that language with the source beside it. A language with no list offers only
*Other*, the honest answer for a language nobody has written one for yet. A concept a
language does not write (a ``maitre`` printed in English) is drawn from the catalogues
(`label`), where an empty entry is the concept's English word and Weblate fills the rest:
better than printing nothing for something the person chose.

**Gendered titles with a neutral fallback.** English and French have one *Dr* and one
*Prof.* for everybody, Portuguese has *Dr.* and *Dr.ª*. The concepts are therefore
``doctor`` and ``doctor_f`` (likewise ``professor``, ``engineer``): a language that has no
feminine form prints the unmarked word for both, and a value stored as the English *Dr* or
*Prof.*, which does not say, becomes the unmarked concept. Nothing here ever decides
``doctor_f`` from the gender or the name.

Which list is offered follows the language of the career record, since that is the
language the name is written in beside everything else, and the interface's when the
record says nothing.

Sources, so a list can be argued with rather than guessed at:

- English: British usage (*New Hart's Rules*, Oxford), which drops the full stop from a
  contraction, a word that keeps its last letter (Mr, Mrs, Dr), and keeps it on a word cut
  off at its end, which is why *Prof.* has one where the rest have none. *Mx*, the form
  that does not say a gender, is in the Oxford English Dictionary since 2015 and offered
  by many of the UK's public bodies.
- French: the abbreviations of the *Lexique des règles typographiques en usage à
  l'Imprimerie nationale* (M., Mme, Dr, Pr, Me); *Mademoiselle* is left out, as the Prime
  Minister's circular of 21 February 2012 took it off administrative forms. *iel* is in
  *Le Robert* since 2021.
- European Portuguese: the *Código de Redação Interinstitucional* of the Publications
  Office of the European Union, which writes the feminine with a superior *a* (Sr.ª, Dr.ª,
  Eng.ª, Prof.ª).
- Brazilian Portuguese: the Federal Senate's *Manual de Comunicação* (Sr., Sra., Dr.,
  Dra., Prof., Prof.ª). *Eng.* before a name is Portuguese usage rather than Brazilian.
- Gender (#681): three plain words per language -- the female, the male and the non-binary
  answer, which #864 named as *Female*, *Male* and *Non-binary* (fr *Féminin*, *Masculin*,
  *Non binaire*; pt *Feminino*, *Masculino*, *Não binário*) after the lists first said
  *Woman* and *Man* -- and no more, since *Other…* is there for the rest and a list that offers only
  two would be wrong for many people. No authority publishes a list of genders for the forms
  of any of these languages, and none is claimed: the words are the ones the general
  dictionaries carry for each (the *Oxford English Dictionary* has *non-binary*, *Le Robert*
  has *non binaire*, and the Portuguese dictionaries have *não binário*), with the words
  the same in both Portuguese norms. The concept stored is ``female``, ``male`` or
  ``non_binary``; the mapping to vCard's ``GENDER`` that #660 will write is the free-text
  identity with an empty sex component (``GENDER:;Non-binary``), never a guess at ``M`` or
  ``F``, and a card read back never sets the field without the person confirming it.
- Pronouns: the concepts are ``she``, ``he`` and ``they``. In Portuguese the object of the
  preposition *de* is how a Portuguese speaker says which set they use (*ela/dela*);
  *elu/delu* is the neutral set most asked for.
"""

from __future__ import annotations

from django.utils.translation import pgettext_lazy

from postulo.core import languages

#: The value the *Other…* choice posts. The same word the rows of #284 use for their kind,
#: so the stylesheet's rule that shows a box only while Other is chosen reads both.
OTHER = "other"

FORM_OF_ADDRESS = "form_of_address"
PRONOUNS = "pronouns"
GENDER = "gender"
FIELDS = (FORM_OF_ADDRESS, PRONOUNS, GENDER)

#: Every concept of each field, in the order a list shows them (#921). These keys are what a
#: profile stores; they are never shown.
CONCEPTS: dict[str, tuple[str, ...]] = {
    FORM_OF_ADDRESS: (
        "mr",
        "mrs",
        "miss",
        "ms",
        "mx",
        "doctor",
        "doctor_f",
        "professor",
        "professor_f",
        "engineer",
        "engineer_f",
        "maitre",
    ),
    PRONOUNS: ("she", "he", "they"),
    GENDER: ("female", "male", "non_binary"),
}

#: The unmarked concept of a gendered one, which is what a language without the feminine form
#: prints, and what a bare *Dr* or *Prof.* (which does not say) becomes (#921).
UNMARKED: dict[str, str] = {
    "doctor_f": "doctor",
    "professor_f": "professor",
    "engineer_f": "engineer",
}

#: How each language writes the concepts it has a word for, by field. Sources are in the
#: module's note. A language with a list here also has the ``OFFERED`` subset below.
WORDS: dict[str, dict[str, dict[str, str]]] = {
    FORM_OF_ADDRESS: {
        "en": {
            "mr": "Mr",
            "mrs": "Mrs",
            "miss": "Miss",
            "ms": "Ms",
            "mx": "Mx",
            "doctor": "Dr",
            "doctor_f": "Dr",
            "professor": "Prof.",
            "professor_f": "Prof.",
        },
        "fr": {
            "mr": "M.",
            "mrs": "Mme",
            "doctor": "Dr",
            "doctor_f": "Dr",
            "professor": "Pr",
            "professor_f": "Pr",
            "maitre": "Me",
        },
        "pt-PT": {
            "mr": "Sr.",
            "mrs": "Sr.ª",
            "doctor": "Dr.",
            "doctor_f": "Dr.ª",
            "engineer": "Eng.",
            "engineer_f": "Eng.ª",
            "professor": "Prof.",
            "professor_f": "Prof.ª",
        },
        "pt-BR": {
            "mr": "Sr.",
            "mrs": "Sra.",
            "doctor": "Dr.",
            "doctor_f": "Dra.",
            "professor": "Prof.",
            "professor_f": "Prof.ª",
        },
    },
    PRONOUNS: {
        "en": {"she": "she/her", "he": "he/him", "they": "they/them"},
        "fr": {"she": "elle", "he": "il", "they": "iel"},
        "pt-PT": {"she": "ela/dela", "he": "ele/dele", "they": "elu/delu"},
        "pt-BR": {"she": "ela/dela", "he": "ele/dele", "they": "elu/delu"},
    },
    GENDER: {
        "en": {"female": "Female", "male": "Male", "non_binary": "Non-binary"},
        "fr": {"female": "Féminin", "male": "Masculin", "non_binary": "Non binaire"},
        "pt-PT": {"female": "Feminino", "male": "Masculino", "non_binary": "Não binário"},
        "pt-BR": {"female": "Feminino", "male": "Masculino", "non_binary": "Não binário"},
    },
}

#: What each language offers in its menu, in the order a form in that language lists them.
#: A gendered pair is offered as two where the language has two forms, and as the unmarked
#: concept alone where it has one (English and French: one *Dr*, one *Prof.*).
OFFERED: dict[str, dict[str, tuple[str, ...]]] = {
    FORM_OF_ADDRESS: {
        "en": ("mr", "mrs", "miss", "ms", "mx", "doctor", "professor"),
        "fr": ("mr", "mrs", "doctor", "professor", "maitre"),
        "pt-PT": (
            "mr",
            "mrs",
            "doctor",
            "doctor_f",
            "engineer",
            "engineer_f",
            "professor",
            "professor_f",
        ),
        "pt-BR": ("mr", "mrs", "doctor", "doctor_f", "professor", "professor_f"),
    },
    PRONOUNS: dict.fromkeys(WORDS[PRONOUNS], ("she", "he", "they")),
    GENDER: dict.fromkeys(WORDS[GENDER], ("female", "male", "non_binary")),
}

#: The catalogue entry of each concept, for a language the tables above do not cover and for
#: a concept a covered language does not write (#921). The message is the English word, so an
#: untranslated entry reads as the concept's English word and Weblate fills the rest; the
#: context says what it is, since *Me* and *Ms* mean nothing apart from it. A feminine
#: concept shares the unmarked one's entry.
CATALOGUE: dict[str, dict[str, object]] = {
    FORM_OF_ADDRESS: {
        "mr": pgettext_lazy("form of address", "Mr"),
        "mrs": pgettext_lazy("form of address", "Mrs"),
        "miss": pgettext_lazy("form of address", "Miss"),
        "ms": pgettext_lazy("form of address", "Ms"),
        "mx": pgettext_lazy("form of address", "Mx"),
        "doctor": pgettext_lazy("form of address", "Dr"),
        "professor": pgettext_lazy("form of address", "Prof."),
        "engineer": pgettext_lazy("form of address", "Eng."),
        "maitre": pgettext_lazy("form of address", "Me"),
    },
    PRONOUNS: {
        "she": pgettext_lazy("pronouns", "she/her"),
        "he": pgettext_lazy("pronouns", "he/him"),
        "they": pgettext_lazy("pronouns", "they/them"),
    },
    GENDER: {
        "female": pgettext_lazy("gender", "Female"),
        "male": pgettext_lazy("gender", "Male"),
        "non_binary": pgettext_lazy("gender", "Non-binary"),
    },
}

#: The words the gender lists offered before #864, each with the word that replaced it. A
#: profile, an archive and a candidate file written before store these as text; they are
#: read as the concept their replacement is (`concept_of`) and `current_gender_word` still
#: says what the new word was. Why they are the same concept: "Woman" and "Femme" were the
#: first list's wording of the female answer in each language, "Man" and "Homme" of the male,
#: and so on -- #864 changed the wording and never the meaning.
LEGACY_GENDERS: dict[str, str] = {
    "Woman": "Female",
    "Man": "Male",
    "Femme": "Féminin",
    "Homme": "Masculin",
    "Mulher": "Feminino",
    "Homem": "Masculino",
}


def current_gender_word(word: str) -> str:
    """The word the lists offer now for one they offered before (#864), else ``word`` as it is."""
    return LEGACY_GENDERS.get(word, word)


def _fold(text: str) -> str:
    """What two spellings of one word share: case folded, a trailing full stop dropped."""
    return (text or "").strip().casefold().removesuffix(".").strip()


def _build_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {field: {} for field in FIELDS}
    for field in FIELDS:
        seen = index[field]
        # The unmarked concept of a word shared by both of a pair must win: English and
        # French print one *Dr* for both, and a bare *Dr* does not say.
        ordered = sorted(
            ((key, word) for table in WORDS[field].values() for key, word in table.items()),
            key=lambda pair: pair[0] in UNMARKED,
        )
        for key, word in ordered:
            seen.setdefault(_fold(word), key)
        for key in CONCEPTS[field]:
            seen.setdefault(_fold(key), key)
    for old, new in LEGACY_GENDERS.items():
        index[GENDER].setdefault(_fold(old), index[GENDER][_fold(new)])
    return index


_INDEX = _build_index()


def concept_of(field: str, text: str) -> str:
    """The concept somebody's text names in any language this module knows, else ``""``.

    ``Mme`` is ``mrs``, ``iel`` is ``they``, a bare ``Dr`` is the unmarked ``doctor`` and
    ``Dr.ª`` is ``doctor_f``; so is the concept's own key. Nothing else is a concept: text
    that matches none is what somebody typed under *Other…* and stays as typed.
    """
    return _INDEX[field].get(_fold(text), "")


def stored_value(field: str, text: str) -> str:
    """What to store for a text from the page, the API, an archive or a file: the concept
    where it names one, else the text, stripped, as it was typed."""
    text = (text or "").strip()
    return concept_of(field, text) or text


def language_of(profile) -> str:
    """Which language's lists to offer somebody: their career record's, else the interface's.

    The interface's is the one this request is being drawn in, which is the profile's
    choice where it made one and the negotiated one where it did not.
    """
    return (getattr(profile, "record_language", "") or "").strip() or languages.current()


def offered(field: str, language: str) -> tuple[str, ...]:
    """The concepts ``language`` offers for ``field``: its own list, else its family's, else
    none. ``en-GB`` takes English's and a Portuguese not listed takes European Portuguese's,
    the first of the family here. Never a different language's."""
    found = languages.match(language, OFFERED[field])
    return OFFERED[field].get(found, ()) if found else ()


def forms_of_address(language: str) -> tuple[str, ...]:
    return offered(FORM_OF_ADDRESS, language)


def pronouns(language: str) -> tuple[str, ...]:
    return offered(PRONOUNS, language)


def genders(language: str) -> tuple[str, ...]:
    return offered(GENDER, language)


def label(field: str, key: str, language: str) -> str:
    """The word for a concept in ``language``; text that is not a concept, as it was typed.

    The language's own table first, then the unmarked sibling of a gendered concept it has
    no feminine for, then the catalogues in ``language``.
    """
    key = (key or "").strip()
    if key not in CONCEPTS[field]:
        return key
    found = languages.match(language, WORDS[field])
    table = WORDS[field].get(found, {}) if found else {}
    for candidate in (key, UNMARKED.get(key)):
        if candidate in table:
            return table[candidate]
    entry = CATALOGUE[field][UNMARKED.get(key, key)]
    with languages.override(language or languages.current()):
        return str(entry)


def choices(field: str, language: str) -> list[tuple[str, str]]:
    """``(key, label)`` for each concept ``language`` offers, in its order."""
    shown = written_in(language) or language
    return [(key, label(field, key, shown)) for key in offered(field, language)]


def written_in(language: str) -> str:
    """The language the lists offered for ``language`` are written in, or nothing.

    What an option's ``lang`` says, so a screen reader reading English pronounces *Mme* as
    French (WCAG 3.1.2, Language of Parts).
    """
    return languages.match(
        language,
        list(dict.fromkeys(lang for field in FIELDS for lang in OFFERED[field])),
    )
