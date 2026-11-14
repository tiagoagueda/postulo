"""Draw the first page of a PDF as a WebP, in a process of its own (#942).

``python -I -m postulo.documents.thumbnailer WIDTH HEIGHT`` reads a PDF on standard input and
writes a WebP to standard output, or exits non-zero and writes nothing. It is a separate
process because PDFium is a parser on bytes somebody else wrote: `previews` starts it under a
time limit, and it limits itself where the system lets it -- address space and processor
time -- before it reads a byte. It imports nothing from Django and nothing from Postulo.
"""

from __future__ import annotations

import sys

#: What the process may allocate and how long it may compute, where `resource` exists.
MEMORY_LIMIT = 1024 * 1024 * 1024
CPU_SECONDS = 30
MAX_PDF_BYTES = 40 * 1024 * 1024


def limit() -> None:
    try:
        import resource
    except ImportError:  # Windows has none; the caller's time limit still holds
        return
    for which, value in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_CPU, CPU_SECONDS)):
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass


def main(argv: list[str]) -> int:
    limit()
    try:
        width, height = int(argv[1]), int(argv[2])
        import pypdfium2 as pdfium

        data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            return 3
        document = pdfium.PdfDocument(data)
        if len(document) < 1:
            return 4
        page = document[0]
        page_width, page_height = page.get_size()
        if not (page_width > 0 and page_height > 0):
            return 5
        scale = min(width / page_width, height / page_height, 4.0)
        image = page.render(scale=scale).to_pil().convert("RGB")
        image.save(sys.stdout.buffer, format="WEBP", quality=80)
        return 0
    except Exception:  # a broken, locked or hostile file is all one answer: no thumbnail
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
