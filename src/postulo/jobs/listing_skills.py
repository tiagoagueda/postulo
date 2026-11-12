"""Which of a listing's skills the person has, and which they do not (#1093).

Somebody reading a listing asks which of its requirements they meet. Postulo holds both
halves of the answer: the ESCO skills a person's career record names (`resume.Skill`
keeps the classification's identifier beside each name, #266) and the classification
itself. This lays them side by side, with no model and nothing fetched: the skills a
listing names are the runs of its words that are a skill's name (`esco.skills_named`), and
the person has one where a skill of theirs is that same skill.

Worked out when the page is drawn, from the listing's title and text, which are short; the
reading of the text is remembered per text by `esco.skills_named`, and what the person has
is one query. Where the skills have not been downloaded there is nothing to compare, and
the panel says how an administrator downloads them rather than saying the listing names
none.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Count, F

from postulo.core import languages
from postulo.resume import translatable
from postulo.resume.models import Skill

from . import esco


@dataclass
class SkillsPanel:
    """What the panel draws. ``available`` is false where ESCO's skills are not here."""

    available: bool
    #: The classification's names, in the reader's language, in the order the listing
    #: first names them.
    held: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: The CV that prints the most of what the person has of them, as its ``pk`` and
    #: ``name``, and how many it prints.
    best_cv: dict | None = None
    best_cv_count: int = 0

    @property
    def named(self) -> bool:
        return bool(self.held or self.missing)


def _text_of(posting) -> str:
    """What a listing says, as words: its title, and its description with no markup."""
    return f"{posting.title}\n{posting.description_text}"


def panel(posting, user) -> SkillsPanel:
    """The skills ``posting`` names, split into those ``user`` has and those they do not."""
    if not esco.skills_available():
        return SkillsPanel(available=False)
    reader = languages.current()
    named = esco.skills_named(_text_of(posting), translatable.record_language_of(user), reader)
    if not named:
        return SkillsPanel(available=True)
    theirs = set(
        Skill.objects.for_user(user).filter(esco_uri__in=named).values_list("esco_uri", flat=True)
    )
    found = SkillsPanel(available=True)
    for uri in named:
        name = esco.skill_name(uri, reader, strict=False)
        if name:
            (found.held if uri in theirs else found.missing).append(name)
    if theirs:
        found.best_cv, found.best_cv_count = _best_cv(user, theirs)
    return found


def _best_cv(user, uris: set[str]) -> tuple[dict | None, int]:
    """The CV of ``user``'s that prints the most of ``uris``, and how many it prints.

    Skills reach a CV in their groups, so a skill counts for a CV where its group is an
    entry of it and that entry is printed. The first by name among CVs that print as many;
    none where no CV prints any. One query, the CV's name and number with the count.
    """
    entries = "group__cv_entries"
    best = (
        Skill.objects.for_user(user)
        .filter(
            esco_uri__in=uris,
            **{f"{entries}__is_included": True, f"{entries}__cv__owner": user},
        )
        .values(cv=F(f"{entries}__cv"), cv_name=F(f"{entries}__cv__name"))
        .annotate(covered=Count("esco_uri", distinct=True))
        .order_by("-covered", "cv_name", "cv")
        .first()
    )
    if best is None:
        return None, 0
    return {"pk": best["cv"], "name": best["cv_name"]}, best["covered"]
