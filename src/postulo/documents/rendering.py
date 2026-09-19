"""Building documents: HTML from records, PDF from HTML, and snapshots of what was sent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import formats, timezone, translation
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from . import themes
from .models import CV, CoverLetter, RenderedDocument
from .pdf import html_to_pdf

#: Only these placeholders are substituted, and only these.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

#: How a placeholder with nothing to put in it is drawn in a *preview*, and never in a
#: document that goes out. A known placeholder whose value is empty used to become an empty
#: string in both, so "Dear {{ company }}," previewed as "Dear ," and a posting with no
#: location sent a gap somebody only found afterwards. The name is kept inside the marker so
#: that what is missing is the thing named (#235).
MISSING_PLACEHOLDER = "[ %s ]"

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


def document_holder(document) -> str:
    """Whose document this is, in their own words, or empty if nothing says.

    The name on the document rather than the name of the file it was filed under. Used for
    the title a PDF viewer announces and the name it is downloaded with (#223).
    """
    owner = getattr(document, "owner", None)
    if owner is None:
        return ""
    return (owner.get_full_name() or getattr(owner, "display_name", "") or "").strip()


def document_title(document) -> str:
    """What a PDF viewer shows and a screen reader announces for this document.

    The holder's name and what the document is — "Alex Morgan — CV" — rather than the
    variant's name, which is the person's own filing and is marked in the model as being
    for them and not for the employer (#223). Called inside the language override, so the
    word for the kind is in the document's language.
    """
    holder = document_holder(document)
    kind = str(getattr(document, "get_kind_display", lambda: "")()) or ""
    if holder and kind:
        return gettext("%(name)s — %(kind)s") % {"name": holder, "kind": kind}
    return holder or kind or getattr(document, "name", "")


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
    from postulo.core import phone_numbers, web_links

    profile = getattr(owner, "profile", None)
    primary = phone_numbers.primary_for(profile) if profile is not None else None
    # One link of each kind, the primary, under the names the themes have always read --
    # a theme somebody wrote against the three columns keeps working (#189).
    links = web_links.primaries_for(profile) if profile is not None else {}

    def link(kind: str) -> str:
        row = links.get(kind)
        return row.url if row else ""

    identifiers = list(profile.identifiers.all()) if profile is not None else []
    details = {
        "name": owner.get_full_name() or owner.display_name,
        "email": owner.email,
        "headline": getattr(profile, "headline", ""),
        "phone": primary.number if primary else "",
        "location": getattr(profile, "location", ""),
        "website": link(web_links.Kind.WEBSITE),
        "linkedin_url": link(web_links.Kind.SOCIAL),
        "source_repo_url": link(web_links.Kind.REPOSITORY),
        # Beside the website and the LinkedIn address, which is where a reader looks.
        "identifiers": identifiers,
    }
    # The same details again as an ordered list, so a theme can put a *real character*
    # between them. They were separated by a CSS `::after`, and generated content is not in
    # the text a PDF hands back: an applicant tracking system, `pdftotext` or a screen
    # reader got the telephone number run into the email address with nothing between them
    # (#235). Every key above is still here and still means what it did, because a theme a
    # plugin ships reads those by name (#189).
    details["details"] = [
        part
        for part in (
            details["email"],
            details["phone"],
            *(f"{row.display_label} {row.value}" for row in identifiers),
            details["location"],
            details["website"],
            details["linkedin_url"],
            details["source_repo_url"],
        )
        if part
    ]
    # A letter's sender block, which has always shown the three a letterhead has room for.
    details["brief_details"] = [
        part for part in (details["email"], details["phone"], details["location"]) if part
    ]
    return details


def render_cv_html(cv: CV, *, nonce=None) -> str:
    """Render a CV variant to a complete, self-contained HTML document.

    ``nonce`` is for the preview page alone: the browser's content security policy refuses
    the theme's inlined `<style>` unless the element carries the request's nonce. The PDF
    renderer passes none and the attribute is left out (#232).

    The kind decides which theme vocabulary sets it: a portfolio leads with the work and a
    CV with the career, and that is a difference in structure rather than in styling (#133).

    Rendered in the document's own language (#223). #131 translated what the person wrote
    and left the page around it in whatever language they happened to be reading Postulo
    in, so a French CV exported by somebody browsing in English came out headed
    "Experience" over "Mar 2021 – present" — a document that is half one language and half
    another, sent to an employer who reads one of them.
    """
    with translation.override(document_language(cv)):
        return render_to_string(
            themes.template_for(cv.theme, cv.theme_kind),
            {
                "cv": cv,
                "sections": build_sections(cv),
                "contact": contact_details(cv.owner) if cv.show_contact_details else None,
                "document_language": document_language(cv),
                "document_direction": document_direction(cv),
                "document_title": document_title(cv),
                "csp_nonce": nonce,
            },
        )


def fill_placeholders(text: str, values: dict[str, str], *, mark_empty: bool = False) -> str:
    """Substitute ``{{ name }}`` placeholders from ``values``.

    Deliberately *not* Django's template engine. A cover letter is text a person wrote,
    often with fragments pasted from a job advert, and handing that to a template engine
    would let ``{% ... %}`` in the source reach into the application. A regular
    expression over a fixed set of names cannot do anything but substitute.

    Unknown placeholders are left alone rather than blanked, so a typo is visible in the
    draft instead of silently deleting a word.

    ``mark_empty`` is for a preview: a placeholder Postulo knows but has nothing to fill it
    with is drawn as a marker rather than as the nothing it will become, so that the gap is
    something a person can see before it goes out (#235).
    """

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        if not value and mark_empty:
            return MISSING_PLACEHOLDER % key
        return value

    return PLACEHOLDER_PATTERN.sub(replace, text or "")


def letter_values(letter: CoverLetter, application=None) -> dict[str, str]:
    """The values available to a letter's placeholders."""
    owner = letter.owner
    values = {
        "name": owner.get_full_name() or owner.display_name,
        # Through Django's formatter rather than `strftime`, which names the month from the
        # C locale and so wrote an English month into every letter whatever language the
        # letter was in (#223). Called inside the override, so it follows the letter.
        "date": formats.date_format(timezone.localdate(), "DATE_FORMAT"),
        "company": "",
        "role": "",
        "location": "",
        # Who the letter is addressed to. The follow-up starter has asked for "[name]" in
        # square brackets since it was written, which is a placeholder Postulo could fill
        # and did not offer -- the application already knows who you spoke to (#235).
        "contact": "",
    }
    if application is not None:
        values["company"] = application.posting.company.name
        values["role"] = application.posting.title
        values["location"] = application.posting.location
        values["contact"] = application.contact.name if application.contact_id else ""
    return values


def unfilled_placeholders(letter: CoverLetter, application=None) -> list[str]:
    """The placeholders this letter uses that there is nothing to fill in, in order.

    What a warning before freezing is made of. A placeholder nothing recognises is not in
    here: that is a typo, it survives into the document as the literal text somebody typed,
    and it is visible in the draft already.
    """
    values = letter_values(letter, application)
    found: list[str] = []
    for key in PLACEHOLDER_PATTERN.findall(f"{letter.subject}\n{letter.body or ''}"):
        if key in values and not values[key] and key not in found:
            found.append(key)
    return found


def render_letter_html(
    letter: CoverLetter, application=None, *, mark_empty: bool = False, nonce=None
) -> str:
    """Render a cover letter, with its placeholders filled in.

    In the letter's own language, and so is the date it carries (#223). A letter whose
    `{{ date }}` was written in one language under a heading printed in another was the
    plainest version of this: two dates, two languages, one page.
    """
    with translation.override(document_language(letter)):
        values = letter_values(letter, application)
        return render_to_string(
            themes.template_for(letter.theme, themes.Kind.LETTER),
            {
                "letter": letter,
                "subject": fill_placeholders(letter.subject, values, mark_empty=mark_empty),
                "body": fill_placeholders(letter.body, values, mark_empty=mark_empty),
                "contact": contact_details(letter.owner),
                "application": application,
                "document_language": document_language(letter),
                "document_direction": document_direction(letter),
                "document_title": document_title(letter),
                "csp_nonce": nonce,
            },
        )


def letter_text(letter: CoverLetter, application=None, *, mark_empty: bool = False) -> str:
    """The letter as plain text, for storing beside the PDF and for showing before it goes.

    The same words as the PDF, so it is filled in the letter's language too (#223).
    """
    with translation.override(document_language(letter)):
        values = letter_values(letter, application)
        subject = fill_placeholders(letter.subject, values, mark_empty=mark_empty)
        body = fill_placeholders(letter.body, values, mark_empty=mark_empty)
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


def _file_and_save(document: RenderedDocument, filename: str, content: bytes) -> None:
    """Put the bytes on disk and the record in the database, the record in one transaction.

    The views that reach here no longer run inside a transaction of their own: rendering a
    PDF takes seconds, and on SQLite a request's transaction holds the write lock for every
    one of them, so the whole instance waited on one person's CV (#220). What has to be
    atomic is the row and what its saving sets off — `schedule_copies` writes a pending copy
    for every store the owner has connected — and that is these two statements and nothing
    slow between them.

    The file is written first and outside, as it always was. A row that fails to save leaves
    bytes nobody points at, which is the same orphan a rolled-back request left before.
    """
    _keep(document, filename, content)
    with transaction.atomic():
        document.save()


def sent_to(application) -> str:
    """Where a snapshot went, in words, so it still says so if the application is deleted.

    The posting as it stood on the day it was sent. `RenderedDocument.application` is
    `SET_NULL` since #217, and a PDF with a blank where the employer's name should be is a
    record of nothing.
    """
    if application is None:
        return ""
    posting = application.posting
    return gettext("%(role)s at %(company)s") % {
        "role": posting.title,
        "company": posting.company.name,
    }


def snapshot_cv(cv: CV, *, application=None, backend=None) -> RenderedDocument:
    """Freeze a CV as a PDF, exactly as it stands now.

    This is the record of what an employer received. It is never regenerated: months
    later, when someone asks about a line on your CV, you need the version they read.
    """
    html = render_cv_html(cv)
    content = html_to_pdf(html, backend=backend)
    # Named for whoever opens it, not for the shelf it was filed on (#223). The variant's
    # name is the person's own filing — "Backend, English" — and the model's help text says
    # so; it was going into the PDF's `/Title`, which a viewer shows in its title bar and a
    # screen reader announces, and into the file name attached to portals and emails.
    with translation.override(document_language(cv)):
        title = document_title(cv)

    document = RenderedDocument(
        owner=cv.owner,
        title=title,
        # What the model says it is, rather than a constant: a portfolio filed as a CV is a
        # document an employment office or a store would then mislabel (#133).
        kind=cv.document_kind,
        source=cv,
        application=application,
        sent_to=sent_to(application),
        source_text=html,
        checksum=RenderedDocument.checksum_for(content),
    )
    _file_and_save(document, f"{slugify(title) or 'cv'}.pdf", content)
    return document


def snapshot_report(owner, *, title: str, html: str, filename: str, backend=None):
    """Freeze a report as a PDF, filed under Sent documents like a CV is (#162).

    A report is computed from the record and never stored as a page; what is stored is
    the document somebody handed over, at the moment they pressed *Download*. Pressing it
    twice on the same day is one document: the HTML carries the day it was produced, so
    an identical text is an identical report, and the one already filed is handed back
    rather than a twin. Nothing links a report to a source -- there is no model behind
    it -- so `source` stays empty, which every reader already copes with.
    """
    from .models import DocumentKind

    existing = (
        RenderedDocument.objects.for_user(owner)
        .filter(kind=DocumentKind.REPORT, source_text=html)
        .order_by("-rendered_at")
        .first()
    )
    if existing is not None:
        return existing
    content = html_to_pdf(html, backend=backend)
    document = RenderedDocument(
        owner=owner,
        title=title,
        kind=DocumentKind.REPORT,
        source_text=html,
        checksum=RenderedDocument.checksum_for(content),
    )
    _file_and_save(document, filename, content)
    return document


def snapshot_letter(letter: CoverLetter, *, application=None, backend=None) -> RenderedDocument:
    """Freeze a cover letter as a PDF, with its placeholders already resolved."""
    html = render_letter_html(letter, application)
    content = html_to_pdf(html, backend=backend)
    # The recipient's name, not the person's own filing name for this draft (#223).
    with translation.override(document_language(letter)):
        title = document_title(letter)

    document = RenderedDocument(
        owner=letter.owner,
        title=title,
        kind=letter.document_kind,
        source=letter,
        application=application,
        sent_to=sent_to(application),
        source_text=letter_text(letter, application),
        checksum=RenderedDocument.checksum_for(content),
    )
    _file_and_save(document, f"{slugify(title) or 'cover-letter'}.pdf", content)
    return document
