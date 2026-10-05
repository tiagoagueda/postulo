"""An OpenDocument text file, written with the standard library (#480).

OpenDocument Text (`.odt`, ISO/IEC 26300) is the open standard for the job Word's format
does, and public bodies in several countries ask for it by name. Like a `.docx` it is a zip
of XML, and a document made of headings, paragraphs and lists needs five small parts, so it
is written from the same `Outline` and with the same restraint: the words, not the page.

**What is in the package, and why each part is there.**

``mimetype``
    The first entry, stored and not compressed, holding nothing but the media type. It is how
    a file manager and `file(1)` tell an `.odt` from any other zip without opening it, which
    is what the standard requires of the package.
``META-INF/manifest.xml``
    What every other part is. A reader refuses a package without it.
``meta.xml``
    The title, the subject, the keywords, the author and the language: the properties
    (`properties.py`), written the way OpenDocument writes them. No creation date and no
    generator, so a document handed to an employer does not say when it was made or by what.
``styles.xml``
    What *Heading 1* means, what a bullet is, the page, and the language every paragraph
    inherits -- which is what a spell checker and a screen reader pronounce by.
``content.xml``
    The words, as `text:h` for a heading (with its outline level, which is how a navigator
    finds a document's structure), `text:p` for a paragraph and `text:list` for a list.

**The same input is the same file, byte for byte**: every entry carries one fixed date.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from postulo.core import languages

from .docx import DECLARATION, EPOCH, clean
from .outline import BULLETS, HEADING, Outline

CONTENT_TYPE = "application/vnd.oasis.opendocument.text"

VERSION = "1.2"

NAMESPACES = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"'
)

#: The deepest heading a style is written for, as in the Word file.
DEEPEST_HEADING = 3

#: A4, with the margin the PDF has (`pdf.PAGE_MARGIN`, 18 mm).
PAGE_MARGIN = "18mm"

#: Points, and the space above and below in millimetres, per heading level.
HEADINGS = {1: (18, "0mm", "2.1mm"), 2: (13, "4.9mm", "2.8mm"), 3: (11, "2.8mm", "0.7mm")}

MANIFEST = (
    DECLARATION + f'<manifest:manifest {NAMESPACES} manifest:version="{VERSION}">'
    f'<manifest:file-entry manifest:full-path="/" manifest:version="{VERSION}" '
    f'manifest:media-type="{CONTENT_TYPE}"/>'
    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
    "</manifest:manifest>"
)

#: Two or more spaces in a row, and one at the start of a line: OpenDocument collapses
#: white space the way HTML does, and says a space that is meant with `text:s`.
SPACES = re.compile(r"(^ | {2,})")


def _spaces(match: re.Match) -> str:
    return f'<text:s text:c="{len(match.group(0))}"/>'


def _text(text: str) -> str:
    """What a person typed, as the content of a paragraph: escaped, with its breaks kept."""
    lines = clean(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "<text:line-break/>".join(SPACES.sub(_spaces, escape(line)) for line in lines)


def _paragraph(text: str, style: str = "Standard") -> str:
    return f'<text:p text:style-name="{style}">{_text(text)}</text:p>'


def _body(outline: Outline) -> str:
    parts: list[str] = []
    for block in outline.blocks:
        if block.kind == HEADING:
            if block.text.strip():
                level = min(max(block.level, 1), DEEPEST_HEADING)
                parts.append(
                    f'<text:h text:style-name="Heading_20_{level}" text:outline-level="{level}">'
                    f"{_text(block.text.strip())}</text:h>"
                )
        elif block.kind == BULLETS:
            items = [item.strip() for item in block.items if item.strip()]
            if items:
                parts.append(
                    '<text:list text:style-name="Bullets">'
                    + "".join(
                        f"<text:list-item>{_paragraph(item, 'List_20_Paragraph')}</text:list-item>"
                        for item in items
                    )
                    + "</text:list>"
                )
        elif block.text.strip():
            parts.append(_paragraph(block.text.strip()))
    return "".join(parts)


def _content(outline: Outline) -> str:
    return (
        DECLARATION
        + f'<office:document-content {NAMESPACES} office:version="{VERSION}">'
        + f"<office:body><office:text>{_body(outline)}</office:text></office:body>"
        + "</office:document-content>"
    )


def _language(outline: Outline) -> str:
    """The default language of the text, as OpenDocument says one: a language, maybe a script
    and a country, each its own attribute.

    A right-to-left script is a *complex* one to OpenDocument, which names its language in
    attributes of their own beside the Latin ones, as Word does.
    """
    tag = languages.tag(clean(outline.language))
    if not tag:
        return ""
    language, *rest = tag.split("-")
    script = next((part for part in rest if len(part) == 4 and part.isalpha()), "")
    country = next(
        (part for part in rest if (len(part) == 2 and part.isalpha()) or len(part) == 3), ""
    )
    suffix = "-complex" if outline.direction == "rtl" else ""
    if suffix:
        attributes = f"style:language{suffix}={quoteattr(language)}"
        if country:
            attributes += f" style:country{suffix}={quoteattr(country)}"
        return attributes
    attributes = f"fo:language={quoteattr(language)}"
    if country:
        attributes += f" fo:country={quoteattr(country)}"
    if script:
        attributes += f" fo:script={quoteattr(script)}"
    return attributes


def _styles(outline: Outline) -> str:
    rtl = outline.direction == "rtl"
    direction = ' style:writing-mode="rl-tb"' if rtl else ""
    headings = "".join(
        f'<style:style style:name="Heading_20_{level}" style:display-name="Heading {level}" '
        'style:family="paragraph" style:parent-style-name="Standard" '
        'style:next-style-name="Standard" style:default-outline-level="' + str(level) + '">'
        f'<style:paragraph-properties fo:margin-top="{before}" fo:margin-bottom="{after}" '
        'fo:keep-with-next="always"/>'
        f'<style:text-properties fo:font-size="{size}pt" fo:font-weight="bold" '
        f'style:font-size-complex="{size}pt" style:font-weight-complex="bold"/>'
        "</style:style>"
        for level, (size, before, after) in HEADINGS.items()
    )
    return (
        DECLARATION + f'<office:document-styles {NAMESPACES} office:version="{VERSION}">'
        "<office:styles>"
        '<style:default-style style:family="paragraph">'
        f'<style:paragraph-properties fo:margin-bottom="1mm"{direction}/>'
        '<style:text-properties fo:font-family="Arial" fo:font-size="10.5pt" '
        f'style:font-size-complex="10.5pt" {_language(outline)}/>'
        "</style:default-style>"
        '<style:style style:name="Standard" style:family="paragraph" style:class="text"/>'
        f"{headings}"
        '<style:style style:name="List_20_Paragraph" style:display-name="List Paragraph" '
        'style:family="paragraph" style:parent-style-name="Standard">'
        '<style:paragraph-properties fo:margin-bottom="0.3mm"/>'
        "</style:style>"
        '<text:list-style style:name="Bullets">'
        '<text:list-level-style-bullet text:level="1" text:bullet-char="•">'
        '<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
        '<style:list-level-label-alignment text:label-followed-by="listtab" '
        'text:list-tab-stop-position="6.35mm" fo:text-indent="-6.35mm" fo:margin-start="6.35mm"/>'
        "</style:list-level-properties>"
        "</text:list-level-style-bullet>"
        "</text:list-style>"
        "</office:styles>"
        "<office:automatic-styles>"
        '<style:page-layout style:name="pm1">'
        '<style:page-layout-properties fo:page-width="210mm" fo:page-height="297mm" '
        f'fo:margin-top="{PAGE_MARGIN}" fo:margin-bottom="{PAGE_MARGIN}" '
        f'fo:margin-left="{PAGE_MARGIN}" fo:margin-right="{PAGE_MARGIN}"'
        f"{direction}/>"
        "</style:page-layout>"
        "</office:automatic-styles>"
        "<office:master-styles>"
        '<style:master-page style:name="Standard" style:page-layout-name="pm1"/>'
        "</office:master-styles>"
        "</office:document-styles>"
    )


def _keywords(text: str) -> list[str]:
    return [word.strip() for word in re.split(r"[,;\n]", clean(text)) if word.strip()]


def _meta(outline: Outline) -> str:
    """The file's own properties. No dates and no generator: see the note at the top."""
    fields = [f"<dc:title>{escape(clean(outline.title))}</dc:title>"]
    if outline.subject:
        fields.append(f"<dc:description>{escape(clean(outline.subject))}</dc:description>")
        fields.append(f"<dc:subject>{escape(clean(outline.subject))}</dc:subject>")
    fields.extend(
        f"<meta:keyword>{escape(word)}</meta:keyword>" for word in _keywords(outline.keywords)
    )
    if outline.author:
        fields.append(f"<dc:creator>{escape(clean(outline.author))}</dc:creator>")
        fields.append(
            f"<meta:initial-creator>{escape(clean(outline.author))}</meta:initial-creator>"
        )
    tag = languages.tag(clean(outline.language))
    if tag:
        fields.append(f"<dc:language>{escape(tag)}</dc:language>")
    return (
        DECLARATION
        + f'<office:document-meta {NAMESPACES} office:version="{VERSION}">'
        + f"<office:meta>{''.join(fields)}</office:meta></office:document-meta>"
    )


def parts_of(outline: Outline) -> dict[str, str]:
    """Every part but ``mimetype``, by the name it is filed under, in the order written."""
    return {
        "META-INF/manifest.xml": MANIFEST,
        "meta.xml": _meta(outline),
        "styles.xml": _styles(outline),
        "content.xml": _content(outline),
    }


def write(outline: Outline) -> bytes:
    """The outline as an `.odt`."""
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", zipfile.ZIP_DEFLATED) as package:
        first = zipfile.ZipInfo("mimetype", date_time=EPOCH)
        first.compress_type = zipfile.ZIP_STORED
        first.create_system = 0
        first.external_attr = 0
        package.writestr(first, CONTENT_TYPE.encode("ascii"))
        for name, content in parts_of(outline).items():
            entry = zipfile.ZipInfo(name, date_time=EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 0
            entry.external_attr = 0
            package.writestr(entry, content.encode("utf-8"))
    return held.getvalue()
