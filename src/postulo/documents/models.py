"""CVs, cover letters, uploaded files, and the snapshots of what you actually sent.

Three ideas hold this together.

**A CV variant is a selection, not a copy.** ``CV`` picks items out of
:mod:`postulo.resume` through ``CVItem`` and orders them. Correcting a job title in the
master copy corrects it in every variant. A variant may override an item's highlights
for its own purposes without touching the original.

**A cover letter is a template until it is sent.** Placeholders are filled from the
application it is being sent with, so one well-written letter serves many applications
without copy-paste drift.

**What you sent is frozen.** ``RenderedDocument`` stores the PDF produced at the moment
of sending, along with the text it was built from. Months later, when an interviewer
asks about something on your CV, you need the version they read, not the version you
have edited eleven times since.
"""

from __future__ import annotations

import hashlib

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel


def upload_to_documents(instance, filename: str) -> str:
    """Store files under the owner, so a stray path can only ever reach one person.

    Media is never served directly, but defence in depth is cheap here.
    """
    return f"documents/{instance.owner_id}/{timezone.now():%Y/%m}/{filename}"


class Theme(models.TextChoices):
    """Built-in rendering themes.

    Kept as choices rather than user-editable rows: a theme is a Django template plus a
    stylesheet, and letting people upload those would mean executing their markup during
    rendering. User themes belong behind a deliberate decision, not in the first version.
    """

    PLAIN = "plain", _("Plain")
    CLASSIC = "classic", _("Classic")


class CV(OwnedModel):
    """A named selection of your career, aimed at a particular kind of role."""

    name = models.CharField(
        _("name"), max_length=120, help_text=_("For you, not for the employer: “Backend, English”.")
    )
    headline = models.CharField(_("headline"), max_length=200, blank=True)
    summary = models.TextField(
        _("summary"), blank=True, help_text=_("The opening paragraph, if you use one.")
    )
    theme = models.CharField(_("theme"), max_length=20, choices=Theme, default=Theme.PLAIN)
    language = models.CharField(
        _("language"),
        max_length=10,
        blank=True,
        help_text=_(
            "Which language this variant is written in. Leave blank to follow your profile."
        ),
    )
    show_contact_details = models.BooleanField(
        _("include contact details"),
        default=True,
        help_text=_("Your name, email and location, taken from your profile."),
    )

    class Meta:
        verbose_name = _("CV")
        verbose_name_plural = _("CVs")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=("owner", "name"), name="unique_cv_name_per_owner")
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("documents:cv_detail", args=[self.pk])

    def included_items(self):
        """The items that will actually be rendered, in order."""
        return self.items.filter(is_included=True).select_related("content_type")


class CVItem(OwnedModel):
    """One entry on one CV variant.

    A generic relation is used because a CV is an ordered list of heterogeneous things.
    Six nullable foreign keys with a check constraint would say the same thing less
    clearly, and would need widening every time a new kind of item is added.
    """

    cv = models.ForeignKey(CV, on_delete=models.CASCADE, related_name="items", verbose_name=_("CV"))

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    item = GenericForeignKey("content_type", "object_id")

    order = models.PositiveIntegerField(_("order"), default=0)
    is_included = models.BooleanField(
        _("included"),
        default=True,
        help_text=_("Uncheck to keep the entry on this variant but leave it off the page."),
    )
    override_highlights = models.TextField(
        _("highlights for this CV"),
        blank=True,
        help_text=_("Replaces the master highlights on this variant only. One per line."),
    )

    class Meta:
        verbose_name = _("CV entry")
        verbose_name_plural = _("CV entries")
        ordering = ("order", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("cv", "content_type", "object_id"), name="unique_item_per_cv"
            )
        ]
        indexes = [models.Index(fields=("content_type", "object_id"))]

    def __str__(self) -> str:
        return str(self.item) if self.item else _("Missing entry")

    @property
    def highlight_lines(self) -> list[str]:
        """The highlights to render: this variant's override, or the master copy."""
        from postulo.resume.models import split_highlights

        if self.override_highlights.strip():
            return split_highlights(self.override_highlights)
        return split_highlights(getattr(self.item, "highlights", ""))


class LetterKind(models.TextChoices):
    """What sort of letter this is, which decides its shape rather than its wording.

    A **cover letter** is one page, addressed, about one posting. A **motivation letter**
    is longer and sectioned, about the person and their reasons, usually with no addressee
    block — the norm for academic posts, EU institutions, NGOs and much of the continent.
    A **speculative letter** has no posting behind it. A **follow-up note** comes after an
    interview and is short.

    The names are a translation hazard worth knowing about: in French and Portuguese
    *lettre de motivation* and *carta de motivação* are the everyday words for what
    English calls a cover letter. The two kinds here are told apart by their shape — the
    length, the sections, the addressee — and never by the name alone.
    """

    COVER = "cover", _("Cover letter")
    MOTIVATION = "motivation", _("Motivation letter")
    SPECULATIVE = "speculative", _("Speculative letter")
    FOLLOW_UP = "follow_up", _("Follow-up note")


#: What a new letter of each kind starts as. Not a template to be filled in mechanically:
#: something on the page beats an empty box, and shows the shape the kind expects.
LETTER_STARTERS = {
    LetterKind.COVER: _(
        "Dear {{ company }},\n\n"
        "[Why this role, in a sentence.]\n\n"
        "[What you have done that bears on it.]\n\n"
        "Yours sincerely,\n{{ name }}"
    ),
    LetterKind.MOTIVATION: _(
        "[What you are applying for, and the one thing that makes you the person for "
        "it.]\n\n"
        "Why this work\n[What draws you to it.]\n\n"
        "Why {{ company }}\n[What you know about them.]\n\n"
        "What I bring\n[Your route here, and what it taught you.]\n\n"
        "{{ name }}\n{{ date }}"
    ),
    LetterKind.SPECULATIVE: _(
        "Dear {{ company }},\n\n"
        "You are not advertising, and I am writing anyway.\n\n"
        "[What you would bring, and to which part of the work.]\n\n"
        "Yours sincerely,\n{{ name }}"
    ),
    LetterKind.FOLLOW_UP: _(
        "Dear [name],\n\n"
        "Thank you for your time on [date].\n\n"
        "[The one thing you would like them to remember.]\n\n"
        "Yours sincerely,\n{{ name }}"
    ),
}

#: The theme each kind starts with. A motivation letter is a piece of prose and reads
#: better set; a follow-up note is an email in all but name.
LETTER_THEMES = {
    LetterKind.COVER: "plain",
    LetterKind.MOTIVATION: "classic",
    LetterKind.SPECULATIVE: "plain",
    LetterKind.FOLLOW_UP: "plain",
}


class CoverLetter(OwnedModel):
    """A letter, or a template for many letters.

    Placeholders are filled in when the letter is rendered against an application. They
    are deliberately a small, fixed set: a general-purpose expression language in a
    document people paste employer-supplied text into is a liability, not a feature.
    """

    #: Placeholders the renderer understands, and where each comes from.
    PLACEHOLDERS = {
        "company": _("The company you are applying to"),
        "role": _("The job title"),
        "location": _("Where the role is based"),
        "name": _("Your own name"),
        "date": _("Today's date"),
    }

    name = models.CharField(_("name"), max_length=120)
    kind = models.CharField(
        _("kind"), max_length=20, choices=LetterKind, default=LetterKind.COVER, db_index=True
    )
    subject = models.CharField(_("subject"), max_length=250, blank=True)
    body = models.TextField(
        _("body"), help_text=_("Placeholders such as {{ company }} are filled in when sent.")
    )
    is_template = models.BooleanField(
        _("reusable template"),
        default=True,
        help_text=_("Templates appear when you send a letter with an application."),
    )
    theme = models.CharField(_("theme"), max_length=20, choices=Theme, default=Theme.PLAIN)
    #: What the body is written in. A letter to a Portuguese employer is written in
    #: Portuguese, and the PDF has to say so: a screen reader reading it out is often the
    #: recruiter's, and hyphenation and justification follow the declaration too.
    language = models.CharField(
        _("language"),
        max_length=10,
        blank=True,
        help_text=_(
            "Which language this letter is written in. Leave blank to follow your profile."
        ),
    )

    class Meta:
        verbose_name = _("letter")
        verbose_name_plural = _("letters")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("documents:letter_detail", args=[self.pk])

    @property
    def document_kind(self) -> str:
        """Which kind of document a render of this letter is filed as."""
        if self.kind == LetterKind.MOTIVATION:
            return DocumentKind.MOTIVATION_LETTER
        return DocumentKind.COVER_LETTER


class DocumentKind(models.TextChoices):
    CV = "cv", _("CV")
    COVER_LETTER = "cover_letter", _("Cover letter")
    MOTIVATION_LETTER = "motivation_letter", _("Motivation letter")
    CERTIFICATE = "certificate", _("Certificate")
    PORTFOLIO = "portfolio", _("Portfolio")
    REFERENCE = "reference", _("Reference")
    OTHER = "other", _("Other")


class UploadedDocument(OwnedModel):
    """A file you already had: a designed CV, a scanned certificate, a portfolio.

    The hybrid half of the model. Not everything worth sending was written in Postulo,
    and an application manager that cannot hold the PDF a designer made for you is not
    much use.
    """

    title = models.CharField(_("title"), max_length=200)
    kind = models.CharField(_("kind"), max_length=20, choices=DocumentKind, default=DocumentKind.CV)
    file = models.FileField(
        _("file"),
        upload_to=upload_to_documents,
        validators=[
            FileExtensionValidator(
                ["pdf", "doc", "docx", "odt", "rtf", "txt", "png", "jpg", "jpeg"]
            )
        ],
    )
    notes = models.TextField(_("notes"), blank=True)

    version = models.PositiveIntegerField(_("version"), default=1)
    replaces = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaced_by",
        verbose_name=_("replaces"),
        help_text=_("The earlier version this supersedes. The old file is kept."),
    )

    class Meta:
        verbose_name = _("uploaded document")
        verbose_name_plural = _("uploaded documents")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.title} (v{self.version})"

    def get_absolute_url(self) -> str:
        # There is no detail page for a file; the edit page shows everything about it.
        return reverse("documents:upload_update", args=[self.pk])

    @property
    def is_current(self) -> bool:
        """Whether anything supersedes this version."""
        return not self.replaced_by.exists()


class RenderedDocument(OwnedModel):
    """A PDF exactly as it was sent, kept unchanged.

    The source text is stored alongside the file. A PDF is awkward to search and
    impossible to diff; keeping the text means you can still answer "what did I claim?"
    without opening anything.
    """

    title = models.CharField(_("title"), max_length=250)
    kind = models.CharField(_("kind"), max_length=20, choices=DocumentKind, default=DocumentKind.CV)
    file = models.FileField(_("file"), upload_to=upload_to_documents)

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rendered_documents",
        verbose_name=_("application"),
    )
    cv = models.ForeignKey(
        CV,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renders",
        verbose_name=_("from CV"),
    )
    cover_letter = models.ForeignKey(
        CoverLetter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renders",
        verbose_name=_("from cover letter"),
    )

    source_text = models.TextField(_("text as sent"), blank=True)
    checksum = models.CharField(_("checksum"), max_length=64, blank=True, editable=False)
    rendered_at = models.DateTimeField(_("rendered on"), default=timezone.now)

    class Meta:
        verbose_name = _("sent document")
        verbose_name_plural = _("sent documents")
        ordering = ("-rendered_at",)

    def __str__(self) -> str:
        return self.title

    @staticmethod
    def checksum_for(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


class CopyStatus(models.TextChoices):
    PENDING = "pending", _("Waiting to be sent")
    SENT = "sent", _("Archived")
    FAILED = "failed", _("Failed")
    DECLINED = "declined", _("Not accepted")


class DocumentCopy(OwnedModel):
    """Where a copy of a document went, or is going, and how that is getting on.

    Local media is the source of truth; a copy is what an external store — a Paperless,
    a share — was given. One row per document per connection. The reference the store
    handed back (its id, a link) lives here and travels in the export, so a restored
    instance still knows where its copies went even before the connection is recreated:
    ``connection`` may be empty, ``store`` and ``label`` say what it was.
    """

    connection = models.ForeignKey(
        "plugins.Connection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="copies",
        verbose_name=_("connection"),
    )
    store = models.CharField(_("store"), max_length=60)
    label = models.CharField(_("label"), max_length=100, blank=True)
    rendered = models.ForeignKey(
        RenderedDocument,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="copies",
        verbose_name=_("sent document"),
    )
    upload = models.ForeignKey(
        UploadedDocument,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="copies",
        verbose_name=_("uploaded document"),
    )

    status = models.CharField(
        _("status"), max_length=10, choices=CopyStatus, default=CopyStatus.PENDING
    )
    external_id = models.CharField(_("id in the store"), max_length=500, blank=True)
    external_url = models.CharField(_("link in the store"), max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    next_attempt_at = models.DateTimeField(
        _("next attempt"), default=timezone.now, null=True, blank=True
    )
    last_attempt_at = models.DateTimeField(_("last attempt"), null=True, blank=True)
    sent_at = models.DateTimeField(_("sent on"), null=True, blank=True)
    last_error = models.TextField(_("last error"), blank=True)

    class Meta:
        verbose_name = _("document copy")
        verbose_name_plural = _("document copies")
        ordering = ("pk",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(rendered__isnull=False, upload__isnull=True)
                    | models.Q(rendered__isnull=True, upload__isnull=False)
                ),
                name="documents_copy_of_one_document",
            ),
            models.UniqueConstraint(
                fields=("connection", "rendered"),
                condition=models.Q(connection__isnull=False, rendered__isnull=False),
                name="documents_copy_once_per_render",
            ),
            models.UniqueConstraint(
                fields=("connection", "upload"),
                condition=models.Q(connection__isnull=False, upload__isnull=False),
                name="documents_copy_once_per_upload",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label or self.store}: {self.get_status_display()}"

    @property
    def document(self):
        return self.rendered if self.rendered_id else self.upload

    @property
    def is_sent(self) -> bool:
        return self.status == CopyStatus.SENT
