"""How a person is addressed and referred to: the lists *Your name* offers, per language (#309).

Two fields, and deliberately two. A **form of address** is written before a name -- Mr,
Mme, *Eng.ª* -- in a formal letter's greeting and, in some countries, at the top of a CV.
**Pronouns** say how to refer to somebody, and are written apart from the name. Somebody
may give either, both or neither; nothing is assumed when one is blank, and neither is ever
worked out from a name.

**These lists are data, not translations.** Portuguese *Eng.* is not the translation of
anything in English, French *iel* is not a translation of *they*, and English has no *Me*
for a lawyer. Each list is what is in common use in its language and nothing more, so it is
kept here as text in that language and never passed through a catalogue. A language with
no list offers only *Other*, which is a box to type in -- the honest answer for a language
nobody has written one for yet, and better than offering somebody another language's
conventions.

**What is stored is the text itself**, never a key into these lists: a profile keeps
"Dr.ª" rather than "pt-pt:3". So it survives the career record changing language, a list
here being revised, and an *Other* is just text like any other value.

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
- Portuguese pronouns: the object of the preposition *de* is how a Portuguese speaker
  says which set they use (*ela/dela*); *elu/delu* is the neutral set most asked for.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import translation

from postulo.resume.translatable import best_match

#: The value the *Other…* choice posts. The same word the rows of #284 use for their kind,
#: so the stylesheet's rule that shows a box only while Other is chosen reads both.
OTHER = "other"

#: What is written before a name, by language, in the order a form in that language lists
#: them.
FORMS_OF_ADDRESS: dict[str, tuple[str, ...]] = {
    "en": ("Mr", "Mrs", "Miss", "Ms", "Mx", "Dr", "Prof."),
    "fr": ("M.", "Mme", "Dr", "Pr", "Me"),
    "pt-pt": ("Sr.", "Sr.ª", "Dr.", "Dr.ª", "Eng.", "Eng.ª", "Prof.", "Prof.ª"),
    "pt-br": ("Sr.", "Sra.", "Dr.", "Dra.", "Prof.", "Prof.ª"),
}

#: Pronouns, by language, as a speaker of it would write the set they use.
PRONOUNS: dict[str, tuple[str, ...]] = {
    "en": ("she/her", "he/him", "they/them"),
    "fr": ("elle", "il", "iel"),
    "pt-pt": ("ela/dela", "ele/dele", "elu/delu"),
    "pt-br": ("ela/dela", "ele/dele", "elu/delu"),
}


def language_of(profile) -> str:
    """Which language's lists to offer somebody: their career record's, else the interface's.

    The interface's is the one this request is being drawn in, which is the profile's
    choice where it made one and the negotiated one where it did not.
    """
    return (
        (getattr(profile, "record_language", "") or "").strip()
        or translation.get_language()
        or settings.LANGUAGE_CODE
    )


def _in(table: dict[str, tuple[str, ...]], language: str) -> tuple[str, ...]:
    """The list for ``language``: its own, else its family's, else none.

    ``en-gb`` takes English's and a Portuguese not listed takes European Portuguese's, the
    first of the family here -- the norm the Portuguese-speaking countries of Africa write
    to. Never a different language's.
    """
    found = best_match(language, table)
    return table.get(found, ()) if found else ()


def forms_of_address(language: str) -> tuple[str, ...]:
    return _in(FORMS_OF_ADDRESS, language)


def pronouns(language: str) -> tuple[str, ...]:
    return _in(PRONOUNS, language)


def written_in(language: str) -> str:
    """The language the lists offered for ``language`` are written in, or nothing.

    What an option's ``lang`` says, so a screen reader reading English pronounces *Mme* as
    French (WCAG 3.1.2, Language of Parts).
    """
    return best_match(language, list(dict.fromkeys([*FORMS_OF_ADDRESS, *PRONOUNS])))
