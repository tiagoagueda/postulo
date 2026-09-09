"""Building documents: HTML from records, PDF from HTML, and snapshots of what was sent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from . import themes
from .models import CV, CoverLetter, DocumentKind, RenderedDocument
from .pdf import html_to_pdf

#: Only these placeholders are substituted, and only these.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

#: Headings for each kind of CV entry, in the order they appear if nothing says otherwise.
SECTION_LABELS = {
    "experience": _("Experience"),
    "education": _("Education"),
    "project": _("Projects"),
    "link": _("Links"),
    "skillgroup": _("Skills"),
    "certification": _("Certifications"),
    "languageskill": _("Languages"),
}


def document_language(document) -> str:
    """The language tag a rendered document declares, best answer first.

    What the document itself says, then what its owner reads Postulo in, then the
    instance default. British English is the last resort rather than the assumption: the
    letter that goes out is the one a recruiter's screen reader may read aloud, and
    declaring the wrong language there makes it unintelligible rather than merely
    untidy — hyphenation and justification follow the same declaration.
    """
    from postulo.core import site

    own = (getattr(document, "language", "") or "").strip()
    if own:
        return own
    profile = getattr(getattr(document, "owner", None), "profile", None)
    from_profile = (getattr(profile, "language", "") or "").strip()
    if from_profile:
        return from_profile
    return site.default_language() or "en-GB"


def document_direction(document) -> str:
    """``"rtl"`` or ``"ltr"`` for a rendered document, from the language it declares.

    Not from whoever is looking at it. A person reading Postulo in Arabic may write a CV in
    English, and the PDF that goes out has to be laid out for the language it is written in
    — WeasyPrint hyphenates, justifies and orders the lines by this and by nothing else.
    """
    from postulo.core import languages

    return languages.direction(document_language(document))


@dataclass
class Section:
    """A run of CV entries sharing a heading."""

    kind: str
    label: str
    items: list = field(default_factory=list)


@dataclass
class Entry:
    """One entry as this CV prints it: in this CV's language, with its overrides.

    A wrapper rather than the `CVItem` itself, and it keeps the two names a template already
    reads — ``entry.item`` and ``entry.highlight_lines`` — so every theme renders a
    translated CV without knowing translations exist. That matters most for the themes
    Postulo has never seen: a plugin's template written against ``entry.item.role`` gets the
    French job title for free, and could not have been asked to do anything else (#131).
    """

    cv_item: object
    item: object

    @property
    def highlight_lines(self) -> list[str]:
        """This variant's override, else the master copy in this CV's language.

        The override wins over the translation, not the other way round. Somebody who wrote
        highlights for *this* CV wrote them for the CV they were looking at, in the language
        it declares, and a translation of the master copy is not a better answer than that.
        """
        from postulo.resume.models import split_highlights

        if self.cv_item.override_highlights.strip():
            return split_highlights(self.cv_item.override_highlights)
        return split_highlights(getattr(self.item, "highlights", ""))


def build_sections(cv: CV) -> list[Section]:
    """Group a CV's entries into sections, keeping the order the owner chose.

    Sections appear in the order their first entry does, so moving one experience to the
    top moves the whole Experience block with it, which is what someone dragging entries
    around expects.

    Each entry reads in the language the CV declares. An entry with nothing to say in that
    language says what it always said, because a blank line where a job used to be is worse
    than a line in the wrong language — and `translating.fields_that_fell_back` is how the
    person finds out which ones did, on the CV's page rather than in the PDF (#131).
    """
    from postulo.resume import translating

    # The language the PDF will declare, not the field: a CV that names none still says
    # something in its `lang`, and text in one language under a declaration of another is
    # the mismatch this whole feature exists to remove.
    language = translating.normalise(document_language(cv))
    cv_items = list(cv.included_items().order_by("order", "pk"))
    entries = [cv_item.item for cv_item in cv_items]
    overrides = translating.overrides_by_entry(entries, language)

    sections: dict[str, Section] = {}
    for cv_item, entry in zip(cv_items, entries, strict=True):
        kind = cv_item.content_type.model
        if kind not in sections:
            sections[kind] = Section(kind=kind, label=str(SECTION_LABELS.get(kind, kind)))
        found = overrides.get(translating.key_of(entry)) if entry is not None else None
        sections[kind].items.append(
            Entry(cv_item=cv_item, item=translating.in_language(entry, language, found or {}))
        )
    return list(sections.values())


def contact_details(owner) -> dict:
    """The contact block, taken from the profile rather than retyped per CV.

    One number is printed, and it is the primary one: a CV header has room for the number
    somebody should ring, not for a list. Whether *Several telephone numbers* is on for
    this person makes no difference here — the document has always shown one, and the
    primary is what "one" means now.
    """
    from postulo.core import phone_numbers

    profile = getattr(owner, "profile", None)
    primary = phone_numbers.primary_for(profile) if profile is not None else None
    return {
        "name": owner.get_full_name() or owner.display_name,
        "email": owner.email,
        "headline": getattr(profile, "headline", ""),
        "phone": primary.number if primary else "",
        "location": getattr(profile, "location", ""),
        "website": getattr(profile, "website", ""),
        "linkedin_url": getattr(profile, "linkedin_url", ""),
        "source_repo_url": getattr(profile, "source_repo_url", ""),
        # Beside the website and the LinkedIn address, which is where a reader looks.
        "identifiers": list(profile.identifiers.all()) if profile is not None else [],
    }


def render_cv_html(cv: CV) -> str:
    """Render a CV variant to a complete, self-contained HTML document."""
    return render_to_string(
        themes.template_for(cv.theme, themes.Kind.CV),
        {
            "cv": cv,
            "sections": build_sections(cv),
            "contact": contact_details(cv.owner) if cv.show_contact_details else None,
            "document_language": document_language(cv),
            "document_direction": document_direction(cv),
        },
    )


def fill_placeholders(text: str, values: dict[str, str]) -> str:
    """Substitute ``{{ name }}`` placeholders from ``values``.

    Deliberately *not* Django's template engine. A cover letter is text a person wrote,
    often with fragments pasted from a job advert, and handing that to a template engine
    would let ``{% ... %}`` in the source reach into the application. A regular
    expression over a fixed set of names cannot do anything but substitute.

    Unknown placeholders are left alone rather than blanked, so a typo is visible in the
    draft instead of silently deleting a word.
    """

    def replace(match: re.Match) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return PLACEHOLDER_PATTERN.sub(replace, text or "")


def letter_values(letter: CoverLetter, application=None) -> dict[str, str]:
    """The values available to a letter's placeholders."""
    owner = letter.owner
    values = {
        "name": owner.get_full_name() or owner.display_name,
        "date": timezone.localdate().strftime("%-d %B %Y" if _supports_dash_day() else "%d %B %Y"),
        "company": "",
        "role": "",
        "location": "",
    }
    if application is not None:
        values["company"] = application.posting.company.name
        values["role"] = application.posting.title
        values["location"] = application.posting.location
    return values


def _supports_dash_day() -> bool:
    """Whether strftime here understands %-d. It does not on Windows."""
    try:
        timezone.localdate().strftime("%-d")
    except ValueError:
        return False
    return True


def render_letter_html(letter: CoverLetter, application=None) -> str:
    """Render a cover letter, with its placeholders filled in."""
    values = letter_values(letter, application)
    return render_to_string(
        themes.template_for(letter.theme, themes.Kind.LETTER),
        {
            "letter": letter,
            "subject": fill_placeholders(letter.subject, values),
            "body": fill_placeholders(letter.body, values),
            "contact": contact_details(letter.owner),
            "application": application,
            "document_language": document_language(letter),
            "document_direction": document_direction(letter),
        },
    )


def letter_text(letter: CoverLetter, application=None) -> str:
    """The letter as plain text, for storing beside the PDF."""
    values = letter_values(letter, application)
    subject = fill_placeholders(letter.subject, values)
    body = fill_placeholders(letter.body, values)
    return f"{subject}\n\n{body}".strip()


def _keep(document: RenderedDocument, filename: str, content: bytes) -> None:
    """Write the PDF to the local store — the one every document is in, always.

    External stores get their copies once the document is saved, through the scheduler.
    """
    from postulo.plugins.localstore import LocalStore

    from .stores import metadata_for

    LocalStore().put(
        document,
        ContentFile(content),
        metadata_for(document, filename=filename),
        {},
        document.owner,
    )


def snapshot_cv(cv: CV, *, application=None, backend=None) -> RenderedDocument:
    """Freeze a CV as a PDF, exactly as it stands now.

    This is the record of what an employer received. It is never regenerated: months
    later, when someone asks about a line on your CV, you need the version they read.
    """
    html = render_cv_html(cv)
    content = html_to_pdf(html, backend=backend)
    title = gettext("%(name)s — CV") % {"name": cv.name}

    document = RenderedDocument(
        owner=cv.owner,
        title=title,
        kind=DocumentKind.CV,
        source=cv,
        application=application,
        source_text=html,
        checksum=RenderedDocument.checksum_for(content),
    )
    _keep(document, f"{slugify(cv.name) or 'cv'}.pdf", content)
    document.save()
    return document


def snapshot_letter(letter: CoverLetter, *, application=None, backend=None) -> RenderedDocument:
    """Freeze a cover letter as a PDF, with its placeholders already resolved."""
    html = render_letter_html(letter, application)
    content = html_to_pdf(html, backend=backend)
    title = gettext("%(name)s — %(kind)s") % {
        "name": letter.name,
        "kind": str(letter.get_kind_display()).lower(),
    }

    document = RenderedDocument(
        owner=letter.owner,
        title=title,
        kind=letter.document_kind,
        source=letter,
        application=application,
        source_text=letter_text(letter, application),
        checksum=RenderedDocument.checksum_for(content),
    )
    _keep(document, f"{slugify(letter.name) or 'cover-letter'}.pdf", content)
    document.save()
    return document
