"""A career entry that says the same thing in more than one language.

> also multiple language documents cohabiting should be normal

At the document level this was already true: `CV.language` and `CoverLetter.language` each
declare what the document is written in, and `document_direction()` lays the page out for
that rather than for whoever is reading Postulo. One level down it was not. The career
record held one text per field per entry, so a CV declaring ``fr-FR`` printed exactly the
same English job titles as one declaring ``en-GB``, and the honest way to keep a CV in two
languages was to keep two careers — a second set of entries, typed again, drifting apart
from the first the moment a date changed (#131).

**A second language is a translation, not a second record.** One `Translation` row per
entry, per language, per field, and the entry is otherwise untouched: a date corrected on
the master copy is corrected in every language at once, which is the whole point of there
being a master copy.

**Which fields may be translated is a decision per field, not a mechanism applied
uniformly**, and `TRANSLATABLE` in `translatable` is that decision with the reasoning
attached. A
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

from postulo.core import language_names, languages
from postulo.jobs import esco

# The rules live in `translatable`, below the models and the forms that declare and offer
# them (#248). Handed out here as well, under the names every caller has always used.
from .translatable import (  # noqa: F401 - re-exported: views, candidate, rendering, tests
    MAX_FIELD_LENGTH,
    TRANSLATABLE,
    _gather,
    _model_name,
    fields_for,
    overrides_for,
    record_language_of,
    stored_for,
)

# --------------------------------------------------------- what an entry says, in a language


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

    wanted = languages.tag(language)
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
        matched = languages.match(wanted, stored)
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

    def __init__(
        self, entry, overrides: dict[str, str], language: str = "", skills: tuple | None = None
    ) -> None:
        # Straight into __dict__: __setattr__ is not overridden, but going through the
        # instance dictionary makes it plain that these three are the wrapper's own and
        # everything else is the entry's.
        self.__dict__["entry"] = entry
        self.__dict__["overrides"] = overrides
        self.__dict__["language"] = language
        # What `skills_in` found for every group on the document, so one group does not ask.
        self.__dict__["lookup"] = skills

    def __getattr__(self, name: str):
        overrides = self.__dict__["overrides"]
        if name in overrides:
            return overrides[name]
        entry = self.__dict__["entry"]
        if name == "name" and self.__dict__["language"] and getattr(entry, "code", ""):
            # A language chosen by its code is named in the language of the document, the
            # person's own translation above having been asked first (#689).
            return language_names.name(entry.code, self.__dict__["language"]) or entry.name
        return getattr(entry, name)

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

        Three answers, in this order: the person's own translation; the name the ESCO
        classification gives the skill in this language, where the name is one of its
        skills (`classified_names` says when); the name as it was written (#266).
        """
        entry = self.__dict__["entry"]
        skills = list(entry.skills.all())
        language = self.__dict__["language"]
        if not language:
            return [skill.name for skill in skills]
        found, classified = self.__dict__["lookup"] or skills_in([entry], language, entry.owner)
        return [
            found.get(key_of(skill), {}).get("name") or classified.get(skill.pk) or skill.name
            for skill in skills
        ]


def skills_in(groups, language: str, owner) -> tuple[dict, dict[int, str]]:
    """The translated names and the classified names of every skill in these groups.

    Two queries at most, however many groups there are: what a document asks once so that
    each group need not (#559).
    """
    skills = [skill for group in groups if group is not None for skill in group.skills.all()]
    return overrides_by_entry(skills, language), classified_names(skills, language, owner)


def classified_names(skills, language: str, owner=None) -> dict[int, str]:
    """What a CV in ``language`` may print for these skills, from the ESCO classification.

    ``{skill id: name}``, for the skills whose name is one of the classification's and
    which it names in that language. **Only ever a fallback**, and a narrow one, because
    somebody reads the CV that comes out and did not write this line (#266):

    - the person's own translation wins wherever there is one -- the caller asks for those
      first, and a name from here is never printed over one;
    - never in the language the career record is written in, where the name as it was
      written is the answer, exactly as it is for everything else on the CV;
    - never English standing in for a language the classification does not publish: a
      CV in Turkish prints the name as written, not the classification's English;
    - only for a name that *is* a skill's preferred name somewhere -- which is all that
      `Skill.match` keeps -- so what is printed is the published name of the skill the
      person named, never a guess at what they meant.

    The first letter follows the person's own: *Project management* prints as *Gestion de
    projets*, where the classification writes its names in lower case. Every name this
    answers with is listed on the CV's page before the CV is exported
    (`named_by_classification`), and on the skill's page in that language.
    """
    wanted = languages.tag(language)
    skills = [skill for skill in skills if getattr(skill, "esco_uri", "")]
    if not wanted or not skills:
        return {}
    owner = owner if owner is not None else skills[0].owner
    if languages.match(wanted, [record_language_of(owner)]):
        return {}
    named: dict[int, str] = {}
    for skill in skills:
        name = esco.skill_name(skill.esco_uri, wanted, strict=True)
        if name:
            named[skill.pk] = as_they_write_it(skill.name, name)
    return named


def as_they_write_it(own: str, classified: str) -> str:
    """The classification's name, with a capital first letter where the person used one."""
    own, classified = (own or "").strip(), classified or ""
    if own[:1].isupper() and classified[:1].islower():
        return classified[:1].upper() + classified[1:]
    return classified


def named_by_classification(cv) -> list[tuple[object, str]]:
    """The skills this CV prints under the classification's name, and that name.

    What the CV's page says before it is exported, as it says what fell back: a line the
    person did not write is theirs to know about, and to replace with their own.
    """
    from postulo.documents.models import document_language

    language = document_language(cv)
    groups = [
        item.item
        for item in cv.included_items().order_by("order", "pk")
        if item.content_type.model == "skillgroup"
    ]
    skills = [skill for group in groups if group is not None for skill in group.skills.all()]
    classified = classified_names(skills, language, cv.owner)
    if not classified:
        return []
    own = overrides_by_entry(skills, language)
    return [
        (skill, classified[skill.pk])
        for skill in skills
        if skill.pk in classified and not own.get(key_of(skill), {}).get("name")
    ]


def in_language(
    entry, language: str, overrides: dict[str, str] | None = None, skills: tuple | None = None
):
    """This entry as it reads in ``language`` — the entry itself where nothing differs.

    ``overrides`` is for a caller that has already fetched them in bulk; without it this
    asks for one entry's own. ``skills`` is `skills_in`'s answer, likewise.
    """
    if entry is None or not language:
        return entry
    if overrides is None:
        overrides = overrides_for(entry, language)
    # Wrapped even when this entry itself has nothing translated, because a skill group with
    # an English heading may still hold skills that do, and the wrapper is what reaches them.
    return Translated(entry, overrides, language, skills)


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
        if str(getattr(entry, field, "") or "").strip()
        and field not in overrides
        and not named_by_code(entry, field)
    )


def named_by_code(entry, field: str) -> bool:
    """Whether this field is printed from the language's code, which has a name in every
    language and so never falls back (#689)."""
    return field == "name" and bool(getattr(entry, "code", ""))


def field_label(entry, field: str) -> str:
    """A field's name in words, taken from the model so it is translated already."""
    try:
        return str(entry._meta.get_field(field).verbose_name)
    except Exception:  # pragma: no cover - every name in TRANSLATABLE is a real field
        return field


def fallen_back(cv) -> list[FellBack]:
    """Which of this CV's entries will print their original text, and in which fields.

    Empty where the CV is in the language the career record is written in — there is
    nothing to fall back *from* then, and a warning naming every entry on the page would
    be read once and ignored for ever after.
    """
    from postulo.documents.models import document_language

    language = document_language(cv)
    if not language or languages.match(language, [record_language_of(cv.owner)]):
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
            if str(getattr(entry, field, "") or "").strip()
            and field not in found
            and not named_by_code(entry, field)
        )
        if missing:
            report.append(FellBack(entry=entry, fields=missing))
    return report
