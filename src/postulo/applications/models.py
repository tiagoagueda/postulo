"""Applications: what you did about a posting, and what came of it.

Two ideas carry most of the weight here.

The **status** field is what you sort and filter by. The **event log** is what actually
happened, in order, and is never rewritten. Storing both is deliberate: a status alone
cannot tell you that a company took five weeks to reply, and a log alone makes a board
view expensive. When they disagree, the log is right.

Nothing here deletes history. Withdrawing, being rejected, or being ghosted are all
outcomes worth keeping, because the interesting questions in a job search — which
sources convert, how long employers take, where applications die — can only be answered
from the failures.
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel, Tag
from postulo.jobs.models import Contact, JobPosting


class Status(models.TextChoices):
    """Where an application stands.

    ``GHOSTED`` earns its place: an employer that simply stops replying is the single
    most common ending, and recording it as "rejected" would misrepresent both the
    employer and your own response-rate figures.
    """

    DRAFT = "draft", _("Draft")
    APPLIED = "applied", _("Applied")
    ACKNOWLEDGED = "acknowledged", _("Acknowledged")
    SCREENING = "screening", _("Screening")
    INTERVIEWING = "interviewing", _("Interviewing")
    ASSESSMENT = "assessment", _("Assessment")
    OFFER = "offer", _("Offer")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    GHOSTED = "ghosted", _("Ghosted")


#: Statuses where the outcome is still undecided.
OPEN_STATUSES = frozenset(
    {
        Status.DRAFT,
        Status.APPLIED,
        Status.ACKNOWLEDGED,
        Status.SCREENING,
        Status.INTERVIEWING,
        Status.ASSESSMENT,
        Status.OFFER,
    }
)

#: The order columns appear on the board, left to right.
BOARD_STATUSES = (
    Status.DRAFT,
    Status.APPLIED,
    Status.ACKNOWLEDGED,
    Status.SCREENING,
    Status.INTERVIEWING,
    Status.ASSESSMENT,
    Status.OFFER,
)


class Channel(models.TextChoices):
    COMPANY_SITE = "company_site", _("Company website")
    JOB_BOARD = "job_board", _("Job board")
    EMAIL = "email", _("Email")
    REFERRAL = "referral", _("Referral")
    RECRUITER = "recruiter", _("Recruiter")
    EVENT = "event", _("Event or fair")
    OTHER = "other", _("Other")


class Priority(models.IntegerChoices):
    LOW = 1, _("Low")
    NORMAL = 2, _("Normal")
    HIGH = 3, _("High")


class ApplicationQuerySet(models.QuerySet):
    def for_user(self, user) -> ApplicationQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def open(self) -> ApplicationQuerySet:
        return self.filter(status__in=list(OPEN_STATUSES))

    def closed(self) -> ApplicationQuerySet:
        return self.exclude(status__in=list(OPEN_STATUSES))

    def with_display_data(self) -> ApplicationQuerySet:
        """Load what every list and board template reads, in one round trip."""
        return self.select_related("posting", "posting__company").prefetch_related("tags")


class Application(OwnedModel):
    """One attempt at one posting."""

    posting = models.ForeignKey(
        JobPosting,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name=_("posting"),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status, default=Status.DRAFT, db_index=True
    )
    channel = models.CharField(_("applied through"), max_length=20, choices=Channel, blank=True)
    priority = models.IntegerField(_("priority"), choices=Priority, default=Priority.NORMAL)

    applied_at = models.DateTimeField(
        _("applied on"),
        null=True,
        blank=True,
        help_text=_("Set automatically the first time the status becomes “Applied”."),
    )
    deadline = models.DateField(_("deadline"), null=True, blank=True)
    closed_at = models.DateTimeField(_("closed on"), null=True, blank=True)

    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
        verbose_name=_("main contact"),
    )
    tags = models.ManyToManyField(
        Tag, blank=True, related_name="applications", verbose_name=_("tags")
    )

    objects = ApplicationQuerySet.as_manager()

    class Meta:
        verbose_name = _("application")
        verbose_name_plural = _("applications")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("owner", "status"))]

    def __str__(self) -> str:
        return f"{self.posting.title} — {self.posting.company.name}"

    def get_absolute_url(self) -> str:
        return reverse("applications:detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def company(self):
        return self.posting.company

    @property
    def days_since_applied(self) -> int | None:
        """How long this has been outstanding, for the "chase it up" nudge."""
        if self.applied_at is None:
            return None
        return (timezone.now() - self.applied_at).days


class EventKind(models.TextChoices):
    STATUS_CHANGE = "status_change", _("Status change")
    NOTE = "note", _("Note")
    EMAIL_SENT = "email_sent", _("Email sent")
    EMAIL_RECEIVED = "email_received", _("Email received")
    CALL = "call", _("Call")
    INTERVIEW = "interview", _("Interview")
    ASSESSMENT = "assessment", _("Assessment or test")
    OFFER = "offer", _("Offer received")
    REJECTION = "rejection", _("Rejection received")
    FOLLOW_UP = "follow_up", _("Followed up")
    OTHER = "other", _("Other")


class ApplicationEventQuerySet(models.QuerySet):
    def for_user(self, user) -> ApplicationEventQuerySet:
        """Scope through the parent application.

        Events carry no owner of their own: duplicating it would create a second source
        of truth that could drift out of step with the application it belongs to.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(application__owner=user)


class ApplicationEvent(models.Model):
    """One thing that happened, recorded once and never edited away.

    Events are append-only by convention rather than by database constraint: the
    interface offers no way to alter one, because a timeline you can quietly rewrite is
    worth very little when you are trying to work out what went wrong.
    """

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="events", verbose_name=_("application")
    )
    kind = models.CharField(_("kind"), max_length=20, choices=EventKind, default=EventKind.NOTE)
    occurred_at = models.DateTimeField(_("happened on"), default=timezone.now, db_index=True)
    summary = models.CharField(_("summary"), max_length=250, blank=True)
    body = models.TextField(_("details"), blank=True)

    from_status = models.CharField(_("from status"), max_length=20, choices=Status, blank=True)
    to_status = models.CharField(_("to status"), max_length=20, choices=Status, blank=True)

    created_at = models.DateTimeField(_("recorded on"), auto_now_add=True)

    objects = ApplicationEventQuerySet.as_manager()

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ("-occurred_at", "-pk")

    def __str__(self) -> str:
        return self.summary or self.get_kind_display()


class ReminderQuerySet(models.QuerySet):
    def for_user(self, user) -> ReminderQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def outstanding(self) -> ReminderQuerySet:
        return self.filter(done_at__isnull=True)

    def due(self, at=None) -> ReminderQuerySet:
        """Outstanding reminders whose moment has arrived."""
        return self.outstanding().filter(due_at__lte=at or timezone.now())


class Reminder(OwnedModel):
    """A nudge to do something about an application on a particular day."""

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="reminders",
        null=True,
        blank=True,
        verbose_name=_("application"),
    )
    summary = models.CharField(_("what to do"), max_length=250)
    due_at = models.DateTimeField(_("when"), db_index=True)
    done_at = models.DateTimeField(_("done on"), null=True, blank=True)

    objects = ReminderQuerySet.as_manager()

    class Meta:
        verbose_name = _("reminder")
        verbose_name_plural = _("reminders")
        ordering = ("due_at",)

    def __str__(self) -> str:
        return self.summary

    @property
    def is_done(self) -> bool:
        return self.done_at is not None

    @property
    def is_overdue(self) -> bool:
        return not self.is_done and self.due_at <= timezone.now()

    def complete(self) -> None:
        if self.done_at is None:
            self.done_at = timezone.now()
            self.save(update_fields=["done_at", "updated_at"])
