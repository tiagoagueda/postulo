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
from functools import cached_property

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from postulo.core.language_field import LanguageField
from postulo.core.models import OwnedModel

from . import kinds, themes

# The stored values of Postulo's own kinds live with the registry that describes them, which
# no longer has to import this module to find them (#248). Handed out here as well, because
# this is where the archive and the tests -- a plugin's own among them -- find them.
from .kinds import DocumentKind


def upload_to_documents(instance, filename: str) -> str:
    """Store files under the owner, so a stray path can only ever reach one person.

    Media is never served directly, but defence in depth is cheap here.
    """
    return f"documents/{instance.owner_id}/{timezone.now():%Y/%m}/{filename}"


def theme_field(*kinds: str) -> models.CharField:
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
        validators=[themes.SetsThisKind(*kinds)],
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


def document_language(document) -> str:
    """The language tag a rendered document declares, best answer first.

    What the document itself says, then what its owner reads Postulo in, then the
    instance default. British English is the last resort rather than the assumption: the
    letter that goes out is the one a recruiter's screen reader may read aloud, and
    declaring the wrong language there makes it unintelligible rather than merely
    untidy — hyphenation and justification follow the same declaration.

    Beside the models rather than in `rendering`, because a document asks it of itself
    (`effective_language`) and the models importing the renderer was a cycle (#248).
    `rendering` still hands it out.

    Written as a tag is written, and only where it is one (#337): what is not a language
    tag declares nothing, and the next answer is taken rather than printed into a ``lang``
    that no reader can use.
    """
    from postulo.core import languages, site

    profile = getattr(getattr(document, "owner", None), "profile", None)
    for declared in (getattr(document, "language", ""), getattr(profile, "language", "")):
        if languages.well_formed(declared):
            return languages.tag(declared)
    return languages.tag(site.default_language()) or languages.SOURCE


def document_direction(document) -> str:
    """``"rtl"`` or ``"ltr"`` for a rendered document, from the language it declares.

    Not from whoever is looking at it. A person reading Postulo in Arabic may write a CV in
    English, and the PDF that goes out has to be laid out for the language it is written in
    — WeasyPrint hyphenates, justifies and orders the lines by this and by nothing else.
    """
    from postulo.core import languages

    return languages.direction(document_language(document))


class HasALanguage:
    """What every document can be asked: which language it is in, and its name.

    Two answers to the first question, because the four kinds differ in what a blank field
    means, and that difference is the whole of #283. What they share is the second.
    """

    @cached_property
    def language_name(self) -> str:
        """The language's own name for itself, or nothing where none is known."""
        from postulo.core import languages

        return languages.native_name(self.effective_language)


class DeclaresALanguage(HasALanguage):
    """A document Postulo writes: blank means *follow your profile* (#280).

    The answer a reader wants is the resolved one rather than the field: it is what the
    PDF declares, what a recruiter's screen reader reads the letter out with, and what
    WeasyPrint hyphenates by (#223). A declared language and an inherited one are drawn
    exactly the same, because what reaches the employer is the same either way and a
    distinction here would be about a setting rather than about the document.
    """

    @cached_property
    def effective_language(self) -> str:
        """Cached, because a card asks for the language and then for its name, and the
        last step of the fallback reads the instance's settings row -- which is not cached
        itself (#231), so asking three times a card is three reads a card (#280)."""
        return document_language(self)


class RecordsALanguage(HasALanguage):
    """A document Postulo did not write: blank means *nobody has said* (#283).

    An upload is a file somebody else made and Postulo has never read, and a render is a
    PDF frozen at a moment that has passed. Falling back to what its owner happens to read
    Postulo in would be a claim about contents nobody has examined -- which is how a
    scanned German certificate came to be filed in Paperless as English, defeating the
    point of sending it there. Nothing is a better answer than a guess, and it is the
    prompt to say.
    """

    @cached_property
    def effective_language(self) -> str:
        return (self.language or "").strip()


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


class Prints(models.TextChoices):
    """How one CV chooses the detail it prints of one kind (#308).

    Three answers, and the first is what every CV did before there was a question.

    **Default follows the profile.** The primary number, the account's address, the primary
    link of a kind, every identifier: whatever *Your details* says today, read again at
    every render, so a new primary number is printed without the CV being opened.

    **Chosen pins a row**, which then stays whatever becomes primary. A pinned row that is
    deleted from the profile -- or is no longer offered, because its feature was switched
    off -- is replaced by nothing: the kind prints none until somebody chooses again. Not
    by the primary, because a CV must never print a detail nobody chose for it, and the
    person who pinned a work number may have done so to keep the other one off the page.

    **None prints none of that kind**, including one recorded later.
    """

    DEFAULT = "default", _("Follow your details")
    CHOSEN = "chosen", _("Chosen for this CV")
    NONE = "none", _("None")


def prints_field(verbose_name) -> models.CharField:
    """The column one kind's answer lives in. See `Prints`."""
    return models.CharField(verbose_name, max_length=10, choices=Prints, default=Prints.DEFAULT)


def pinned_row(to: str, verbose_name) -> models.ForeignKey:
    """The row a CV pins when its answer is `Prints.CHOSEN`.

    `SET_NULL`, and the answer stays *chosen*: that pair -- chosen, and nothing to point at
    -- is how the CV knows a row was deleted from under it, so its page can say so and its
    next render prints none of that kind. No reverse name, because nothing asks a telephone
    number which CVs print it.

    **A column is not a permission.** Whose row this is gets checked where it is written
    (the form, the API, the importer) and again where it is read: `printing.resolve` looks
    the row up among the owner's own, so an id that reached this column some other way
    still prints nothing.
    """
    return models.ForeignKey(
        to,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=verbose_name,
    )


class CV(DeclaresALanguage, OwnedModel):
    """A named selection of your career, aimed at a particular kind of role."""

    name = models.CharField(
        _("name"), max_length=120, help_text=_("For you, not for the employer: “Backend, English”.")
    )
    kind = models.CharField(
        _("type"), max_length=20, choices=CVKind, default=CVKind.CV, db_index=True
    )
    headline = models.CharField(_("headline"), max_length=200, blank=True)
    summary = models.TextField(
        _("summary"), blank=True, help_text=_("The opening paragraph, if you use one.")
    )
    theme = theme_field(themes.Kind.CV, themes.Kind.PORTFOLIO)
    language = LanguageField(
        _("language"),
        blank=True,
        help_text=_(
            "Which language this variant is written in. Leave blank to follow your profile."
        ),
    )
    #: The master switch, as it has always been: off prints no name and none of what is
    #: chosen below, whatever those say.
    show_contact_details = models.BooleanField(
        _("include contact details"),
        default=True,
        help_text=_(
            "Your name and the details chosen below, taken from your details. Unticked, "
            "this CV prints none of them."
        ),
    )

    # --- which of the owner's details this CV prints (#308) ----------------------------
    #
    # One answer per kind of detail the contact block can carry, each a `Prints` and, for
    # *chosen*, the row it pins. Every default is what a CV printed before the question
    # existed, so a CV nobody opens the choice on is drawn exactly as it was. They are read
    # when the document is drawn -- `printing.resolve`, from `rendering.contact_details` --
    # and nowhere else, which is what freezes them with a PDF: a `RenderedDocument` keeps
    # the file and the text, and nothing here reaches either afterwards.
    phone_choice = prints_field(_("telephone number printed"))
    pinned_phone = pinned_row("core.PhoneNumber", _("telephone number chosen"))
    email_choice = prints_field(_("email address printed"))
    #: allauth's row, because that is where an account's addresses are (#145). Only a
    #: confirmed one is ever offered or printed: an address nobody has answered at may be a
    #: typing mistake or somebody else's, and a CV is where an employer replies to.
    pinned_email = pinned_row("account.EmailAddress", _("email address chosen"))
    social_choice = prints_field(_("social profile printed"))
    pinned_social = pinned_row("core.WebLink", _("social profile chosen"))
    repository_choice = prints_field(_("code repository printed"))
    pinned_repository = pinned_row("core.WebLink", _("code repository chosen"))
    website_choice = prints_field(_("website printed"))
    pinned_website = pinned_row("core.WebLink", _("website chosen"))
    #: A handle is never printed because it was added: the default prints none, and a CV
    #: prints one only when somebody chose it (#682).
    messaging_choice = prints_field(_("messaging handle printed"))
    pinned_messaging = pinned_row("core.MessagingHandle", _("messaging handle chosen"))
    #: Any number of them, so the pin is a set. The default is every identifier, including
    #: one added later; *chosen* is exactly the ticked ones, and one deleted from the
    #: profile leaves the set by itself.
    identifiers_choice = prints_field(_("identifiers printed"))
    pinned_identifiers = models.ManyToManyField(
        "accounts.PersonIdentifier",
        blank=True,
        related_name="+",
        verbose_name=_("identifiers chosen"),
    )
    #: Only ever the line `core.postal.printed_location` gives: what was typed, or the
    #: primary address's town and country. Never a street (#92, #309).
    show_location = models.BooleanField(
        _("print where you are"),
        default=True,
        help_text=_("The town and country from your details, never a street."),
    )
    #: Off, both of them: #309 put the two on the profile on the promise that neither is
    #: printed until a CV says so, and this is where one does.
    show_form_of_address = models.BooleanField(
        _("print your form of address before your name"), default=False
    )
    show_pronouns = models.BooleanField(_("print your pronouns after your name"), default=False)
    #: Off as well (#679): the two most identifying things a profile can hold are printed
    #: only by a CV that says so, each by its own switch, in one line under the name.
    show_birth_date = models.BooleanField(_("print your date of birth"), default=False)
    show_birth_place = models.BooleanField(_("print your place of birth"), default=False)
    #: And off (#680): the countries listed, or the scope's wording where none is.
    show_nationality = models.BooleanField(_("print your nationality"), default=False)
    #: And off (#681), one plain switch.
    show_gender = models.BooleanField(_("print your gender"), default=False)

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
        return with_entries(self.items.filter(is_included=True))

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

    def clean(self):
        """The theme must set *this row's* kind, which the column's validator cannot know.

        The column holds CVs and portfolios, so its validator only asks for a theme that
        sets one of them; a portfolio-only theme from a plugin is then a legitimate choice
        for a portfolio and a refused one for a CV (#412). A name nothing recognises is the
        validator's to refuse, and falls back to `plain` when rendered.
        """
        super().clean()
        theme = themes.find(self.theme)
        if theme is not None and not theme.sets(self.theme_kind):
            raise ValidationError(
                {
                    "theme": ValidationError(
                        _("The %(theme)s theme does not set this type of document."),
                        code="wrong_kind",
                        params={"theme": theme.label},
                    )
                }
            )


def with_entries(items):
    """These CV items with the entries they point at, a query per kind rather than per item.

    A generic link has no join to follow, so every reader that walked the items and read
    ``item.item`` cost a query an entry, and three more for each skill group shown in a
    language (#559). A skill group arrives with its skills.
    """
    from django.apps import apps
    from django.contrib.contenttypes.prefetch import GenericPrefetch

    from postulo.resume.models import ResumeItem

    querysets = [
        model.objects.prefetch_related("skills")
        if model._meta.model_name == "skillgroup"
        else model.objects.all()
        for model in apps.get_app_config("resume").get_models()
        if issubclass(model, ResumeItem)
    ]
    return items.select_related("content_type").prefetch_related(GenericPrefetch("item", querysets))


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
        return str(self.item) if self.item else str(_("Missing entry"))


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


class CoverLetter(DeclaresALanguage, OwnedModel):
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
        # The application's main contact. The follow-up starter has asked for a name in
        # square brackets since it was written, and the application already knew it (#235).
        "contact": _("The person you are writing to, if the application names one"),
        "name": _("Your own name"),
        "date": _("Today's date"),
    }

    name = models.CharField(_("name"), max_length=120)
    kind = models.CharField(
        _("type"), max_length=20, choices=LetterKind, default=LetterKind.COVER, db_index=True
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
    language = LanguageField(
        _("language"),
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


class UploadedDocument(RecordsALanguage, OwnedModel):
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
        _("type"), max_length=20, choices=kinds.choices, default=DocumentKind.CV
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
    #: Which language the file is in, said by the person who uploaded it (#283).
    #:
    #: **Blank means nobody has said**, not "follow your profile". This is a file Postulo
    #: has never read: guessing from the reader's own interface language is how a store
    #: came to be told a German certificate was English. Asked for on the form, left
    #: blank until somebody answers, and shown as unsaid rather than as nothing.
    language = LanguageField(
        _("language"),
        blank=True,
        help_text=_(
            "Which language this file is in. Postulo cannot read it, so nothing is "
            "assumed; a store files it by this."
        ),
    )

    #: The file's SHA-256, written once when it arrives.
    #:
    #: A render has had one since #133, and an upload had none, which left two holes. A store
    #: was handed an empty checksum and so could not tell a copy it already held from a new
    #: one; and nothing could tell whether the bytes an application says it sent are still the
    #: bytes on disk. The file cannot be swapped on an edit any more (#217), so this is
    #: written at creation and never changes: a different file is a different version, through
    #: `replaces`.
    checksum = models.CharField(_("checksum"), max_length=64, blank=True, editable=False)

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
    #: A file somebody put here is a file they meant to keep, so a store is always offered
    #: it. See `RenderedDocument.goes_to_stores` for the one that is not always (#236).
    goes_to_stores = True

    def save(self, *args, **kwargs):
        """Write the checksum the first time the bytes are here, and never again.

        Read in chunks rather than whole: an upload is capped, but a model that reads a file
        into memory to save a row is a habit that outlives the cap.
        """
        if self.file and not self.checksum:
            digest = hashlib.sha256()
            # Leave the file as it was found. Hashing opens it, and a handle left open is a
            # file Windows will not let anything delete afterwards -- which is exactly what
            # the delete this issue adds has to be able to do.
            was_closed = self.file.closed
            try:
                for chunk in self.file.chunks():
                    digest.update(chunk)
            finally:
                if was_closed:
                    self.file.close()
                else:
                    self.file.seek(0)
            self.checksum = digest.hexdigest()
            if "update_fields" in kwargs and kwargs["update_fields"] is not None:
                kwargs["update_fields"] = [*kwargs["update_fields"], "checksum"]
        super().save(*args, **kwargs)

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


class ReferenceDelivery(models.TextChoices):
    """How a reference letter reaches the employer (#666)."""

    YOU = "you", _("You send it")
    REFEREE = "referee", _("The referee sends it themselves")


class ReferenceLetter(OwnedModel):
    """What is known about a letter somebody wrote about you: who, when, and how it travels.

    A side record rather than columns on the upload, because "valid until" and "who sends
    it" mean nothing on a CV or a certificate, and a referee is a person. The file is the
    upload it belongs to, so storage, checksum, copies and the version chain are unchanged;
    deleting the upload deletes this (#666).

    The referee is a `Contact`, which may have no company. **Deleting the contact unlinks
    the letter and keeps it**: the file is the account holder's, and the name on it is not a
    reason to lose it. The erasure screen offers to delete the letters as well.
    """

    upload = models.OneToOneField(
        UploadedDocument,
        on_delete=models.CASCADE,
        related_name="reference_letter",
        verbose_name=_("file"),
    )
    referee = models.ForeignKey(
        "jobs.Contact",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reference_letters",
        verbose_name=_("referee"),
    )
    written_on = models.DateField(_("written on"), null=True, blank=True)
    #: The person's own date, never enforced: it warns and does not block. Most letters have
    #: none, so it is optional.
    valid_until = models.DateField(_("do not send after"), null=True, blank=True)
    delivery = models.CharField(
        _("how it reaches the employer"),
        max_length=10,
        choices=ReferenceDelivery,
        default=ReferenceDelivery.YOU,
    )

    class Meta:
        verbose_name = _("reference letter")
        verbose_name_plural = _("reference letters")

    def __str__(self) -> str:
        return str(self.upload.title)

    def clean(self) -> None:
        super().clean()
        if self.written_on and self.valid_until and self.valid_until < self.written_on:
            raise ValidationError(
                {"valid_until": _("A letter cannot expire before it was written.")}
            )

    @property
    def is_expired(self) -> bool:
        """Whether the person's own "do not send after" date has passed."""
        return bool(self.valid_until and self.valid_until < timezone.localdate())

    @property
    def sent_by_referee(self) -> bool:
        return self.delivery == ReferenceDelivery.REFEREE

    @property
    def description(self) -> str:
        """The line a list shows beside the file: who wrote it, when, and whether it is stale."""
        from django.utils.formats import date_format

        parts = []
        if self.referee_id:
            parts.append(_("from %(name)s") % {"name": self.referee.name})
        if self.written_on:
            parts.append(
                _("written %(date)s") % {"date": date_format(self.written_on, "DATE_FORMAT")}
            )
        if self.is_expired:
            parts.append(
                _("not to be sent after %(date)s")
                % {"date": date_format(self.valid_until, "DATE_FORMAT")}
            )
        return ", ".join(str(part) for part in parts)


class RenderedDocument(RecordsALanguage, OwnedModel):
    """A PDF exactly as it was sent, kept unchanged.

    The source text is stored alongside the file. A PDF is awkward to search and
    impossible to diff; keeping the text means you can still answer "what did I claim?"
    without opening anything.
    """

    title = models.CharField(_("title"), max_length=250)
    #: From the registry, as an upload's is, and for the same reason (#133).
    kind = models.CharField(
        _("type"), max_length=20, choices=kinds.choices, default=DocumentKind.CV
    )
    file = models.FileField(_("file"), upload_to=upload_to_documents)

    #: **`SET_NULL`, not a cascade** (#217). This model exists so that what an employer
    #: received survives everything else being tidied up, and a cascade here undid that from
    #: the other end: deleting an application -- or a listing, or a company, each of which
    #: cascades into applications -- silently took the frozen PDFs with it. Deleting the
    #: *source* was already handled that way (`signals.py`); this is the same rule for the
    #: other link. What is left behind still says where it went, because `sent_to` is text.
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rendered_documents",
        verbose_name=_("application"),
    )
    #: The role and employer this was sent to, written when the snapshot is taken.
    #:
    #: Text rather than a link, and kept even while the link is there: an application that is
    #: later deleted would otherwise leave a PDF nobody can place. "Research Engineer at Black
    #: Mesa" is what somebody needs to recognise it, and it cannot go stale in a way that
    #: matters -- it is what the posting said on the day it was sent.
    sent_to = models.CharField(_("sent to"), max_length=250, blank=True, editable=False)
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
        verbose_name=_("type of source"),
    )
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey("source_type", "source_id")

    #: The language this PDF declared when it was frozen, kept beside `sent_to` and the
    #: checksum and for the same reason (#283).
    #:
    #: Written at the snapshot rather than read from the source afterwards. The source can
    #: be edited later, or deleted -- `signals.py` clears the link so the PDF survives it
    #: -- and either way the language of what the employer actually received would change
    #: or vanish under a record whose whole purpose is that it cannot. Not editable, like
    #: everything else about a frozen document.
    language = LanguageField(_("language"), blank=True, editable=False)

    source_text = models.TextField(_("text as sent"), blank=True)
    #: The words of a CV without their setting, kept beside the markup they were set in.
    #:
    #: `source_text` is what the PDF was drawn from, and for a CV that is a whole themed
    #: page: every rule of the stylesheet, then the markup, then the words. It is the right
    #: thing to keep -- it is what was rendered -- and the wrong thing to read, because two
    #: versions that differ by one job title differ by one line in four hundred, and a
    #: change of theme differs in all of them while saying nothing new. This is the text a
    #: person compares and a portal is handed (#236).
    #:
    #: **Blank for a letter**, whose `source_text` already is its words and would only be
    #: said twice, **and blank for a CV frozen before this existed**. That one is not
    #: backfilled: the text would have to be built from the CV as it stands today, which is
    #: exactly what a snapshot exists not to be. `text_to_compare` is what a reader asks.
    plain_text = models.TextField(_("text without its layout"), blank=True, editable=False)
    checksum = models.CharField(_("checksum"), max_length=64, blank=True, editable=False)
    #: Whether the file was written with its properties -- the author, the subject, the
    #: keywords, a title with the person's name in it -- or without them (#480). Part of what
    #: is recorded, because a copy exported without them is a different file from one with
    #: them, and the stored copy is the one that was actually sent.
    with_properties = models.BooleanField(
        _("written with its properties"), default=True, editable=False
    )
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

    @property
    def went_with_an_application(self) -> bool:
        """Whether this was sent to somebody, rather than exported on its own (#236).

        The link or the words. `application` is cleared when the application is deleted
        (#217) and `sent_to` is what is left saying where the document went, so a PDF an
        employer received does not turn into an export because somebody tidied up.
        """
        return bool(self.application_id or self.sent_to)

    @property
    def goes_to_stores(self) -> bool:
        """Whether the stores its owner has connected are given a copy (#236).

        Every *Export PDF* used to be copied to every store, and the only way to see where a
        page breaks was to export -- so a Paperless filled with the tries. What a store is
        for is what was handed over: a render that went with an application is one, and one
        exported on its own stays here, where it can still be downloaded.

        **A report is the exception, because it never goes with an application.** It is
        handed to an employment office, at a deliberate press, and #162 decided that a store
        is given it; asking it for an application would switch that off by accident, and
        leave every store connection with a *Report* switch that does nothing.
        """
        return self.went_with_an_application or self.kind == DocumentKind.REPORT

    def file_is_as_rendered(self) -> bool:
        """Whether the bytes on disk are still the ones the checksum was taken of.

        What the checksum is for, and until #236 nothing asked: a record whose file has
        gone, or has been changed underneath it, is not one to hand back in place of a new
        render. Read in chunks, for the reason `UploadedDocument.save` gives.
        """
        if not self.file or not self.checksum:
            return False
        digest = hashlib.sha256()
        try:
            with self.file.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except (OSError, ValueError):
            return False
        return digest.hexdigest() == self.checksum

    @property
    def text_to_compare(self) -> str:
        """This document's words as plain text, or nothing where none were kept (#236).

        A CV keeps them in `plain_text`. A letter's `source_text` is its words already, and
        the registry is what says a kind is a letter, so one a plugin brings is read the
        same way. Anything else -- a CV frozen before its text was kept, a report -- answers
        with nothing, and the page says so rather than comparing markup.
        """
        if self.plain_text:
            return self.plain_text
        if kinds.theme_kind_for(self.kind) == themes.Kind.LETTER:
            return self.source_text
        return ""

    def previous(self):
        """The render of the same source that came before this one, or nothing.

        *The same source* is the generic link, so it is a question only while the source is
        still there: deleting a CV clears the link on every PDF made from it
        (`signals.py`), and what is left has nothing to be compared with.
        """
        if not self.source_type_id or not self.source_id:
            return None
        return (
            RenderedDocument.objects.filter(
                owner_id=self.owner_id, source_type_id=self.source_type_id, source_id=self.source_id
            )
            .filter(
                models.Q(rendered_at__lt=self.rendered_at)
                | models.Q(rendered_at=self.rendered_at, pk__lt=self.pk)
            )
            .order_by("-rendered_at", "-pk")
            .first()
        )


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
        verbose_name=_("type of document"),
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
    #: Set while somebody is sending this copy, and apart from `next_attempt_at` so that
    #: *Send now* can override the wait of a failed copy without taking a live claim (#509).
    claimed_until = models.DateTimeField(_("claimed until"), null=True, blank=True)
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
