"""Building documents: HTML from records, PDF from HTML, and snapshots of what was sent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import formats, timezone
from django.utils.text import slugify
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from postulo.core import languages

from . import formats as file_formats
from . import themes
from .models import (
    CV,
    CoverLetter,
    CVKind,
    RenderedDocument,
    document_direction,
    document_language,
)
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
    language = document_language(cv)
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


def contact_details(owner, cv: CV | None = None) -> dict:
    """The contact block, taken from the profile rather than retyped per CV.

    **Which of the profile's details, the CV says** (#308): `printing.resolve` reads its
    answers and this is the only place they reach a document, so every renderer -- a theme,
    the preview, the plain text, the Word file -- is handed the same ones. With no CV, or a
    CV nobody has opened the choice on, they are what was always printed: the account's
    address, the primary number, the primary link of each kind, every identifier and the
    location. A letter's sender block calls this with no CV and prints what it always has.

    One number is printed: a CV header has room for the number somebody should ring, not
    for a list. Whether *Several telephone numbers* is on for this person makes no
    difference to the default — the document has always shown one, and the primary is what
    "one" means.

    It is printed grouped the way its own country writes it (#304), from the stored
    international form: `+33 6 12 34 56 78`, not thirteen characters in a row.

    The location is what was typed, or the town and country of the primary postal address
    when nothing was (#309): `postal.printed_location`, which every reader asks, and only
    ever that line.
    """
    from postulo.core import phones

    from . import printing

    profile = getattr(owner, "profile", None)
    printed = printing.resolve(owner, cv)
    details = {
        "name": owner.get_full_name() or owner.display_name,
        "email": printed.email,
        "headline": getattr(profile, "headline", ""),
        "phone": phones.readable(printed.phone),
        "location": printed.location,
        # One link of each kind, under the names the themes have always read -- a theme
        # somebody wrote against the three columns keeps working (#189).
        "website": printed.website,
        "linkedin_url": printed.social,
        "source_repo_url": printed.repository,
        # Beside the website and the LinkedIn address, which is where a reader looks.
        "identifiers": printed.identifiers,
    }
    identifiers = printed.identifiers
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
    # What is written beside the name, each only where the CV says so (#308): the form of
    # address before it and the pronouns after. Keys of their own rather than folded into
    # `name`, which a theme prints as the heading and the file's properties carry as the
    # author -- "Dr" is how somebody is addressed, not who wrote the document.
    details["form_of_address"] = printed.form_of_address
    details["pronouns"] = printed.pronouns
    # The same line as one string, for a renderer with no markup to set it in: the
    # brackets are characters here for the reason the separator above is one.
    details["name_line"] = _joined(
        " ",
        printed.form_of_address,
        details["name"],
        f"({printed.pronouns})" if printed.pronouns else "",
    )
    return details


def cv_contact(cv: CV) -> dict | None:
    """The contact block this CV prints, or nothing where its master switch is off.

    `show_contact_details` is asked first and alone decides whether there is a block at
    all; which details are in it is the CV's choice, read by `contact_details`.
    """
    return contact_details(cv.owner, cv) if cv.show_contact_details else None


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
    with languages.override(document_language(cv)):
        return render_to_string(
            themes.template_for(cv.theme, cv.theme_kind),
            {
                "cv": cv,
                "sections": build_sections(cv),
                "contact": cv_contact(cv),
                "document_language": document_language(cv),
                "document_direction": document_direction(cv),
                "document_title": document_title(cv),
                "csp_nonce": nonce,
            },
        )


# ------------------------------------------------------- a CV as its words (#236)

#: What goes between two details on one line: the character the themes write between the
#: contact details, so the text and the PDF say the same line.
BETWEEN = " · "


def _joined(separator: str, *parts) -> str:
    """The parts that say something, with ``separator`` between them."""
    return separator.join(str(part).strip() for part in parts if str(part or "").strip())


def _period(item, *, years_only: bool = False) -> str:
    """When an entry ran, as the themes print it: "March 2021 – present", or the years.

    Called inside the language override, so the month is the document's and so is the word
    for *present* -- the same string the themes translate, asked for by the same name.
    """
    start = getattr(item, "start_date", None)
    end = getattr(item, "end_date", None)
    if years_only:
        return "–".join(str(day.year) for day in (start, end) if day)
    first = formats.date_format(start, "YEAR_MONTH_FORMAT") if start else ""
    last = formats.date_format(end, "YEAR_MONTH_FORMAT") if end else ""
    if start and not end:
        last = gettext("present")
    return _joined(" – ", first, last)


def _entry_blocks(section: Section, entry: Entry) -> list:
    """One experience, qualification or project, as a CV sets it: a heading and what is
    under it."""
    item = entry.item
    if section.kind == "experience":
        title, under = item.role, _joined(BETWEEN, item.organisation, item.location)
    elif section.kind == "education":
        title, under = item.qualification, _joined(BETWEEN, item.institution, item.location)
    else:
        title, under = item.name, getattr(item, "role", "")
    blocks = [file_formats.heading(title, 3)]
    for line in (under, _period(item), getattr(item, "summary", "")):
        if str(line or "").strip():
            blocks.append(file_formats.paragraph(line))
    if entry.highlight_lines:
        blocks.append(file_formats.bullets(entry.highlight_lines))
    return blocks


def _piece_blocks(entry: Entry) -> list:
    """One project, as a portfolio sets it: the work, the case for it, and where to see it."""
    item = entry.item
    blocks = [file_formats.heading(item.name, 3)]
    for line in (_period(item), item.role, item.summary):
        if str(line or "").strip():
            blocks.append(file_formats.paragraph(line))
    if entry.highlight_lines:
        blocks.append(file_formats.bullets(entry.highlight_lines))
    if item.url:
        blocks.append(file_formats.paragraph(item.url))
    return blocks


def _section_blocks(section: Section, *, as_portfolio: bool) -> list:
    """What one section says, in the shape the themes give its kind.

    The same branches `base_cv.html` and `base_portfolio.html` take, and they are kept in
    step on purpose: this text stands beside a PDF as the record of what it claimed, and a
    text that printed a grade the page left out would be a record of something nobody sent.
    A portfolio sets the career as context -- a line an entry, the years and no highlights
    -- and so does its text.
    """
    entries = [entry for entry in section.items if entry.item is not None]
    if section.kind == "skillgroup":
        lines = [f"{one.item.name}: {', '.join(one.item.skill_names)}" for one in entries]
    elif section.kind == "languageskill":
        # A level nobody stated prints nothing, as it does on the page (#235).
        lines = [
            _joined(
                " — ",
                one.item.name,
                one.item.get_proficiency_display() if one.item.proficiency else "",
            )
            for one in entries
        ]
    elif section.kind == "link":
        lines = [
            _joined(BETWEEN, _joined(" — ", one.item.title, one.item.description), one.item.url)
            for one in entries
        ]
    elif section.kind == "certification":
        lines = [
            _joined(
                BETWEEN,
                _joined(", ", one.item.name, one.item.issuer),
                one.item.issued_on.year if one.item.issued_on else "",
            )
            for one in entries
        ]
    elif as_portfolio and section.kind == "project":
        return [block for one in entries for block in _piece_blocks(one)]
    elif as_portfolio:
        lines = [
            _joined(
                BETWEEN,
                one.item.role if section.kind == "experience" else one.item.qualification,
                one.item.organisation if section.kind == "experience" else one.item.institution,
                _period(one.item, years_only=True),
            )
            for one in entries
        ]
    else:
        return [block for one in entries for block in _entry_blocks(section, one)]
    return [file_formats.bullets(lines)]


def cv_outline(cv: CV) -> file_formats.Outline:
    """A CV variant as its words: what plain text, Word and a comparison are made of.

    Built from `build_sections`, in the document's own language, for the reason
    `render_cv_html` gives -- and with the contact block left off where the person left it
    off, the author's name with it.
    """
    language = document_language(cv)
    with languages.override(language):
        contact = cv_contact(cv)
        blocks = []
        if contact:
            # The name as the page sets it: alone, unless this CV prints a form of address
            # before it or the pronouns after (#308).
            blocks.append(file_formats.heading(contact["name_line"], 1))
            if cv.headline or contact["headline"]:
                blocks.append(file_formats.paragraph(cv.headline or contact["headline"]))
            if contact["details"]:
                blocks.append(file_formats.paragraph(BETWEEN.join(contact["details"])))
        elif cv.headline:
            blocks.append(file_formats.heading(cv.headline, 1))
        if cv.summary.strip():
            blocks.append(file_formats.paragraph(cv.summary, apart=True))
        as_portfolio = cv.kind == CVKind.PORTFOLIO
        for section in build_sections(cv):
            blocks.append(file_formats.heading(section.label, 2))
            blocks.extend(_section_blocks(section, as_portfolio=as_portfolio))
        return file_formats.Outline(
            title=document_title(cv),
            language=language,
            direction=document_direction(cv),
            author=contact["name"] if contact else "",
            blocks=tuple(blocks),
        )


def cv_text(cv: CV) -> str:
    """A CV variant as plain text: to paste into a form, and to keep beside a snapshot."""
    return file_formats.as_text(cv_outline(cv))


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
    with languages.override(document_language(letter)):
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
    with languages.override(document_language(letter)):
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


def draft_name(document) -> str:
    """What a draft is called on the way out: the document's title, and that it is a draft.

    In the document's language, as its title is, so the name is one language rather than
    two. The word is there because the file is the only thing that leaves: nothing in
    Postulo will ever say this PDF was a try, so the PDF says it itself (#236).
    """
    with languages.override(document_language(document)):
        return gettext("%(title)s (draft)") % {"title": document_title(document)}


def exported_on_its_own(cv: CV):
    """The PDFs made from this CV that went with no application, newest first."""
    return cv.renders.filter(owner=cv.owner, application__isnull=True, sent_to="")


def already_exported(cv: CV, *, html: str = "", checksum: str = "") -> RenderedDocument | None:
    """The export of this CV that is already filed, where a new one would be its twin.

    Asked twice, because there are two ways of being the same document and neither covers
    the other (#236).

    **By what it was drawn from, before anything is drawn.** An unchanged CV renders to the
    same markup, so the question can be answered without a renderer -- which is the press
    somebody makes twice. It has to be asked this way as well as the other, because the
    other cannot see it on every backend: Chromium writes the minute it printed into each
    PDF, so two renders of one page never share a checksum there.

    **By what was drawn, afterwards.** The checksum has been computed for every render
    since #133 and compared with nothing. Two files with the same one are the same file,
    whatever differed in the markup on the way to it.

    Either way the one handed back has to still *be* that file. A record whose PDF has gone
    from disk, or no longer matches the checksum taken of it, is not an answer to somebody
    asking for their CV, so it is passed over and the press draws a new one.
    """
    if not html and not checksum:
        return None
    filed = exported_on_its_own(cv)
    if html:
        filed = filed.filter(source_text=html)
    if checksum:
        filed = filed.filter(checksum=checksum)
    for document in filed:
        if document.file_is_as_rendered():
            return document
    return None


def snapshot_cv(cv: CV, *, application=None, backend=None) -> RenderedDocument:
    """Freeze a CV as a PDF, exactly as it stands now.

    This is the record of what an employer received. It is never regenerated: months
    later, when someone asks about a line on your CV, you need the version they read.

    **An export with no application is filed once per version** (#236). Pressing *Export
    PDF* twice on a CV nobody has touched used to file two records of the same document;
    the second press is handed the first one now, marked `already_filed` so that whoever
    asked can say so. A render that goes with an application is always its own record,
    however like the last one it is: two employers sent the same CV were sent two things.
    """
    html = render_cv_html(cv)
    if application is None:
        filed = already_exported(cv, html=html)
        if filed is not None:
            filed.already_filed = True
            return filed
    content = html_to_pdf(html, backend=backend)
    checksum = RenderedDocument.checksum_for(content)
    if application is None:
        filed = already_exported(cv, checksum=checksum)
        if filed is not None:
            filed.already_filed = True
            return filed
    # Named for whoever opens it, not for the shelf it was filed on (#223). The variant's
    # name is the person's own filing — "Backend, English" — and the model's help text says
    # so; it was going into the PDF's `/Title`, which a viewer shows in its title bar and a
    # screen reader announces, and into the file name attached to portals and emails.
    language = document_language(cv)
    with languages.override(language):
        title = document_title(cv)

    document = RenderedDocument(
        owner=cv.owner,
        title=title,
        # Written down now rather than read back off the source later: the source can be
        # edited, and is cleared outright when it is deleted, and this is the record of
        # what an employer actually received (#283).
        language=language,
        # What the model says it is, rather than a constant: a portfolio filed as a CV is a
        # document an employment office or a store would then mislabel (#133).
        kind=cv.document_kind,
        source=cv,
        application=application,
        sent_to=sent_to(application),
        source_text=html,
        # The same words with the setting taken off, kept for reading and comparing: two
        # versions of the markup differ in every line when only the theme changed (#236).
        plain_text=cv_text(cv),
        checksum=checksum,
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
        # A report has no source document; the language it is in is the one it was just
        # rendered in, which is the request's (#283).
        language=languages.current(),
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
    language = document_language(letter)
    with languages.override(language):
        title = document_title(letter)

    document = RenderedDocument(
        owner=letter.owner,
        title=title,
        kind=letter.document_kind,
        # Frozen with the document, for the reason `snapshot_cv` gives (#283).
        language=language,
        source=letter,
        application=application,
        sent_to=sent_to(application),
        source_text=letter_text(letter, application),
        checksum=RenderedDocument.checksum_for(content),
    )
    _file_and_save(document, f"{slugify(title) or 'cover-letter'}.pdf", content)
    return document
