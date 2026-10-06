"""Your career, stored once.

This is the master copy: every role you have held, every qualification, every skill,
written down a single time. CV variants in :mod:`postulo.documents` draw on it rather
than duplicating it, so correcting a job title fixes it everywhere at once.

Highlights are stored as text, one bullet per line, rather than as rows in a separate
table. Rows would buy per-bullet reordering at the cost of a formset on every editing
screen, and would make a per-variant override a fiddly set of selections instead of a
textarea. One line per bullet is quicker to write, quicker to reorder, and trivial to
override for a particular CV.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core import languages
from postulo.core.language_field import LanguageField
from postulo.core.models import OwnedModel
from postulo.jobs import esco

from . import publications, translatable


def split_highlights(text: str) -> list[str]:
    """Turn a highlights field into a list of bullets, ignoring blank lines."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class ResumeItem(OwnedModel):
    """Shared behaviour for anything that can appear on a CV."""

    #: The person's own order for the section, dense and exactly what the overview shows:
    #: the arrows there swap an entry with its neighbour and renumber (`ordering.move`,
    #: #203). Hidden on the entry's form unless Settings > Appearance says otherwise.
    order = models.PositiveIntegerField(
        _("order"), default=0, help_text=_("Lower numbers appear first.")
    )

    #: What this entry says in other languages, and the cascade that goes with it: an entry
    #: deleted takes its translations, because a translation of nothing is nothing (#131).
    #:
    #: Declared on the abstract base so every kind gets one, `Certification` included --
    #: which translates nothing, and whose relation is therefore always empty. One rule that
    #: is sometimes vacuous beats seven declarations with one missing.
    translations = GenericRelation("resume.Translation")

    #: The places this entry is on a CV. Deleting the entry takes them: a CV entry pointing at
    #: nothing cannot be printed, and the person could not reach it to remove it (#381).
    cv_entries = GenericRelation("documents.CVItem")

    class Meta:
        abstract = True
        # Insertion order breaks ties, so a list typed top to bottom stays that way.
        ordering = ("order", "pk")

    @property
    def highlight_lines(self) -> list[str]:
        return split_highlights(getattr(self, "highlights", ""))


class Experience(ResumeItem):
    """A job you have held.

    ``organisation`` is the name the entry gives its employer, as text of its own: a CV
    states the name an employer had when the person worked there, and companies are
    renamed and merged. ``company`` links it to a :class:`~postulo.jobs.models.Company` of
    the person's, found by that name when the entry is saved (#683). A company the career
    added is marked ``from_career`` and kept out of the places a company is picked for new
    work, so former employers do not crowd the posting picker. Deleting the company keeps
    the entry and its text.
    """

    organisation = models.CharField(_("organisation"), max_length=200)
    company = models.ForeignKey(
        "jobs.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="career_entries",
        verbose_name=_("company"),
    )
    role = models.CharField(_("role"), max_length=200)
    location = models.CharField(_("location"), max_length=200, blank=True)
    start_date = models.DateField(_("from"))
    end_date = models.DateField(
        _("until"), null=True, blank=True, help_text=_("Leave empty if this is your current role.")
    )
    summary = models.TextField(_("summary"), blank=True)
    highlights = models.TextField(
        _("highlights"), blank=True, help_text=_("One achievement per line.")
    )

    class Meta(ResumeItem.Meta):
        verbose_name = _("experience")
        verbose_name_plural = _("experience")
        # The number, not the date, since #203: seeded from the dates once, and the person's
        # from then on. A new entry still lands where its date puts it (`ordering.place_new`).
        ordering = ("order", "pk")

    def __str__(self) -> str:
        return f"{self.role} — {self.organisation}"

    @property
    def is_current(self) -> bool:
        return self.end_date is None


class EqfLevel(models.IntegerChoices):
    """The eight levels of the European Qualifications Framework (#684).

    Stated by the person, from a diploma or its supplement, and never worked out from the
    qualification's name, as a language's level is not (#235). Unset prints nothing.
    """

    __empty__ = _("Not stated")

    L1 = 1, _("Level 1")
    L2 = 2, _("Level 2")
    L3 = 3, _("Level 3")
    L4 = 4, _("Level 4")
    L5 = 5, _("Level 5")
    L6 = 6, _("Level 6")
    L7 = 7, _("Level 7")
    L8 = 8, _("Level 8")


class Education(ResumeItem):
    """A qualification, finished or in progress.

    ``institution`` is the name the entry gives its school, as text of its own, which every
    CV and file prints unchanged and which stays required: self-taught study is written as
    text and never forced into a company. ``company`` links it to a
    :class:`~postulo.jobs.models.Company` of the person's, found by that name when the entry
    is saved, as an experience's is (#685). Deleting the company keeps the entry and its text.
    """

    institution = models.CharField(_("institution"), max_length=200)
    company = models.ForeignKey(
        "jobs.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="education_entries",
        verbose_name=_("company"),
    )
    qualification = models.CharField(
        _("qualification"), max_length=200, help_text=_("For example, “BSc Computer Science”.")
    )
    field_of_study = models.CharField(_("field of study"), max_length=200, blank=True)
    location = models.CharField(_("location"), max_length=200, blank=True)
    start_date = models.DateField(_("from"), null=True, blank=True)
    end_date = models.DateField(_("until"), null=True, blank=True)
    grade = models.CharField(_("grade"), max_length=100, blank=True)
    eqf_level = models.PositiveSmallIntegerField(
        _("EQF level"), choices=EqfLevel, null=True, blank=True
    )
    highlights = models.TextField(_("highlights"), blank=True)

    class Meta(ResumeItem.Meta):
        verbose_name = _("education")
        verbose_name_plural = _("education")
        ordering = ("order", "pk")  # as Experience: the person's, since #203

    def __str__(self) -> str:
        return f"{self.qualification} — {self.institution}"


class SkillGroup(ResumeItem):
    """A heading a set of skills sits under, such as “Languages” or “Infrastructure”."""

    name = models.CharField(_("name"), max_length=100)

    class Meta(ResumeItem.Meta):
        verbose_name = _("skill group")
        verbose_name_plural = _("skill groups")

    def __str__(self) -> str:
        return self.name

    @property
    def skill_names(self) -> list[str]:
        return [skill.name for skill in self.skills.all()]


class Skill(ResumeItem):
    name = models.CharField(_("name"), max_length=100)
    #: The ESCO skill the name is, as the classification's own identifier -- a URI, since a
    #: skill has no code the way an occupation's unit group has -- or empty where the name
    #: is none of them. It follows the name rather than being set beside it, so the two
    #: cannot disagree, and a skill that matches nothing is not a lesser kind of skill
    #: (#266). Never read from a form or a file: `save` works it out, and so does anything
    #: that writes skills without calling it.
    esco_uri = models.CharField(_("ESCO skill"), max_length=200, blank=True, editable=False)
    group = models.ForeignKey(
        SkillGroup,
        on_delete=models.CASCADE,
        related_name="skills",
        null=True,
        blank=True,
        verbose_name=_("group"),
    )

    class Meta(ResumeItem.Meta):
        verbose_name = _("skill")
        verbose_name_plural = _("skills")

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        update = kwargs.get("update_fields")
        if update is None or "name" in update:
            # Derived whenever the name may be written, and carried along then, whatever
            # else the save meant to write -- the rule a posting's code follows.
            self.match()
            if update is not None and "esco_uri" not in update:
                kwargs["update_fields"] = [*update, "esco_uri"]
        return super().save(*args, **kwargs)

    def match(self) -> str:
        """Work out which ESCO skill the name is, keep it, and say it.

        Tried in the language the career record is written in, then in the one its owner
        is reading, then in English: the record's first, because that is the language the
        name was written in, and the reader's because that is the language the skill box
        offered names in.
        """
        record = [translatable.record_language_of(self.owner)] if self.owner_id else []
        self.esco_uri = esco.skill_for(self.name, *record, languages.current())
        return self.esco_uri

    @property
    def esco_name(self) -> str:
        """What the classification calls this skill, in the language being read.

        The English name where the classification publishes none in that language: this is
        for the person editing their record, who is being told what the name was taken for.
        """
        return esco.skill_name(self.esco_uri, strict=False) if self.esco_uri else ""


class Project(ResumeItem):
    name = models.CharField(_("name"), max_length=200)
    role = models.CharField(_("your role"), max_length=200, blank=True)
    url = models.URLField(_("link"), blank=True)
    start_date = models.DateField(_("from"), null=True, blank=True)
    end_date = models.DateField(_("until"), null=True, blank=True)
    summary = models.TextField(_("summary"), blank=True)
    highlights = models.TextField(_("highlights"), blank=True)

    class Meta(ResumeItem.Meta):
        verbose_name = _("project")
        verbose_name_plural = _("projects")

    def __str__(self) -> str:
        return self.name


class Certification(ResumeItem):
    name = models.CharField(_("name"), max_length=200)
    issuer = models.CharField(_("issued by"), max_length=200, blank=True)
    issued_on = models.DateField(_("issued on"), null=True, blank=True)
    expires_on = models.DateField(_("expires on"), null=True, blank=True)
    credential_url = models.URLField(_("credential link"), blank=True)

    class Meta(ResumeItem.Meta):
        verbose_name = _("certification")
        verbose_name_plural = _("certifications")
        ordering = ("order", "pk")  # as Experience: the person's, since #203

    def __str__(self) -> str:
        return self.name


class Publication(ResumeItem):
    """A paper, a book, a chapter, a thesis, a dataset: shaped like a BibTeX entry (#687).

    One table with a type, as BibTeX has it. The type decides which fields the form offers
    (`publications.TYPES` is the table) and never which are kept: a value in a field the
    type does not use stays where it is, so a type changed and changed back loses nothing.
    Names are text as typed, one per line; splitting a name into its parts is the job of
    whatever reads or writes a ``.bib`` file, where a wrong guess costs more than the typed
    form does.

    It translates nothing, as a `Certification` does: a paper's title is the paper.
    """

    entry_type = models.CharField(
        _("type"),
        max_length=20,
        choices=publications.CHOICES,
        default=publications.DEFAULT_TYPE,
    )
    title = models.CharField(_("title"), max_length=500)
    authors = models.TextField(_("authors"), blank=True, max_length=2000)
    editors = models.TextField(_("editors"), blank=True, max_length=2000)
    container_title = models.CharField(_("published in"), max_length=300, blank=True)
    publisher = models.CharField(_("publisher"), max_length=200, blank=True)
    institution = models.CharField(_("institution"), max_length=200, blank=True)
    location = models.CharField(_("place"), max_length=200, blank=True)
    date = models.CharField(
        _("date"),
        max_length=10,
        blank=True,
        validators=[publications.validate_date],
    )
    volume = models.CharField(_("volume"), max_length=40, blank=True)
    number = models.CharField(_("number"), max_length=40, blank=True)
    pages = models.CharField(_("pages"), max_length=40, blank=True)
    edition = models.CharField(_("edition"), max_length=40, blank=True)
    series = models.CharField(_("series"), max_length=200, blank=True)
    chapter = models.CharField(_("chapter"), max_length=40, blank=True)
    doi = models.CharField(
        _("DOI"),
        max_length=publications.DOI_MAX_LENGTH,
        blank=True,
        validators=[publications.validate_doi],
    )
    url = models.URLField(_("link"), max_length=500, blank=True)
    note = models.CharField(_("note"), max_length=500, blank=True)
    language = LanguageField(_("language"), blank=True)
    cite_key = models.CharField(
        _("citation key"),
        max_length=publications.CITE_KEY_MAX_LENGTH,
        blank=True,
        validators=[publications.validate_cite_key],
    )

    class Meta(ResumeItem.Meta):
        verbose_name = _("publication")
        verbose_name_plural = _("publications")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "cite_key"),
                condition=~models.Q(cite_key=""),
                name="resume_one_cite_key_per_owner",
            )
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        self.doi = publications.normalise_doi(self.doi)
        if not self.cite_key:
            self.cite_key = self.free_cite_key()
        super().save(*args, **kwargs)

    def free_cite_key(self, taken=None) -> str:
        """A key made from the entry, one nobody of this person's holds."""
        if taken is None:
            held = type(self).objects.for_user(self.owner).exclude(pk=self.pk)
            taken = set(held.exclude(cite_key="").values_list("cite_key", flat=True))
        return publications.free_key(
            publications.suggest_key(self.authors, self.editors, self.date, self.title), taken
        )

    @property
    def citation(self) -> str:
        """The one neutral line a CV prints, in the language it is being drawn in."""
        return publications.citation(self)

    @property
    def citation_text(self) -> str:
        """The line without the DOI's address, which a page links."""
        return publications.citation_parts(self)[0]

    @property
    def doi_url(self) -> str:
        return publications.doi_url(self.doi)


class Proficiency(models.TextChoices):
    """The Common European Framework levels, plus the two ends people actually write.

    And one more: **not stated**. A level is a claim about yourself that somebody will test
    in an interview, so there has to be a way of not making one -- a Europass file with no
    CEFR level in it used to import as B1, which is a claim Postulo invented on the person's
    behalf and put on their CV (#235). An unset level prints nothing at all.
    """

    UNSET = "", _("Not stated")
    A1 = "a1", _("A1 — beginner")
    A2 = "a2", _("A2 — elementary")
    B1 = "b1", _("B1 — intermediate")
    B2 = "b2", _("B2 — upper intermediate")
    C1 = "c1", _("C1 — advanced")
    C2 = "c2", _("C2 — proficient")
    NATIVE = "native", _("Native")


class LanguageSkill(ResumeItem):
    """A spoken language. Named to avoid colliding with Django's own Language.

    **Chosen by code where the language is on the list** (#689): `code` is a BCP 47 tag, and
    what a CV prints is the language's name in the CV's own language, from CLDR through
    `core.language_names` -- *Inglês* on a Portuguese one, *Anglais* on a French one -- with
    nobody typing either. `name` stays beside it, filled in the record's language, so that
    anything that wants a plain name (an older reader, a theme from a plugin) has one. A
    language that is not on the list has no code and is the name it was typed as.
    """

    name = models.CharField(_("language"), max_length=100)
    code = LanguageField(_("language code"), blank=True)
    proficiency = models.CharField(
        _("proficiency"),
        max_length=10,
        choices=Proficiency,
        default=Proficiency.B2,
        # Blank is a real answer here, so the form must accept it. The default is untouched:
        # somebody adding a language by hand is saying something about themselves, and B2 is
        # still the likeliest thing they mean to say.
        blank=True,
    )

    class Meta(ResumeItem.Meta):
        verbose_name = _("language")
        verbose_name_plural = _("languages")

    def save(self, *args, **kwargs):
        if self.code and not self.name.strip():
            from postulo.core import language_names

            record = translatable.record_language_of(self.owner) if self.owner_id else ""
            self.name = language_names.name(self.code, record)[:100]
            update = kwargs.get("update_fields")
            if update is not None and "name" not in update:
                kwargs["update_fields"] = [*update, "name"]
        return super().save(*args, **kwargs)

    @property
    def shown_name(self) -> str:
        """The name in the language being read: the table's for a coded entry, else as typed."""
        if not self.code:
            return self.name
        from postulo.core import language_names

        return language_names.name(self.code, languages.current()) or self.name

    def __str__(self) -> str:
        return f"{self.shown_name} ({self.get_proficiency_display()})"


class LinkKind(models.TextChoices):
    """What a link points at, which is enough for a reader to know whether to click."""

    PORTFOLIO = "portfolio", _("Portfolio")
    SITE = "site", _("Personal site")
    CODE = "code", _("Code (GitHub, GitLab…)")
    DESIGN = "design", _("Design (Behance, Dribbble…)")
    PUBLICATION = "publication", _("Publication")
    VIDEO = "video", _("Video")
    OTHER = "other", _("Other")


class LinkStatus(models.TextChoices):
    UNCHECKED = "", _("Not checked")
    OK = "ok", _("Answered")
    BROKEN = "broken", _("Did not answer")


class Link(ResumeItem):
    """Somewhere your work already lives: a portfolio, a profile, a video.

    A portfolio is mostly an address, and a video CV almost always is one — an unlisted
    upload somewhere, not a file to hand over. Both belong on the CV as a *Links* section
    and both can be sent with an application, which is what this is.

    Postulo never fetches a link on its own. *Check* asks, once, whether the address still
    answers, and records what it found: a portfolio that 404s on the day the recruiter
    clicks is the worst possible outcome, and the only thing worse is a job tracker
    quietly making requests nobody asked for.
    """

    title = models.CharField(_("title"), max_length=200)
    url = models.URLField(_("address"), max_length=500)
    kind = models.CharField(_("type"), max_length=20, choices=LinkKind, default=LinkKind.PORTFOLIO)
    description = models.CharField(
        _("description"),
        max_length=250,
        blank=True,
        help_text=_("One line, for whoever is reading the CV."),
    )

    checked_at = models.DateTimeField(_("last checked"), null=True, blank=True)
    check_status = models.CharField(
        _("last check"), max_length=10, choices=LinkStatus, blank=True, default=""
    )
    check_detail = models.CharField(_("what the check found"), max_length=250, blank=True)

    class Meta(ResumeItem.Meta):
        verbose_name = _("link")
        verbose_name_plural = _("links")

    def __str__(self) -> str:
        return self.title

    @property
    def host(self) -> str:
        from urllib.parse import urlsplit

        return (urlsplit(self.url).hostname or "").removeprefix("www.")

    @property
    def is_broken(self) -> bool:
        return self.check_status == LinkStatus.BROKEN


class Translation(OwnedModel):
    """What one field of one career entry says in one other language.

    A row per field rather than a row per entry, and a row per entry rather than a column
    per field, for reasons that pull in opposite directions and settle here. Columns per
    field would need a migration for every field anybody ever wants to say differently, and
    a table with the union of every model's translatable fields, most of them null on most
    rows. A JSON map on the entry would need neither, and would also accept a field that
    does not exist, a language that is not a language, and two spellings of the same key.

    So: a generic link, exactly as `CVItem` and `PostalAddress` already use, with the field
    name checked against `translatable.TRANSLATABLE` when it is set. The constraint is what
    a JSON map could not have — one text per field per language per entry, in the database
    rather than in whoever wrote the last save (#131).

    **Blank is withdrawal, not emptiness.** A translation somebody cleared renders as the
    original, so `translatable.stored_for` drops blank rows. The row is left alone rather
    than deleted, because the alternative is a form that silently removes what it was given.
    """

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+", verbose_name=_("type of entry")
    )
    object_id = models.PositiveIntegerField()
    entry = GenericForeignKey("content_type", "object_id")

    language = LanguageField(_("language"))
    field = models.CharField(_("field"), max_length=translatable.MAX_FIELD_LENGTH)
    text = models.TextField(_("text"), blank=True)

    class Meta:
        verbose_name = _("translation")
        verbose_name_plural = _("translations")
        ordering = ("language", "field")
        constraints = [
            models.UniqueConstraint(
                fields=("content_type", "object_id", "language", "field"),
                name="resume_one_text_per_field_per_language",
            )
        ]
        indexes = [models.Index(fields=("content_type", "object_id", "language"))]

    def __str__(self) -> str:
        return f"{self.field} ({self.language})"
