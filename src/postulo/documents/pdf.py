"""Turning HTML into PDF.

Two backends, because the obvious choice is different on a server and on a laptop:

``weasyprint``
    Pure Python, small, and excellent at paged CSS. It needs GTK, which makes it a poor
    default on Windows and an easy one inside a container.

``chromium``
    Playwright driving headless Chromium. A heavier dependency, but it prints what a
    browser would, and it installs without ceremony everywhere.

Neither is a hard dependency. Postulo is perfectly usable without PDF export — you can
still track applications and write letters — so a missing backend produces a clear
message rather than an import error at start-up.
"""

from __future__ import annotations

import importlib.util
from typing import Protocol

from django.conf import settings
from django.utils.translation import gettext_lazy as _

#: A4 with margins wide enough that nothing is lost to a printer's unprintable edge.
PAGE_FORMAT = "A4"
PAGE_MARGIN = "18mm"


class PDFBackendUnavailable(RuntimeError):
    """Raised when no PDF backend is installed, or the named one is missing."""


class PDFBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def render(self, html: str) -> bytes: ...


class WeasyPrintBackend:
    """Render with WeasyPrint. Preferred where its native dependencies are present."""

    name = "weasyprint"

    def is_available(self) -> bool:
        return importlib.util.find_spec("weasyprint") is not None

    def render(self, html: str) -> bytes:
        from weasyprint import HTML  # imported late: an optional dependency

        return HTML(string=html).write_pdf()


class ChromiumBackend:
    """Render with headless Chromium through Playwright."""

    name = "chromium"

    def is_available(self) -> bool:
        return importlib.util.find_spec("playwright") is not None

    def render(self, html: str) -> bytes:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                # The document is self-contained: themes inline their CSS, so nothing
                # is fetched and the renderer never reaches the network or the disk.
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


#: Tried in this order when the backend is set to "auto".
BACKENDS: tuple[type[PDFBackend], ...] = (WeasyPrintBackend, ChromiumBackend)


def get_pdf_backend(name: str | None = None) -> PDFBackend:
    """Return a usable backend, or explain why there is not one.

    ``POSTULO_PDF_BACKEND`` may name one explicitly; the default, ``auto``, takes the
    first that is installed.
    """
    requested = (name or getattr(settings, "POSTULO_PDF_BACKEND", "auto") or "auto").lower()

    if requested != "auto":
        for backend_class in BACKENDS:
            backend = backend_class()
            if backend.name == requested:
                if not backend.is_available():
                    raise PDFBackendUnavailable(
                        str(
                            _(
                                "The %(name)s PDF backend is configured but not installed. "
                                "Install it with: uv sync --extra %(name)s"
                            )
                            % {"name": backend.name}
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
                "No PDF backend is installed, so documents cannot be exported. "
                "Install one with: uv sync --extra weasyprint (Linux) or "
                "uv sync --extra chromium (any platform, then: playwright install chromium)."
            )
        )
    )


def html_to_pdf(html: str, *, backend: PDFBackend | None = None) -> bytes:
    """Render a complete HTML document to PDF bytes."""
    return (backend or get_pdf_backend()).render(html)
