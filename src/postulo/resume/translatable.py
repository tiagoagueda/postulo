"""Which parts of a career entry may be said in another language, and how one is chosen.

The rules `translating` works by, and nothing that works: which fields are translatable
(`TRANSLATABLE`), and what an entry already
says in one. The models declare their `Translation` column with these, the forms offer a box
per field with them, and a document asks the language of its record with them -- so they sit
below all three. They were the top of `translating`, which reads the documents a CV renders
into, so the models and forms that needed only the rules closed a loop through it (#248).
`translating` still hands out every name here.
"""

from __future__ import annotations

from postulo.core import languages

#: What may be said differently in another language, by model, and what may not.
#:
#: Each exclusion is a name that belongs to somebody else. ``organisation`` and
#: ``institution`` are not translated because *Universidade de Lisboa* stays that on an
#: English CV; translating it invents an employer who never existed. `Certification` is
#: absent altogether because both of its texts are the awarding body's wording — the name
#: of a credential *is* the credential, and an English rendering of it is not one.
#:
#: What is here is either the person's own words (``summary``, ``highlights``, a project
#: they named) or a thing that genuinely differs between languages: a job title, a city
#: (Lisbon, Lisboa), a qualification, a grade on a scale that does not exist elsewhere.
TRANSLATABLE: dict[str, tuple[str, ...]] = {
    "experience": ("role", "location", "summary", "highlights"),
    "education": ("qualification", "field_of_study", "location", "grade", "highlights"),
    "project": ("name", "role", "summary", "highlights"),
    # A course's title and provider are the provider's wording; its summary is the person's (#695).
    "course": ("summary",),
    "skillgroup": ("name",),
    "skill": ("name",),
    "languageskill": ("name",),
    # `drivinglicence` is absent on purpose: a code is a code, and a country is a country (#691).
    "link": ("title", "description"),
    # An honour says its summary in the person's own words; the prize and who gave it are
    # theirs, as a certification's two texts are (#693).
    "honour": ("summary",),
    # A membership's organisation stays as written, as an experience's does; what the person
    # was in it and what they say of it are theirs to put into another language (#693).
    "membership": ("role", "summary"),
}

#: The models that translate nothing, each with its reason: a decision written down, not an
#: omission. `tests/test_career_sections.py` asks every section for one or the other.
TRANSLATES_NOTHING: dict[str, str] = {
    "certification": "both texts are the awarding body's wording",
    "publication": "a paper's title is the paper",
    "drivinglicence": "a code is a code, and a country is a country",
    "reference": "the candidate file has no block for it, so a row would point at nothing (#696)",
}

#: How long a stored field name may be. Longer than any name above, and bounded because it
#: is a column.
MAX_FIELD_LENGTH = 40


def _model_name(subject) -> str:
    meta = getattr(subject, "_meta", None)
    return meta.model_name if meta is not None else ""


def fields_for(subject) -> tuple[str, ...]:
    """Which of this entry's fields may be said differently in another language."""
    return TRANSLATABLE.get(_model_name(subject), ())


# ------------------------------------------------------------------ matching a language
#
# How one code is written and compared with another is `postulo.core.languages`' to say,
# for a career entry as for everything else (#337). The rule this module used to hold is
# the last step of `languages.match`: ``pt-BR`` takes ``pt-PT`` rather than printing
# English, because a Brazilian reader given European Portuguese has read the entry, and
# one given English has not. Never another language.


# --------------------------------------------------------- what an entry says, in a language


def _gather(rows, allowed: set[str]) -> dict[str, dict[str, str]]:
    """``{language: {field: text}}`` from translation rows, keeping only what counts.

    Blank texts are dropped rather than kept, because a translation somebody emptied is a
    translation somebody withdrew, and the honest rendering of it is the original. A field
    outside ``allowed`` is dropped too: `TRANSLATABLE` can lose a field between releases,
    and a row nobody can edit any more should not still be printing.
    """
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.field not in allowed or not row.text.strip():
            continue
        found.setdefault(languages.tag(row.language), {})[row.field] = row.text
    return found


def stored_for(entry) -> dict[str, dict[str, str]]:
    """Every translation this entry carries: ``{language: {field: text}}``.

    One query, for one entry. `overrides_by_entry` is the one to reach for when there are
    several, which on a CV there always are.
    """
    return _gather(entry.translations.all(), set(fields_for(entry)))


def overrides_for(entry, language: str) -> dict[str, str]:
    """What this entry says in ``language``, for the fields it says anything in."""
    stored = stored_for(entry)
    matched = languages.match(language, stored)
    return dict(stored.get(matched, {})) if matched else {}


# ---------------------------------------------------- which language the record is in


def record_language_of(user) -> str:
    """The language somebody's career record is written in, best answer first.

    What they said, then the language they read Postulo in, then the instance default —
    the same chain `documents.models.document_language` walks for a document, because
    the question is the same one asked of the other half of the pair. Answering it is what
    lets a CV in the record's own language report nothing rather than everything.
    """
    from django.conf import settings

    profile = getattr(user, "profile", None) if user is not None else None
    for candidate in (
        getattr(profile, "record_language", ""),
        getattr(profile, "language", ""),
        settings.LANGUAGE_CODE,
    ):
        if (candidate or "").strip():
            return languages.tag(candidate)
    return ""
