"""Companies, the people inside them, and the postings they advertise.

A posting is a fact about the world: it exists whether or not you do anything about it.
What you do about it is an :class:`~postulo.applications.models.Application`. Keeping
them apart means a posting you decided against still leaves a record, and re-applying to
the same role a year later does not overwrite the first attempt.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel


class Company(OwnedModel):
    """An employer, as recorded by one applicant.

    Companies are owner-scoped rather than shared. Two people using the same instance
    each keep their own notes on the same employer, and neither can see the other's
    opinion of them.
    """

    name = models.CharField(_("name"), max_length=200)
    website = models.URLField(_("website"), blank=True)
    careers_url = models.URLField(
        _("careers page"), blank=True, help_text=_("Where this company lists its openings.")
    )
    location = models.CharField(_("location"), max_length=200, blank=True)
    industry = models.CharField(_("industry"), max_length=120, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("company")
        verbose_name_plural = _("companies")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("owner", "name"), name="unique_company_name_per_owner")
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("jobs:company_detail", args=[self.pk])


class Contact(OwnedModel):
    """Someone at a company: a recruiter, a hiring manager, a friend on the inside."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="contacts",
        null=True,
        blank=True,
        verbose_name=_("company"),
    )
    name = models.CharField(_("name"), max_length=200)
    role = models.CharField(_("role"), max_length=200, blank=True)
    email = models.EmailField(_("email address"), blank=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    linkedin_url = models.URLField(_("LinkedIn"), blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("contact")
        verbose_name_plural = _("contacts")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class RemoteType(models.TextChoices):
    ONSITE = "onsite", _("On site")
    HYBRID = "hybrid", _("Hybrid")
    REMOTE = "remote", _("Remote")


class EmploymentType(models.TextChoices):
    FULL_TIME = "full_time", _("Full time")
    PART_TIME = "part_time", _("Part time")
    CONTRACT = "contract", _("Contract")
    FREELANCE = "freelance", _("Freelance")
    INTERNSHIP = "internship", _("Internship")
    APPRENTICESHIP = "apprenticeship", _("Apprenticeship")


class SalaryPeriod(models.TextChoices):
    YEAR = "year", _("Per year")
    MONTH = "month", _("Per month")
    DAY = "day", _("Per day")
    HOUR = "hour", _("Per hour")


class ListingState(models.TextChoices):
    """Where a listing stands before anyone applies to it.

    Two more states exist but are never stored, because they follow from other facts:
    *applied*, when an application exists, and *closed*, when the opening is gone or its
    closing date has passed. See :attr:`JobPosting.derived_state`.
    """

    NEW = "new", _("New")
    SHORTLISTED = "shortlisted", _("Shortlisted")
    DISCARDED = "discarded", _("Discarded")


class DiscardReason(models.TextChoices):
    NOT_FOR_ME = "not_for_me", _("Not for me")
    PAY = "pay", _("The pay")
    LOCATION = "location", _("The location")
    CLOSED = "closed", _("Closed or already filled")
    OTHER = "other", _("Other")


#: The stored states a listing can be filtered by, plus the two derived ones.
LISTING_FILTERS = (
    ListingState.NEW,
    ListingState.SHORTLISTED,
    ListingState.DISCARDED,
    "applied",
    "closed",
)


class JobPostingQuerySet(models.QuerySet):
    def for_user(self, user) -> JobPostingQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def open(self) -> JobPostingQuerySet:
        return self.filter(closed_at__isnull=True)

    def with_application_count(self) -> JobPostingQuerySet:
        return self.annotate(application_count=models.Count("applications", distinct=True))

    def undecided(self) -> JobPostingQuerySet:
        """New or shortlisted, not applied to, and still open: what the person must decide."""
        return (
            self.with_application_count()
            .filter(
                state__in=(ListingState.NEW, ListingState.SHORTLISTED),
                application_count=0,
                closed_at__isnull=True,
            )
            .exclude(closes_at__lt=timezone.localdate())
        )

    def closing_soon(self, days: int = 7) -> JobPostingQuerySet:
        today = timezone.localdate()
        return self.undecided().filter(
            closes_at__gte=today, closes_at__lte=today + timedelta(days=days)
        )

    def in_state(self, state: str) -> JobPostingQuerySet:
        """Filter by a stored state or one of the two derived ones."""
        annotated = self.with_application_count()
        if state == "applied":
            return annotated.filter(application_count__gt=0)
        if state == "closed":
            return annotated.filter(application_count=0).filter(
                models.Q(closed_at__isnull=False) | models.Q(closes_at__lt=timezone.localdate())
            )
        if state in ListingState.values:
            return annotated.filter(
                state=state, application_count=0, closed_at__isnull=True
            ).exclude(closes_at__lt=timezone.localdate())
        return annotated


class JobPosting(OwnedModel):
    """A specific opening at a company — a listing, until the person decides about it.

    Every posting arrives here first, captured or typed, and waits: new, then shortlisted
    or discarded by the person, and *applied* the moment an application exists. That last
    state is never stored; it is read from the applications, so it can never disagree
    with them.
    """

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="postings", verbose_name=_("company")
    )
    title = models.CharField(_("title"), max_length=250)
    location = models.CharField(_("location"), max_length=200, blank=True)
    remote_type = models.CharField(
        _("working arrangement"), max_length=20, choices=RemoteType, blank=True
    )
    employment_type = models.CharField(
        _("employment type"), max_length=20, choices=EmploymentType, blank=True
    )

    url = models.URLField(_("posting URL"), blank=True, max_length=500)
    source = models.CharField(
        _("found via"),
        max_length=120,
        blank=True,
        help_text=_("Where you came across it: a job board, a referral, the company site."),
    )
    description = models.TextField(_("description"), blank=True)

    salary_min = models.DecimalField(
        _("salary from"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    salary_max = models.DecimalField(
        _("salary to"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    salary_currency = models.CharField(_("currency"), max_length=3, blank=True, default="EUR")
    salary_period = models.CharField(
        _("salary period"),
        max_length=10,
        choices=SalaryPeriod,
        blank=True,
        default=SalaryPeriod.YEAR,
    )

    posted_at = models.DateField(_("posted on"), null=True, blank=True)
    closes_at = models.DateField(
        _("closing date"),
        null=True,
        blank=True,
        help_text=_("The application deadline, if stated."),
    )
    closed_at = models.DateTimeField(
        _("closed on"),
        null=True,
        blank=True,
        help_text=_("Set when the opening is no longer available."),
    )

    state = models.CharField(
        _("state"), max_length=20, choices=ListingState, default=ListingState.NEW
    )
    discard_reason = models.CharField(
        _("why it was discarded"), max_length=20, choices=DiscardReason, blank=True
    )
    noted_at = models.DateTimeField(
        _("noted on"), default=timezone.now, help_text=_("When it entered your listings.")
    )
    decided_at = models.DateTimeField(_("decided on"), null=True, blank=True)

    objects = JobPostingQuerySet.as_manager()

    class Meta:
        verbose_name = _("job posting")
        verbose_name_plural = _("job postings")
        ordering = ("-noted_at", "-created_at")
        indexes = [models.Index(fields=("owner", "state"))]

    def __str__(self) -> str:
        return f"{self.title} — {self.company.name}"

    def get_absolute_url(self) -> str:
        return reverse("jobs:posting_detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def is_past_closing(self) -> bool:
        return self.closes_at is not None and self.closes_at < timezone.localdate()

    @property
    def has_applications(self) -> bool:
        count = getattr(self, "application_count", None)
        if count is None:
            count = self.applications.count()
        return count > 0

    @property
    def derived_state(self) -> str:
        """What the listing is, all things considered: the stored state, or one it earned."""
        if self.has_applications:
            return "applied"
        if self.state == ListingState.DISCARDED:
            return ListingState.DISCARDED
        if not self.is_open or self.is_past_closing:
            return "closed"
        return self.state

    @property
    def derived_state_label(self) -> str:
        labels = {**dict(ListingState.choices), "applied": _("Applied"), "closed": _("Closed")}
        return str(labels[self.derived_state])

    @property
    def is_undecided(self) -> bool:
        return self.derived_state in (ListingState.NEW, ListingState.SHORTLISTED)

    def shortlist(self) -> None:
        self.state = ListingState.SHORTLISTED
        self.discard_reason = ""
        self.decided_at = timezone.now()
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])

    def discard(self, reason: str = "") -> None:
        self.state = ListingState.DISCARDED
        self.discard_reason = reason
        self.decided_at = timezone.now()
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])

    def restore(self) -> None:
        """Back to new, as if the decision had not been made."""
        self.state = ListingState.NEW
        self.discard_reason = ""
        self.decided_at = None
        self.save(update_fields=["state", "discard_reason", "decided_at", "updated_at"])

    def close(self) -> None:
        if self.closed_at is None:
            self.closed_at = timezone.now()
            self.save(update_fields=["closed_at", "updated_at"])

    @property
    def salary_display(self) -> str:
        """A readable salary range, or an empty string when nothing is known.

        Grouping is left to Django's locale machinery rather than hard-coded, so a
        French reader sees 65 000 where a British one sees 65,000.
        """
        if self.salary_min is None and self.salary_max is None:
            return ""

        def amount(value) -> str:
            return number_format(value, decimal_pos=0, use_l10n=True, force_grouping=True)

        if self.salary_min is not None and self.salary_max is not None:
            figure = _("%(low)s to %(high)s") % {
                "low": amount(self.salary_min),
                "high": amount(self.salary_max),
            }
        elif self.salary_min is not None:
            figure = _("from %(low)s") % {"low": amount(self.salary_min)}
        else:
            figure = _("up to %(high)s") % {"high": amount(self.salary_max)}

        return f"{figure} {self.salary_currency}".strip()


class CaptureStatus(models.TextChoices):
    PENDING = "pending", _("Waiting for review")
    ACCEPTED = "accepted", _("Turned into an application")
    DISCARDED = "discarded", _("Discarded")


class Capture(OwnedModel):
    """A posting read off a page, waiting for a person to confirm it.

    A capture is never an application. Parsing somebody else's markup is guesswork often
    enough that turning the result straight into a record would put invented job titles
    into the one place they must not be. Everything captured waits here until it has been
    looked at.

    The parsed fields are kept as JSON rather than as columns because they are not the
    record — they are a suggestion for a form, and the shape belongs to
    :class:`~postulo.plugins.base.JobPostingData`, which validates them on the way in and
    on the way out.
    """

    url = models.URLField(_("address"), max_length=500, blank=True)
    source_name = models.CharField(_("read by"), max_length=60, blank=True)
    source_version = models.CharField(_("source version"), max_length=20, blank=True)
    origin = models.CharField(
        _("captured from"),
        max_length=20,
        default="web",
        help_text=_("Which part of Postulo produced this: the web interface, or the API."),
    )
    data = models.JSONField(_("parsed posting"), default=dict)
    status = models.CharField(
        _("status"), max_length=20, choices=CaptureStatus, default=CaptureStatus.PENDING
    )
    posting = models.ForeignKey(
        JobPosting,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captures",
        verbose_name=_("listing"),
        help_text=_("The listing this capture became, once reviewed."),
    )
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captures",
        verbose_name=_("application"),
    )

    class Meta:
        verbose_name = _("capture")
        verbose_name_plural = _("captures")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("owner", "status"))]

    def __str__(self) -> str:
        return self.data.get("title") or self.url or _("Empty capture")

    def get_absolute_url(self) -> str:
        return reverse("jobs:capture_review", args=[self.pk])

    @property
    def posting_data(self):
        """The parsed posting, validated again on the way out.

        Stored JSON can outlive the schema that wrote it. Validating on read means a
        capture saved by an older version shows up as a clear error rather than as
        subtly wrong values in a form somebody is about to accept.
        """
        from postulo.plugins.base import JobPostingData

        return JobPostingData(**self.data)

    @property
    def is_pending(self) -> bool:
        return self.status == CaptureStatus.PENDING
