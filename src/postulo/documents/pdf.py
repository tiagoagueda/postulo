"""Turning HTML into PDF.

**WeasyPrint is the default.** It is a small Python dependency, it is excellent at paged
CSS, and it produces the smaller and more faithful document of the two. It is installed
with Postulo and needs no extra step on a server.

What it does need is Pango and its companion system libraries. Those are a package
manager away on Linux and inside a container, and a genuine nuisance on Windows — which
is why a second backend exists:

``chromium``
    Playwright driving headless Chromium. Heavier, and it prints what a browser would.
    Optional, and worth installing on a machine where WeasyPrint's system libraries are
    not practical.

Neither is required to *use* Postulo. Tracking applications and writing letters work
perfectly well with no renderer at all, so a backend that cannot be used produces a clear
message rather than an error at start-up.
"""

from __future__ import annotations

import functools
import importlib
from typing import Protocol

from django.conf import settings
from django.utils.translation import gettext_lazy as _

#: A4 with margins wide enough that nothing is lost to a printer's unprintable edge.
PAGE_FORMAT = "A4"
PAGE_MARGIN = "18mm"

WEASYPRINT_HINT = _(
    "WeasyPrint is installed with Postulo, but it needs Pango and its system libraries. "
    "On Debian or Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0. On Windows they "
    "are awkward to obtain, so use the chromium backend instead. "
    "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
)

CHROMIUM_HINT = _(
    "Install it with: uv sync --extra chromium, then: uv run playwright install chromium"
)


class PDFBackendUnavailable(RuntimeError):
    """Raised when no PDF backend is usable, or the named one is not."""


@functools.cache
def _is_importable(module: str) -> bool:
    """Whether ``module`` can actually be imported.

    Checking that a package is *installed* is not enough. WeasyPrint is a Python package
    that loads Pango and its friends through the system linker, so on a machine without
    those libraries it is present, findable, and completely unusable — importing it
    raises OSError, not ImportError. Asking the import system to do the work is the only
    honest answer, and the result is cached because importing WeasyPrint is not cheap.
    """
    try:
        importlib.import_module(module)
    except Exception:
        # Deliberately broad: ImportError when the package is absent, OSError when its
        # native libraries are, and whatever else a C dependency decides to raise on the
        # way up. Any of them means the same thing here.
        return False
    return True


class PDFBackend(Protocol):
    name: str
    install_hint: str

    def is_available(self) -> bool: ...

    def render(self, html: str) -> bytes: ...


class WeasyPrintBackend:
    """Render with WeasyPrint. The default, and preferred wherever it will run."""

    name = "weasyprint"
    install_hint = WEASYPRINT_HINT

    def is_available(self) -> bool:
        return _is_importable("weasyprint")

    def render(self, html: str) -> bytes:
        # Nothing but the document goes to `write_pdf`. Its `stylesheets` and `xmp_metadata`
        # arguments were the two that ignored the document's fetcher and built one of their
        # own until WeasyPrint 70 (CVE-2026-55073); Postulo passes neither (#163).
        return self.document(html).write_pdf()

    def document(self, html: str):
        """The WeasyPrint document `render` writes, holding the only fetcher it may use."""
        from weasyprint import HTML  # imported late: needs system libraries

        return HTML(string=html, url_fetcher=self.fetcher())

    @staticmethod
    def fetcher():
        """What WeasyPrint may fetch while it draws: ``data:`` addresses, and nothing else.

        Every document Postulo renders carries its own CSS and embeds whatever it shows, so
        there is nothing to fetch -- and until this, that was true only because the templates
        happened to be written that way. WeasyPrint's own fetcher opens ``file:``, ``http:``,
        ``https:`` and ``ftp:`` addresses and follows redirects, and a theme a plugin ships
        (#132) is markup Postulo did not write. With this, an ``<img>``, a ``<link>`` or a
        ``url()`` pointing at ``file:///etc/passwd`` or at an address inside the network draws
        nothing, instead of drawing what it found into a PDF somebody downloads (#163).
        """
        from weasyprint.urls import URLFetcher  # imported late, as above

        return URLFetcher(allowed_protocols={"data"}, allow_redirects=False)


class ChromiumBackend:
    """Render with headless Chromium through Playwright. The fallback."""

    name = "chromium"
    install_hint = CHROMIUM_HINT

    def is_available(self) -> bool:
        return _is_importable("playwright")

    def render(self, html: str) -> bytes:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                # The document is self-contained: themes inline their CSS, so there is
                # nothing to fetch. Every request the page makes is refused anyway rather
                # than trusted not to happen -- the counterpart of WeasyPrint's fetcher. A
                # `data:` address is not a request, so what a document embeds still draws
                # (#163).
                page.route("**/*", lambda route: route.abort())
                page.set_content(html, wait_until="load")
                return page.pdf(
                    format=PAGE_FORMAT,
                    print_background=True,
                    margin={
                        "top": PAGE_MARGIN,
                        "bottom": PAGE_MARGIN,
                        "left": PAGE_MARGIN,
                        "right": PAGE_MARGIN,
                    },
                )
            finally:
                browser.close()


#: Tried in this order when the backend is "auto". WeasyPrint comes first because it is
#: the default; Chromium exists for machines where WeasyPrint will not run.
BACKENDS: tuple[type[PDFBackend], ...] = (WeasyPrintBackend, ChromiumBackend)


def get_pdf_backend(name: str | None = None) -> PDFBackend:
    """Return a usable backend, or explain why there is not one.

    ``POSTULO_PDF_BACKEND`` may name one explicitly. The default, ``auto``, takes the
    first that actually works, which is WeasyPrint wherever its system libraries are
    present and Chromium otherwise.
    """
    requested = (name or getattr(settings, "POSTULO_PDF_BACKEND", "auto") or "auto").lower()

    if requested != "auto":
        for backend_class in BACKENDS:
            backend = backend_class()
            if backend.name == requested:
                if not backend.is_available():
                    raise PDFBackendUnavailable(
                        str(
                            _("The %(name)s PDF backend is configured but not usable. %(hint)s")
                            % {"name": backend.name, "hint": backend.install_hint}
                        )
                    )
                return backend
        raise PDFBackendUnavailable(
            str(
                _("Unknown PDF backend %(name)r. Choose from: auto, weasyprint, chromium.")
                % {"name": requested}
            )
        )

    for backend_class in BACKENDS:
        backend = backend_class()
        if backend.is_available():
            return backend

    raise PDFBackendUnavailable(
        str(
            _(
                "No PDF backend is usable, so documents cannot be exported. %(weasyprint)s "
                "Alternatively: %(chromium)s"
            )
            % {"weasyprint": WEASYPRINT_HINT, "chromium": CHROMIUM_HINT}
        )
    )


def html_to_pdf(html: str, *, backend: PDFBackend | None = None) -> bytes:
    """Render a complete HTML document to PDF bytes."""
    return (backend or get_pdf_backend()).render(html)
