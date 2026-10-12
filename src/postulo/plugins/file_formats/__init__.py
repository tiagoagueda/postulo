"""Extend file formats: the module whose sub-modules each add a format to import and export.

PDF, Word, OpenDocument, iCalendar, vCard and Europass are the formats Postulo reads or writes
besides its own JSON, and they were six unrelated things in six places. They are one module
now, **Extend file formats**, with one sub-module per format (#877), so an administrator can
say which formats an instance offers and a person sees only those.

**These are grouping rows and declarations; the work is elsewhere.** What a format does lives
where it always did -- ``documents.formats`` for the Word and OpenDocument writers,
``applications.ical`` for iCalendar, ``core.vcard`` for vCard, the Europass importer in
``plugins.europass``. What these rows add is the answer to *is it offered to this person*,
which ``core.modules`` asks at each place a format leaves or arrives: the Documents export
menu, the Import and Export buttons, the downloads and feeds, which answer 404 while their
module is off and come back at the same address when it is on.

**PDF is always on.** It is the format a CV is made in, and there is nothing to fall back to
that would still be a CV. Its switch is drawn on and disabled, and it stays on whatever the
module above it says.

Europass is the importer that has shipped since #129; it names this module as its parent
and is not declared again here. References (#879) and GeoJSON (#880) will join as
sub-modules of their own when they exist.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on, and what each format names as its parent.
FILE_FORMATS = "file-formats"

PDF = "format-pdf"
DOCX = "format-docx"
ODT = "format-odt"
ICALENDAR = "format-icalendar"
VCARD = "format-vcard"


@declares(
    shipped(
        name=FILE_FORMATS,
        label=_("Extend file formats"),
        kind="feature",
        categories=("files",),
        icon="files",
        description=_(
            "The file formats Postulo can read and write besides its own: PDF, Word, "
            "OpenDocument, iCalendar, vCard and Europass. This is the switch for all of "
            "them at once; each can still be switched off on its own while this is on, "
            "and PDF cannot be. Switched off, nothing is offered in those formats and "
            "nothing is deleted: the files you have, and the data they were made from, stay."
        ),
    )
)
class FileFormatsFeature:
    """The module the format sub-modules belong to. It reads and writes nothing itself."""


@declares(
    shipped(
        name=PDF,
        label=_("PDF"),
        kind="feature",
        parent=FILE_FORMATS,
        categories=("files",),
        icon="file-text",
        required=True,
        description=_(
            "The format a CV and a letter are made in: tagged, so a screen reader can read "
            "it. Always on; Postulo has nothing else that would still be a CV."
        ),
    )
)
class PdfFormat:
    """PDF, always on."""


@declares(
    shipped(
        name=DOCX,
        label=_("DOCX"),
        kind="feature",
        parent=FILE_FORMATS,
        categories=("files",),
        icon="file-type",
        description=_(
            "Word documents: a CV or a letter downloaded as a .docx file, and the text of "
            "a .docx you upload kept so that a search can find it. Switched off, the "
            "download is not offered and an uploaded file's text is not read."
        ),
    )
)
class DocxFormat:
    """Word, on or off."""


@declares(
    shipped(
        name=ODT,
        label=_("ODT"),
        kind="feature",
        parent=FILE_FORMATS,
        categories=("files",),
        icon="file-type-2",
        description=_(
            "OpenDocument text: a CV or a letter downloaded as an .odt file, and the text "
            "of an .odt you upload kept so that a search can find it. Switched off, the "
            "download is not offered and an uploaded file's text is not read."
        ),
    )
)
class OdtFormat:
    """OpenDocument text, on or off."""


@declares(
    shipped(
        name=ICALENDAR,
        label=_("iCalendar"),
        kind="feature",
        parent=FILE_FORMATS,
        categories=("calendar", "files"),
        icon="calendar-days",
        description=_(
            "Your calendar as .ics files and feeds, and a calendar file read in. Switched "
            "off, the downloads, the import and the feeds are not offered and their "
            "addresses answer as if there were nothing there; they answer again, at the same "
            "address, when it is switched on."
        ),
    )
)
class ICalendarFormat:
    """iCalendar, on or off."""


@declares(
    shipped(
        name=VCARD,
        label=_("vCard"),
        kind="feature",
        parent=FILE_FORMATS,
        categories=("contacts", "files"),
        icon="contact-round",
        description=_(
            "Contacts, companies and your own details as .vcf cards, and a card file read "
            "in. Switched off, the downloads and the import are not offered and their "
            "addresses answer as if there were nothing there."
        ),
    )
)
class VCardFormat:
    """vCard, on or off."""
