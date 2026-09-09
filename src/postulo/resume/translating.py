"""A career entry that says the same thing in more than one language.

> also multiple language documents cohabiting should be normal

At the document level this was already true: `CV.language` and `CoverLetter.language` each
declare what the document is written in, and `document_direction()` lays the page out for
that rather than for whoever is reading Postulo. One level down it was not. The career
record held one text per field per entry, so a CV declaring ``fr-fr`` printed exactly the
same English job titles as one declaring ``en-gb``, and the honest way to keep a CV in two
languages was to keep two careers — a second set of entries, typed again, drifting apart
from the first the moment a date changed (#131).

**A second language is a translation, not a second record.** One `Translation` row per
entry, per language, per field, and the entry is otherwise untouched: a date corrected on
the master copy is corrected in every language at once, which is the whole point of there
being a master copy.

**Which fields may be translated is a decision per field, not a mechanism applied
uniformly**, and `TRANSLATABLE` below is that decision with the reasoning attached. A
blanket per-language copy of every field would have invited somebody to translate the one
thing that has to stay verbatim — an employer's name, a credential's title — and the
invitation is the harm, because the CV that comes out is wrong in a way only the awarding
body would notice.

**Falling back is visible, or it is a trap.** An entry with no text in the language a CV
declares renders its original, because a blank line where a job used to be is worse than a
line in the wrong language. But the person is told which entries did that, on the CV's own
page, *before* they export — the same rule #132 settled for themes, for the same reason.
Discovering it in the PDF an employer already has is not discovering it.

**What is left for later, deliberately.** A Europass file may in practice be one of a
bilingual pair, and the reader collapses each file to one language. Nothing here prevents
the second file's text from arriving later as translations of the first — that is what this
storage is shaped to hold — but reading two files as one career is its own piece of work.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    "skillgroup": ("name",),
    "skill": ("name",),
    "languageskill": ("name",),
    "link": ("title", "description"),
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


def may_translate(subject, field: str) -> bool:
    return field in fields_for(subject)


# ------------------------------------------------------------------ matching a language


def normalise(code: str) -> str:
    return (code or "").strip().lower().replace("_", "-")


def base(code: str) -> str:
    return normalise(code).partition("-")[0]


def best_match(wanted: str, available) -> str:
    """The stored language closest to ``wanted``, or empty if none is close enough.

    Exact first, then the same base language — ``pt-br`` takes ``pt-pt`` rather than
    printing English, because a Brazilian reader given European Portuguese has read the
    entry, and one given English has not. Never the other way round from a base to an
    unrelated variant: ``pt`` matching ``pt-pt`` is the same language, ``fr`` matching
    ``fr-ca`` is too, and that is as far as this goes.
    """
    wanted = normalise(wanted)
    if not wanted:
        return ""
    available = [normalise(one) for one in available]
    if wanted in available:
        return wanted
    family = base(wanted)
    return next((one for one in available if base(one) == family), "")


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
        found.setdefault(normalise(row.language), {})[row.field] = row.text
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
    matched = best_match(language, stored)
    return dict(stored.get(matched, {})) if matched else {}


def key_of(entry) -> tuple[int, int]:
    """How a translation row points at an entry: content type and local id."""
    from django.contrib.contenttypes.models import ContentType

    return (ContentType.objects.get_for_model(entry.__class__).pk, entry.pk)


def overrides_by_entry(entries, language: str) -> dict[tuple[int, int], dict[str, str]]:
    """What each of these entries says in ``language``, in one query for all of them.

    A generic link has no join to ``select_related``, so a page rendering twenty entries
    would otherwise ask twenty times — the same cost `documents.archiving.copies_for`
    answers the same way, and for the same reason (#130).
    """
    from django.db.models import Q

    from .models import Translation

    wanted = normalise(language)
    entries = [entry for entry in entries if entry is not None and entry.pk]
    if not wanted or not entries:
        return {}

    by_type: dict[int, set[int]] = {}
    allowed: dict[int, set[str]] = {}
    for entry in entries:
        content_type, pk = key_of(entry)
        by_type.setdefault(content_type, set()).add(pk)
        allowed[content_type] = set(fields_for(entry))

    query = Q()
    for content_type, ids in by_type.items():
        query |= Q(content_type_id=content_type, object_id__in=ids)

    rows: dict[tuple[int, int], list] = {}
    for row in Translation.objects.filter(query):
        rows.setdefault((row.content_type_id, row.object_id), []).append(row)

    found: dict[tuple[int, int], dict[str, str]] = {}
    for key, group in rows.items():
        stored = _gather(group, allowed.get(key[0], set()))
        matched = best_match(wanted, stored)
        if matched:
            found[key] = dict(stored[matched])
    return found


def languages_of(entry) -> list[str]:
    """Every language this entry has been translated into, in order."""
    return sorted(stored_for(entry))


def languages_by_entry(entries) -> dict[tuple[int, int], list[str]]:
    """Which languages each of these entries has, in one query for all of them."""
    from django.db.models import Q

    from .models import Translation

    entries = [entry for entry in entries if entry is not None and entry.pk]
    if not entries:
        return {}

    by_type: dict[int, set[int]] = {}
    allowed: dict[int, set[str]] = {}
    for entry in entries:
        content_type, pk = key_of(entry)
        by_type.setdefault(content_type, set()).add(pk)
        allowed[content_type] = set(fields_for(entry))

    query = Q()
    for content_type, ids in by_type.items():
        query |= Q(content_type_id=content_type, object_id__in=ids)

    rows: dict[tuple[int, int], list] = {}
    for row in Translation.objects.filter(query):
        rows.setdefault((row.content_type_id, row.object_id), []).append(row)

    found = {key: sorted(_gather(group, allowed.get(key[0], set()))) for key, group in rows.items()}
    return {key: codes for key, codes in found.items() if codes}


class Translated:
    """One career entry, reading in a particular language.

    A wrapper rather than a copy of the model, so that a theme's template — including one
    from a plugin, which Postulo has never seen (#132) — reads ``entry.item.role`` and gets
    the translated title without knowing that translation exists. Anything not translated
    falls through to the entry itself, dates and links and proficiency levels included.
    """

    def __init__(self, entry, overrides: dict[str, str], language: str = "") -> None:
        # Straight into __dict__: __setattr__ is not overridden, but going through the
        # instance dictionary makes it plain that these three are the wrapper's own and
        # everything else is the entry's.
        self.__dict__["entry"] = entry
        self.__dict__["overrides"] = overrides
        self.__dict__["language"] = language

    def __getattr__(self, name: str):
        overrides = self.__dict__["overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["entry"], name)

    def __str__(self) -> str:
        return str(self.entry)

    def __eq__(self, other: object) -> bool:
        other = other.entry if isinstance(other, Translated) else other
        return self.entry == other

    def __hash__(self) -> int:
        return hash(self.entry)

    @property
    def highlight_lines(self) -> list[str]:
        """The bullets, translated if this language has them.

        Computed here rather than inherited, because the entry's own property reads its own
        ``highlights`` field and would return the original every time.
        """
        from .models import split_highlights

        return split_highlights(self.highlights)

    @property
    def skill_names(self) -> list[str]:
        """A skill group's skills, each in this language.

        The one place a CV prints an entry that is not the entry it asked for: a `Skill` is
        never on a CV in its own right, only through its group. Without this the interface
        would offer to translate a skill's name and then never print the translation, which
        is a worse promise than not offering it (#131).
        """
        entry = self.__dict__["entry"]
        skills = list(entry.skills.all())
        language = self.__dict__["language"]
        if not language:
            return [skill.name for skill in skills]
        found = overrides_by_entry(skills, language)
        return [found.get(key_of(skill), {}).get("name", skill.name) for skill in skills]


def in_language(entry, language: str, overrides: dict[str, str] | None = None):
    """This entry as it reads in ``language`` — the entry itself where nothing differs.

    ``overrides`` is for a caller that has already fetched them in bulk; without it this
    asks for one entry's own.
    """
    if entry is None or not language:
        return entry
    if overrides is None:
        overrides = overrides_for(entry, language)
    # Wrapped even when this entry itself has nothing translated, because a skill group with
    # an English heading may still hold skills that do, and the wrapper is what reaches them.
    return Translated(entry, overrides, language)


# ------------------------------------------------------------------- saying what fell back


@dataclass(frozen=True)
class FellBack:
    """One entry that had nothing to say in the language a document declares."""

    entry: object
    fields: tuple[str, ...]

    @property
    def label(self) -> str:
        return str(self.entry)

    @property
    def named(self) -> list[str]:
        """The fields in words, so the warning reads rather than lists column names."""
        return [field_label(self.entry, field) for field in self.fields]

    @property
    def section(self) -> str:
        """Which section of the career this entry is in, for the link to its editor."""
        from .registry import SECTIONS

        name = _model_name(self.entry)
        return next(
            (slug for slug, spec in SECTIONS.items() if spec.model._meta.model_name == name), ""
        )


def fields_that_fell_back(entry, language: str) -> tuple[str, ...]:
    """Which of this entry's translatable fields printed their original text.

    Only fields that have something to print: an empty ``summary`` did not fall back on
    anything, and listing it would bury the two that did.
    """
    if not language:
        return ()
    overrides = overrides_for(entry, language)
    return tuple(
        field
        for field in fields_for(entry)
        if str(getattr(entry, field, "") or "").strip() and field not in overrides
    )


def field_label(entry, field: str) -> str:
    """A field's name in words, taken from the model so it is translated already."""
    try:
        return str(entry._meta.get_field(field).verbose_name)
    except Exception:  # pragma: no cover - every name in TRANSLATABLE is a real field
        return field


# ---------------------------------------------------- which language the record is in


def record_language_of(user) -> str:
    """The language somebody's career record is written in, best answer first.

    What they said, then the language they read Postulo in, then the instance default —
    the same chain `documents.rendering.document_language` walks for a document, because
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
            return normalise(candidate)
    return ""


def fallen_back(cv) -> list[FellBack]:
    """Which of this CV's entries will print their original text, and in which fields.

    Empty where the CV is in the language the career record is written in — there is
    nothing to fall back *from* then, and a warning naming every entry on the page would
    be read once and ignored for ever after.
    """
    from postulo.documents.rendering import document_language

    language = normalise(document_language(cv))
    if not language or best_match(language, [record_language_of(cv.owner)]):
        return []

    entries = [item.item for item in cv.included_items().order_by("order", "pk")]
    entries = [entry for entry in entries if entry is not None]
    overrides = overrides_by_entry(entries, language)

    report: list[FellBack] = []
    for entry in entries:
        found = overrides.get(key_of(entry), {})
        missing = tuple(
            field
            for field in fields_for(entry)
            if str(getattr(entry, field, "") or "").strip() and field not in found
        )
        if missing:
            report.append(FellBack(entry=entry, fields=missing))
    return report
