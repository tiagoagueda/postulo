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

import uuid

from django.db import models
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Now
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
        return (
            self.select_related("posting", "posting__company")
            .prefetch_related("tags")
            .with_next_interview()
        )

    def with_table_data(self) -> ApplicationQuerySet:
        """Annotate what the optional table columns show: the last activity and the next reminder.

        Subqueries rather than joins, for the same reason as the interview: they compose.
        """
        last_event = (
            ApplicationEvent.objects.filter(application=OuterRef("pk"))
            .order_by("-occurred_at")
            .values("occurred_at")[:1]
        )
        next_reminder = (
            Reminder.objects.filter(application=OuterRef("pk"), done_at__isnull=True)
            .order_by("due_at")
            .values("due_at")[:1]
        )
        return self.annotate(
            last_activity_at=Subquery(last_event), next_reminder_at=Subquery(next_reminder)
        )

    def with_next_interview(self) -> ApplicationQuerySet:
        """Annotate ``next_interview_at``: the start of the soonest interview still ahead.

        A subquery rather than a join with ``Min``, so it composes with any other
        annotation without multiplying rows.
        """
        upcoming = (
            Interview.objects.filter(
                application=OuterRef("pk"),
                outcome=InterviewOutcome.SCHEDULED,
                ends_at__gte=Now(),
            )
            .order_by("starts_at")
            .values("starts_at")[:1]
        )
        return self.annotate(next_interview_at=Subquery(upcoming))


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
    sent_uploads = models.ManyToManyField(
        "documents.UploadedDocument",
        blank=True,
        related_name="applications",
        verbose_name=_("files sent"),
        help_text=_("Files you already had, as opposed to documents Postulo rendered."),
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
    INTERVIEW_SCHEDULED = "interview_scheduled", _("Interview scheduled")
    INTERVIEW_CANCELLED = "interview_cancelled", _("Interview cancelled")
    ASSESSMENT = "assessment", _("Assessment or test")
    OFFER = "offer", _("Offer received")
    REJECTION = "rejection", _("Rejection received")
    FOLLOW_UP = "follow_up", _("Followed up")
    OTHER = "other", _("Other")


#: Kinds the record writes for itself. Offering them to be typed would let the log
#: contradict the field or the interview they describe.
SYSTEM_EVENT_KINDS = frozenset(
    {EventKind.STATUS_CHANGE, EventKind.INTERVIEW_SCHEDULED, EventKind.INTERVIEW_CANCELLED}
)


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

    #: Who wrote it when it was not the person at the keyboard: "API token laptop-agent".
    #: Blank means the person themselves, which is nearly always.
    actor = models.CharField(_("recorded by"), max_length=120, blank=True)

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
    #: When its falling due was announced through the person's notifiers, if it was.
    notified_at = models.DateTimeField(_("notified on"), null=True, blank=True)

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


class InterviewKind(models.TextChoices):
    PHONE = "phone", _("Phone screen")
    VIDEO = "video", _("Video call")
    ONSITE = "onsite", _("On site")
    PANEL = "panel", _("Panel")
    ASSESSMENT = "assessment", _("Assessment or test")
    OTHER = "other", _("Other")


class InterviewOutcome(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    DONE = "done", _("Held")
    CANCELLED = "cancelled", _("Cancelled")
    NO_SHOW = "no_show", _("They did not show up")


#: Outcomes under which the meeting is over, one way or another.
SETTLED_OUTCOMES = frozenset(
    {InterviewOutcome.DONE, InterviewOutcome.CANCELLED, InterviewOutcome.NO_SHOW}
)


class InterviewQuerySet(models.QuerySet):
    def for_user(self, user) -> InterviewQuerySet:
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(owner=user)

    def scheduled(self) -> InterviewQuerySet:
        """Interviews nobody has recorded an outcome for yet."""
        return self.filter(outcome=InterviewOutcome.SCHEDULED)

    def upcoming(self, at=None) -> InterviewQuerySet:
        """Scheduled interviews that have not finished yet, soonest first."""
        return self.scheduled().filter(ends_at__gte=at or timezone.now()).order_by("starts_at")

    def awaiting_outcome(self, at=None) -> InterviewQuerySet:
        """Scheduled interviews whose time has passed: the person should say how it went."""
        return self.scheduled().filter(ends_at__lt=at or timezone.now())

    def with_display_data(self) -> InterviewQuerySet:
        return self.select_related(
            "application", "application__posting", "application__posting__company"
        ).prefetch_related("contacts")


def _mint_uid() -> str:
    """A calendar identifier: minted once, never changed, so a sync recognises the meeting."""
    return f"{uuid.uuid4()}@postulo"


class Interview(OwnedModel):
    """A meeting with the other side: a start, an end, a place, the people, and a kind.

    A timeline entry says an interview *happened*; this says one *will*, which is what a
    calendar, a reminder and a "coming up" list all need. The log stays the truth about
    what took place: scheduling one writes an entry, and so does settling it.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="interviews",
        verbose_name=_("application"),
    )
    kind = models.CharField(
        _("kind"), max_length=20, choices=InterviewKind, default=InterviewKind.VIDEO
    )
    starts_at = models.DateTimeField(_("starts"), db_index=True)
    ends_at = models.DateTimeField(_("ends"))
    location = models.CharField(
        _("where"),
        max_length=500,
        blank=True,
        help_text=_("An address, or the link to the call."),
    )
    contacts = models.ManyToManyField(
        Contact, blank=True, related_name="interviews", verbose_name=_("who you are meeting")
    )
    notes = models.TextField(_("preparation notes"), blank=True)
    outcome = models.CharField(
        _("outcome"),
        max_length=20,
        choices=InterviewOutcome,
        default=InterviewOutcome.SCHEDULED,
        db_index=True,
    )
    #: Stable across edits, so a calendar that imported the meeting once can find it again.
    uid = models.CharField(_("calendar identifier"), max_length=64, default=_mint_uid)
    #: The nudge made when it was scheduled, if one was. Cancelling the interview settles it.
    reminder = models.OneToOneField(
        Reminder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview",
        verbose_name=_("reminder"),
    )

    objects = InterviewQuerySet.as_manager()

    class Meta:
        verbose_name = _("interview")
        verbose_name_plural = _("interviews")
        ordering = ("starts_at", "pk")
        constraints = [
            # Unique per calendar, which is per person: two people importing the same
            # archive each keep the identifier their own calendar already knows.
            models.UniqueConstraint(fields=("owner", "uid"), name="interview_uid_per_owner"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.application}"

    def get_absolute_url(self) -> str:
        return f"{self.application.get_absolute_url()}#interview-{self.pk}"

    @property
    def is_scheduled(self) -> bool:
        return self.outcome == InterviewOutcome.SCHEDULED

    @property
    def is_settled(self) -> bool:
        return self.outcome in SETTLED_OUTCOMES

    @property
    def is_over(self) -> bool:
        """Whether its time has passed, whatever was recorded about it."""
        return self.ends_at < timezone.now()

    @property
    def awaits_outcome(self) -> bool:
        return self.is_scheduled and self.is_over

    @property
    def is_link(self) -> bool:
        """Whether the place is somewhere to click rather than somewhere to go."""
        return self.location.startswith(("http://", "https://"))

    @property
    def duration(self):
        return self.ends_at - self.starts_at
