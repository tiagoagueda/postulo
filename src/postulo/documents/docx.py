"""A Word document, written with the standard library (#236).

A `.docx` is a zip of XML parts, and a document made of headings, paragraphs and lists needs
seven small ones. `python-docx` would write the same seven and bring `lxml` with it, a
compiled dependency for every instance whether or not anybody ever asks for a Word file; the
core stays light, and this stays short enough to read.

**What is in the package, and why each part is there.**

``[Content_Types].xml``
    What every other part is. Word refuses a package without it, and reads it first, so it
    is written first.
``_rels/.rels``
    Which part is the document. Without it the package is a zip of files nobody asked for.
``word/document.xml``
    The words.
``word/styles.xml``
    What *Heading 1* means. A heading that is only bold text is not one: the navigation
    pane, a screen reader and an applicant tracking system all find a document's structure
    by the style's name and its outline level, which is the same argument `base_cv.html`
    makes for `<h3>` over a bold paragraph (#235).
``word/numbering.xml``
    What a bullet is. A list that is paragraphs beginning with a dash is read as paragraphs.
``word/_rels/document.xml.rels``
    Where the document finds the two above.
``docProps/core.xml``
    The title, the author and the language, which is what a viewer announces for the file.

**The same input is the same file, byte for byte.** Every entry carries one fixed date, so
nothing in the package says when it was made -- a document somebody hands to an employer
has no reason to carry the minute it was downloaded, and a test can compare two of them.

**Text is what a person typed, so it is cleaned as well as escaped.** XML 1.0 forbids most
control characters outright, escaped or not, and one vertical tab pasted in from a PDF would
make a file Word reports as corrupt. They are dropped; everything else is kept as written.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from postulo.core import languages

from .outline import BULLETS, HEADING, Outline

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: The date every entry in the package carries: the earliest one a zip can hold.
EPOCH = (1980, 1, 1, 0, 0, 0)

DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PACKAGE = "http://schemas.openxmlformats.org/package/2006"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

#: The deepest heading a style is written for. An outline asking for a deeper one gets this.
DEEPEST_HEADING = 3

#: A4, in twentieths of a point, with the margin the PDF has (`pdf.PAGE_MARGIN`, 18 mm).
PAGE_WIDTH = 11906
PAGE_HEIGHT = 16838
PAGE_MARGIN = 1021

#: Everything XML 1.0 will not carry, escaped or otherwise.
UNWRITABLE = re.compile("[^\t\n\r -퟿-�\U00010000-\U0010ffff]")

CONTENT_TYPES = (
    DECLARATION + f'<Types xmlns="{PACKAGE}/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{CONTENT_TYPE}.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)

PACKAGE_RELATIONSHIPS = (
    DECLARATION + f'<Relationships xmlns="{PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{OFFICE}/officeDocument" Target="word/document.xml"/>'
    f'<Relationship Id="rId2" Type="{PACKAGE}/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    "</Relationships>"
)

DOCUMENT_RELATIONSHIPS = (
    DECLARATION + f'<Relationships xmlns="{PACKAGE}/relationships">'
    f'<Relationship Id="rId1" Type="{OFFICE}/styles" Target="styles.xml"/>'
    f'<Relationship Id="rId2" Type="{OFFICE}/numbering" Target="numbering.xml"/>'
    "</Relationships>"
)

#: One list, one level, a real bullet character in the document's own font. The usual
#: recipe names the Symbol font and a private-use code point, which is a bullet only on a
#: machine that has Symbol.
NUMBERING = (
    DECLARATION + f'<w:numbering xmlns:w="{WORD}">'
    '<w:abstractNum w:abstractNumId="0">'
    '<w:multiLevelType w:val="singleLevel"/>'
    '<w:lvl w:ilvl="0">'
    '<w:start w:val="1"/>'
    '<w:numFmt w:val="bullet"/>'
    '<w:lvlText w:val="•"/>'
    '<w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr>'
    "</w:lvl>"
    "</w:abstractNum>"
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    "</w:numbering>"
)

#: Half-points, and the space above and below in twentieths of a point, per heading level.
HEADINGS = {1: (36, 0, 60), 2: (26, 280, 80), 3: (22, 160, 20)}


def clean(text: str) -> str:
    """What a person typed, without what XML cannot carry."""
    return UNWRITABLE.sub("", text or "").replace("\t", " ")


def _run(text: str, *, rtl: bool) -> str:
    """One run of text. A line break inside it is a break, not the end of the paragraph."""
    lines = clean(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    properties = "<w:rPr><w:rtl/></w:rPr>" if rtl else ""
    written = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in lines)
    return f"<w:r>{properties}{written}</w:r>"


def _paragraph(text: str, *, style: str = "", listed: bool = False, rtl: bool = False) -> str:
    # In the order the schema asks for them, which Word holds a document to: the style,
    # the list it belongs to, and then the direction.
    properties = []
    if style:
        properties.append(f'<w:pStyle w:val="{style}"/>')
    if listed:
        properties.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    if rtl:
        properties.append("<w:bidi/>")
    inside = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
    return f"<w:p>{inside}{_run(text, rtl=rtl)}</w:p>"


def _document(outline: Outline) -> str:
    rtl = outline.direction == "rtl"
    paragraphs: list[str] = []
    for block in outline.blocks:
        if block.kind == HEADING:
            if block.text.strip():
                level = min(max(block.level, 1), DEEPEST_HEADING)
                paragraphs.append(_paragraph(block.text.strip(), style=f"Heading{level}", rtl=rtl))
        elif block.kind == BULLETS:
            paragraphs.extend(
                _paragraph(item.strip(), style="ListParagraph", listed=True, rtl=rtl)
                for item in block.items
                if item.strip()
            )
        elif block.text.strip():
            paragraphs.append(_paragraph(block.text.strip(), rtl=rtl))
    section = (
        "<w:sectPr>"
        f'<w:pgSz w:w="{PAGE_WIDTH}" w:h="{PAGE_HEIGHT}"/>'
        f'<w:pgMar w:top="{PAGE_MARGIN}" w:right="{PAGE_MARGIN}" w:bottom="{PAGE_MARGIN}" '
        f'w:left="{PAGE_MARGIN}" w:header="709" w:footer="709" w:gutter="0"/>'
        f"{'<w:bidi/>' if rtl else ''}"
        "</w:sectPr>"
    )
    return (
        DECLARATION
        + f'<w:document xmlns:w="{WORD}"><w:body>{"".join(paragraphs)}{section}</w:body>'
        + "</w:document>"
    )


def _styles(outline: Outline) -> str:
    """The styles the document names: the body, three headings and a list item.

    The language goes here, once, as the default every run inherits. It is what Word
    spell-checks by and what a screen reader pronounces by, so a French CV declaring
    nothing is read with whatever the reader's own Word happens to assume.
    """
    tag = languages.tag(clean(outline.language))
    language = ""
    if tag:
        # A right-to-left script is a *complex script* to Word, and has a language of its
        # own beside the Latin one.
        attribute = "w:bidi" if outline.direction == "rtl" else "w:val"
        language = f"<w:lang {attribute}={quoteattr(tag)}/>"
    headings = "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
        f'<w:name w:val="heading {level}"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:uiPriority w:val="9"/><w:qFormat/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="{before}" w:after="{after}"/>'
        f'<w:outlineLvl w:val="{level - 1}"/></w:pPr>'
        f'<w:rPr><w:b/><w:bCs/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        "</w:style>"
        for level, (size, before, after) in HEADINGS.items()
    )
    return (
        DECLARATION + f'<w:styles xmlns:w="{WORD}">'
        "<w:docDefaults>"
        "<w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
        f'<w:sz w:val="21"/><w:szCs w:val="21"/>{language}'
        "</w:rPr></w:rPrDefault>"
        '<w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="264" w:lineRule="auto"/>'
        "</w:pPr></w:pPrDefault>"
        "</w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/>'
        "</w:style>"
        f"{headings}"
        '<w:style w:type="paragraph" w:styleId="ListParagraph">'
        '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
        '<w:uiPriority w:val="34"/><w:qFormat/>'
        '<w:pPr><w:spacing w:after="20"/><w:contextualSpacing/></w:pPr>'
        "</w:style>"
        "</w:styles>"
    )


def _properties(outline: Outline) -> str:
    """The file's own properties. No dates: see the note on `EPOCH`."""
    fields = [f"<dc:title>{escape(clean(outline.title))}</dc:title>"]
    if outline.author:
        fields.append(f"<dc:creator>{escape(clean(outline.author))}</dc:creator>")
    tag = languages.tag(clean(outline.language))
    if tag:
        fields.append(f"<dc:language>{escape(tag)}</dc:language>")
    return (
        DECLARATION
        + f'<cp:coreProperties xmlns:cp="{PACKAGE}/metadata/core-properties" '
        + 'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + "".join(fields)
        + "</cp:coreProperties>"
    )


def parts_of(outline: Outline) -> dict[str, str]:
    """Every part of the package, by the name it is filed under, in the order written."""
    return {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": PACKAGE_RELATIONSHIPS,
        "docProps/core.xml": _properties(outline),
        "word/document.xml": _document(outline),
        "word/styles.xml": _styles(outline),
        "word/numbering.xml": NUMBERING,
        "word/_rels/document.xml.rels": DOCUMENT_RELATIONSHIPS,
    }


def write(outline: Outline) -> bytes:
    """The outline as a `.docx`."""
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in parts_of(outline).items():
            entry = zipfile.ZipInfo(name, date_time=EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            # The same on every machine: `ZipInfo` otherwise records which operating
            # system wrote the entry, and the same document would differ between two.
            entry.create_system = 0
            entry.external_attr = 0
            package.writestr(entry, content.encode("utf-8"))
    return held.getvalue()
