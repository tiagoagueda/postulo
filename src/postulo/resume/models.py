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

from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel


def split_highlights(text: str) -> list[str]:
    """Turn a highlights field into a list of bullets, ignoring blank lines."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class ResumeItem(OwnedModel):
    """Shared behaviour for anything that can appear on a CV."""

    order = models.PositiveIntegerField(
        _("order"), default=0, help_text=_("Lower numbers appear first.")
    )

    class Meta:
        abstract = True
        # Insertion order breaks ties, so a list typed top to bottom stays that way.
        ordering = ("order", "pk")

    @property
    def highlight_lines(self) -> list[str]:
        return split_highlights(getattr(self, "highlights", ""))


class Experience(ResumeItem):
    """A job you have held.

    ``organisation`` is free text rather than a link to
    :class:`~postulo.jobs.models.Company`. Where you have worked and where you are
    applying are unrelated lists, and joining them would put former employers into the
    company picker for new applications.
    """

    organisation = models.CharField(_("organisation"), max_length=200)
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
        ordering = ("-start_date", "order")

    def __str__(self) -> str:
        return f"{self.role} — {self.organisation}"

    @property
    def is_current(self) -> bool:
        return self.end_date is None


class Education(ResumeItem):
    """A qualification, finished or in progress."""

    institution = models.CharField(_("institution"), max_length=200)
    qualification = models.CharField(
        _("qualification"), max_length=200, help_text=_("For example, “BSc Computer Science”.")
    )
    field_of_study = models.CharField(_("field of study"), max_length=200, blank=True)
    location = models.CharField(_("location"), max_length=200, blank=True)
    start_date = models.DateField(_("from"), null=True, blank=True)
    end_date = models.DateField(_("until"), null=True, blank=True)
    grade = models.CharField(_("grade"), max_length=100, blank=True)
    highlights = models.TextField(_("highlights"), blank=True)

    class Meta(ResumeItem.Meta):
        verbose_name = _("education")
        verbose_name_plural = _("education")
        ordering = ("-end_date", "order")

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
        ordering = ("-issued_on", "order")

    def __str__(self) -> str:
        return self.name


class Proficiency(models.TextChoices):
    """The Common European Framework levels, plus the two ends people actually write."""

    A1 = "a1", _("A1 — beginner")
    A2 = "a2", _("A2 — elementary")
    B1 = "b1", _("B1 — intermediate")
    B2 = "b2", _("B2 — upper intermediate")
    C1 = "c1", _("C1 — advanced")
    C2 = "c2", _("C2 — proficient")
    NATIVE = "native", _("Native")


class LanguageSkill(ResumeItem):
    """A spoken language. Named to avoid colliding with Django's own Language."""

    name = models.CharField(_("language"), max_length=100)
    proficiency = models.CharField(
        _("proficiency"), max_length=10, choices=Proficiency, default=Proficiency.B2
    )

    class Meta(ResumeItem.Meta):
        verbose_name = _("language")
        verbose_name_plural = _("languages")

    def __str__(self) -> str:
        return f"{self.name} ({self.get_proficiency_display()})"


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
    kind = models.CharField(_("kind"), max_length=20, choices=LinkKind, default=LinkKind.PORTFOLIO)
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
