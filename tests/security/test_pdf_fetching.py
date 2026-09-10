"""What a PDF renderer may fetch while it draws: nothing the document does not carry (#163).

A rendered document is somebody's CV on its way to an employer. If the renderer follows an
address in it, what it finds is drawn into the PDF: a ``file:`` address reads the server's
disk, an ``http:`` one reaches whatever the server can reach. CVE-2026-55073 was WeasyPrint's
own version of this -- two ``write_pdf`` arguments that ignored the document's fetcher -- and
70.0 fixed it. Postulo passes neither argument, and was not exposed that way.

But it set no fetcher at all, so the default one applied: every protocol, redirects followed.
That was harmless only because every template Postulo ships inlines its CSS and embeds what
it shows. These tests hold it to that by enforcement rather than by the way the templates
happen to be written, which starts to matter once a theme comes from a plugin (#132).

The ones that draw need Pango, or a browser Playwright can launch, and skip where there is
none. CI's test job has Pango, so the WeasyPrint half runs there.

They skip on Pango itself rather than on whether WeasyPrint imports. The suite treats a
warning as an error, WeasyPrint 70 warns at import when HarfBuzz-Subset is missing, and
asking it whether it imports would have skipped these in silence on the very machine meant to
run them -- which is what happened the first time they ran on Linux.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import threading
from ctypes.util import find_library

import pytest

from postulo.documents.pdf import ChromiumBackend, WeasyPrintBackend

needs_weasyprint = pytest.mark.skipif(
    find_library("pango-1.0") is None,
    reason="Pango is not installed here, and WeasyPrint draws with it",
)

#: One transparent pixel, as a document would embed an image it carries.
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@needs_weasyprint
def test_the_fetcher_takes_data_and_refuses_every_other_address(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("not for a PDF", encoding="utf-8")
    fetcher = WeasyPrintBackend.fetcher()

    # Closed, as WeasyPrint closes what it fetches: a urllib response is a temporary file
    # wrapper, and Python 3.14 warns about one collected open.
    with contextlib.closing(fetcher("data:text/plain,hello")) as response:
        assert response.read() == b"hello"
    for address in (
        secret.as_uri(),
        "http://127.0.0.1:9/",
        "https://example.org/",
        "ftp://example.org/file",
    ):
        with pytest.raises(ValueError, match="disallowed protocol"):
            fetcher(address)


@needs_weasyprint
def test_a_stylesheet_on_the_disk_is_not_read_into_a_document(tmp_path):
    """The advisory, as a document: it asks for a file on the server and gets nothing.

    The stylesheet would make the page a 100-millimetre square, so the page's size says
    whether it was read. The document is the one `render` writes, fetcher included.
    """
    sheet = tmp_path / "page.css"
    sheet.write_text("@page { size: 100mm 100mm }", encoding="utf-8")
    html = (
        f'<html><head><link rel="stylesheet" href="{sheet.as_uri()}"></head>'
        "<body><p>A CV.</p></body></html>"
    )

    page = WeasyPrintBackend().document(html).render().pages[0]

    # 210 millimetres in CSS pixels: A4, as if the stylesheet were not there.
    assert round(page.width) == 794


@needs_weasyprint
def test_what_a_document_embeds_still_draws():
    document = WeasyPrintBackend().document(f'<html><body><img src="{PIXEL}"></body></html>')

    assert document.render().pages, "the document drew"


# --------------------------------------------------------------------------- chromium


@functools.cache
def _chromium_launches() -> bool:
    """Whether a browser can actually be started, not only whether Playwright is installed.

    CI's test job installs Playwright with the development groups and no browser, so
    `is_available` says yes and the launch would fail.
    """
    if not ChromiumBackend().is_available():
        return False
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
    except Exception:  # a missing browser binary, or a sandbox that refuses to start one
        return False
    return True


@pytest.fixture
def listener():
    """A server on this machine that notes every request it is sent."""
    heard: list[str] = []

    class Note(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            heard.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Note)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", heard
    finally:
        server.shutdown()
        server.server_close()


def test_chromium_asks_the_network_for_nothing(listener):
    if not _chromium_launches():
        pytest.skip("no browser Playwright can launch here")
    address, heard = listener
    html = (
        f'<html><head><link rel="stylesheet" href="{address}/sheet.css"></head>'
        f'<body><img src="{address}/pixel.png"><p>A CV.</p></body></html>'
    )

    pdf = ChromiumBackend().render(html)

    assert pdf.startswith(b"%PDF-")
    assert heard == [], f"the renderer asked for {heard}"


def test_chromium_still_draws_what_a_document_embeds():
    if not _chromium_launches():
        pytest.skip("no browser Playwright can launch here")

    pdf = ChromiumBackend().render(f'<html><body><img src="{PIXEL}"></body></html>')

    assert b"/Subtype /Image" in pdf
