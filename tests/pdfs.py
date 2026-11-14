"""A small valid PDF made by hand, so a test needs no renderer to have one (#942).

WeasyPrint wants system libraries a developer's machine may not have, and what these tests
need is a file that PDFium and pdf.js both read: a few pages, each with a line of text in a
standard font, and a correct cross-reference table.
"""

from __future__ import annotations


def make_pdf(pages: int = 2, text: str = "Words of page {n}") -> bytes:
    """A PDF of ``pages`` A4 pages, each saying ``text`` with ``{n}`` as its number."""
    objects: list[bytes] = []
    # 1 catalogue, 2 page tree, 3 font, then a page and its content for each page.
    kids = " ".join(f"{4 + 2 * n} 0 R" for n in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for n in range(pages):
        content = f"BT /F1 24 Tf 72 700 Td ({text.format(n=n + 1)}) Tj ET".encode()
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * n)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return bytes(out)
