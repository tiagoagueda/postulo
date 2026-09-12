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
import posixpath

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.models import OwnedModel

from . import kinds, themes


def upload_to_documents(instance, filename: str) -> str:
    """Store files under the owner, so a stray path can only ever reach one person.

    Media is never served directly, but defence in depth is cheap here.
    """
    return f"documents/{instance.owner_id}/{timezone.now():%Y/%m}/{filename}"


def theme_field(kind: str) -> models.CharField:
    """The column a document's theme lives in.

    Not `choices`, which is what it was. Choices are frozen into every migration that
    touches the field, so a theme arriving from an installed plugin could never be one --
    and a theme that is only ever two names is a theme that has to be taught every new kind
    of document by hand. The name is a string now, validated against
    `postulo.documents.themes` at the moment it is set, and every existing row keeps the
    value it already had (#132).
    """
    return models.CharField(
        _("theme"),
        max_length=themes.MAX_NAME_LENGTH,
        default=themes.DEFAULT,
        validators=[themes.SetsThisKind(kind)],
    )


def renders_of(draft):
    """Every PDF produced from this draft, newest first.

    What ``related_name="renders"`` gave before the link became generic (#130). A
    `GenericRelation` would give it back and would also give a cascade, and the cascade is
    precisely what must not happen: deleting a CV must leave the PDF an employer received
    exactly where it is. So this is a query rather than a relation — the reverse half of the
    link, without the deletion behaviour that comes attached to the real thing.

    `RenderedDocument` is defined further down this module and resolved when this runs.
    """
    return RenderedDocument.objects.filter(
        source_type=ContentType.objects.get_for_model(draft.__class__), source_id=draft.pk
    ).order_by("-rendered_at", "pk")


class CVKind(models.TextChoices):
    """What sort of document this selection is, which decides its shape rather than its name.

    A **CV** is a career read backwards: what you did, where, and when, with the dates
    carrying the argument. A **portfolio** is the work first — projects and the things they
    point at — with the career as context rather than as the spine.

    One model rather than two, for the reason `CoverLetter` already holds four shapes: a
    portfolio *is* a selection from the career record with its own layout, and it would use
    every line of `CVItem`'s machinery unchanged. Two near-identical models would be two
    forms, two lists, two exporters and two of every future change (#133).

    And *of different kinds* -- a developer's portfolio, a designer's, a researcher's -- is
    answered by the theme rather than by a third value here. They differ in what they select
    and how they are laid out, and both of those are already somebody's to choose.
    """

    CV = "cv", _("CV")
    PORTFOLIO = "portfolio", _("Portfolio")


#: What each kind starts out set in. A portfolio leads with the work, so it gets the theme
#: written for that; a CV keeps the one it has always had.
CV_THEMES = {CVKind.CV: "plain", CVKind.PORTFOLIO: "plain"}


class CV(OwnedModel):
    """A named selection of your career, aimed at a particular kind of role."""

    name = models.CharField(
        _("name"), max_length=120, help_text=_("For you, not for the employer: “Backend, English”.")
    )
    kind = models.CharField(
        _("kind"), max_length=20, choices=CVKind, default=CVKind.CV, db_index=True
    )
    headline = models.CharField(_("headline"), max_length=200, blank=True)
    summary = models.TextField(
        _("summary"), blank=True, help_text=_("The opening paragraph, if you use one.")
    )
    theme = theme_field(themes.Kind.CV)
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

    @property
    def theme_label(self) -> str:
        """The theme's name in words, for the pages that mention it.

        What ``get_theme_display()`` used to be. It cannot be any more, and the reason is
        the point of #132: a label read out of ``choices`` can only ever name a theme
        compiled into the model, so a theme from a plugin would have shown as a bare slug.
        """
        return themes.label_for(self.theme)

    @property
    def renders(self):
        """The PDFs made from this CV. See `renders_of`."""
        return renders_of(self)

    def included_items(self):
        """The items that will actually be rendered, in order."""
        return self.items.filter(is_included=True).select_related("content_type")

    @property
    def document_kind(self) -> str:
        """Which kind of document a render of this is filed as.

        The same shape `CoverLetter` uses: the model's own vocabulary of shapes maps onto the
        one a *file* is labelled with, rather than the two being kept in step by hand (#133).
        """
        return DocumentKind.PORTFOLIO if self.kind == CVKind.PORTFOLIO else DocumentKind.CV

    @property
    def theme_kind(self) -> str:
        """Which theme vocabulary sets this. What the picker and the renderer both ask."""
        from . import themes

        return themes.Kind.PORTFOLIO if self.kind == CVKind.PORTFOLIO else themes.Kind.CV


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
    theme = theme_field(themes.Kind.LETTER)
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
    def theme_label(self) -> str:
        """The theme's name in words. See `CV.theme_label`."""
        return themes.label_for(self.theme)

    @property
    def renders(self):
        """The PDFs made from this letter. See `renders_of`."""
        return renders_of(self)

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
    #: The choices come from the registry rather than from the enumeration, so a kind a
    #: plugin adds reaches this picker without a migration -- which is what makes "a kind is
    #: a plugin" mean something. The *values* stay in `DocumentKind`, because a value in a
    #: database column is stored, exported and read by the API, and is not a thing to
    #: compute (#133).
    kind = models.CharField(
        _("kind"), max_length=20, choices=kinds.choices, default=DocumentKind.CV
    )
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

    #: Every copy of this file, and the cascade that used to be `on_delete` on the column:
    #: deleting the file deletes the rows saying where its copies went (#130).
    copies = GenericRelation(
        "documents.DocumentCopy",
        content_type_field="document_type",
        object_id_field="document_id",
    )

    #: What a store is told this is, and where to fetch it. Declared on the model rather
    #: than asked with `isinstance` in `stores.py`, so a third thing that holds a file needs
    #: no branch there at all -- which is what "stores keep working without knowing about
    #: kinds" has to mean if it is to survive a fourth (#133).
    archive_origin = "upload"
    download_url_name = "documents:upload_download"

    @property
    def archived_at(self):
        """When a store should say this document is from. See `archive_origin`."""
        return self.created_at

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

    @property
    def download_name(self) -> str:
        """What the file is called on the way out: the title, with the upload's own extension.

        The extension is the one thing that tells an operating system what the bytes are,
        and it comes from the stored name, which keeps the upload's. Every download used to
        be called `<title>.pdf`, so a `.docx` arrived as a Word document no PDF viewer would
        open -- served as `application/pdf` too, since the type is guessed from the name
        (#193). The stem is the title because that is the name the person gave it; the
        stored stem may carry the suffix Django adds to keep two uploads apart.
        """
        suffix = posixpath.splitext(self.file.name)[1] if self.file else ""
        return f"{self.title}{suffix}"


class RenderedDocument(OwnedModel):
    """A PDF exactly as it was sent, kept unchanged.

    The source text is stored alongside the file. A PDF is awkward to search and
    impossible to diff; keeping the text means you can still answer "what did I claim?"
    without opening anything.
    """

    title = models.CharField(_("title"), max_length=250)
    #: From the registry, as an upload's is, and for the same reason (#133).
    kind = models.CharField(
        _("kind"), max_length=20, choices=kinds.choices, default=DocumentKind.CV
    )
    file = models.FileField(_("file"), upload_to=upload_to_documents)

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rendered_documents",
        verbose_name=_("application"),
    )
    #: What produced this PDF: a CV, a cover letter, or whatever kind arrives next.
    #:
    #: A generic link, for the reason `CVItem` already gives one model over: two nullable
    #: foreign keys and an `isinstance` at every reader say the same thing less clearly and
    #: need widening every time a kind is added. A portfolio, an email, a report -- each was
    #: two columns and a migration; now each is a package (#130).
    #:
    #: **Legitimately empty.** An uploaded document came from a file rather than from
    #: anything authored here, and a render whose source was deleted has none either. Every
    #: reader copes with `None`, which is what `source_label` is for.
    #:
    #: **Deleting the source must not delete this**, which is why there is no
    #: `GenericRelation` back from `CV` or `CoverLetter`: one would give a cascade, and the
    #: whole point of this model is that a PDF an employer received survives somebody
    #: tidying up their drafts. A receiver in `signals.py` clears the link instead, which is
    #: the `SET_NULL` these columns used to carry, written out.
    source_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("kind of source"),
    )
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    source_text = models.TextField(_("text as sent"), blank=True)
    checksum = models.CharField(_("checksum"), max_length=64, blank=True, editable=False)
    rendered_at = models.DateTimeField(_("rendered on"), default=timezone.now)

    #: Every copy of this render, and the cascade that used to be `on_delete` on the
    #: column: deleting a render deletes the rows saying where its copies went.
    copies = GenericRelation(
        "documents.DocumentCopy",
        content_type_field="document_type",
        object_id_field="document_id",
    )

    #: See `UploadedDocument.archive_origin`. A render is dated by when it was rendered,
    #: which is when the employer got it, not by when the row happened to be written.
    archive_origin = "render"
    download_url_name = "documents:rendered_download"

    @property
    def download_name(self) -> str:
        """What the file is called on the way out. A render is always a PDF Postulo drew."""
        return f"{self.title}.pdf"

    @property
    def archived_at(self):
        return self.rendered_at

    class Meta:
        verbose_name = _("sent document")
        verbose_name_plural = _("sent documents")
        ordering = ("-rendered_at", "pk")
        indexes = [models.Index(fields=("source_type", "source_id"))]

    def __str__(self) -> str:
        return self.title

    @property
    def source_label(self) -> str:
        """What made this, in words, or empty where nothing did or nothing is left.

        A reader asks this rather than `isinstance`, so a kind arriving later is a kind
        this already describes.
        """
        source = self.source
        return str(source) if source is not None else ""

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
    #: Which document was copied: a render, an upload, or whatever kind arrives next.
    #: The cascade the two columns carried lives on the `GenericRelation` at each end, so
    #: deleting a document still deletes the rows saying where its copies went (#130).
    document_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        verbose_name=_("kind of document"),
        related_name="+",
    )
    document_id = models.PositiveBigIntegerField()
    document = GenericForeignKey("document_type", "document_id")

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
            # The check constraint that said "exactly one of these two" is gone with the
            # two: a generic link is exactly one thing by construction, which is one fewer
            # rule to widen when a third kind of document arrives (#130).
            models.UniqueConstraint(
                fields=("connection", "document_type", "document_id"),
                condition=models.Q(connection__isnull=False),
                name="documents_copy_once_per_document",
            ),
        ]
        indexes = [models.Index(fields=("document_type", "document_id"))]

    def __str__(self) -> str:
        return f"{self.label or self.store}: {self.get_status_display()}"

    @property
    def is_sent(self) -> bool:
        return self.status == CopyStatus.SENT
