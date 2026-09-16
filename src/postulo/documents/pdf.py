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

**A run of documents is one renderer.** ``session`` holds whatever a backend is expensive to
start, so that pressing *Send* with a CV and a letter starts one Chromium rather than two --
it was a browser per document, launched and torn down inside the request, and launching
Chromium is most of what rendering a two-page letter costs (#220).
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib
from collections import OrderedDict
from typing import Protocol

from django.conf import settings
from django.utils.translation import gettext_lazy as _

#: A4 with margins wide enough that nothing is lost to a printer's unprintable edge.
PAGE_FORMAT = "A4"
PAGE_MARGIN = "18mm"

#: How many *draft* renders one worker keeps, and how large one may be to be worth keeping.
#: A draft is a PDF nothing files — the report a GET hands back — so the same address asked
#: for twice is the same bytes drawn twice, and a browser reloading a download does exactly
#: that. Held in the process rather than in Django's cache deliberately: the default cache is
#: a table in the database, and writing half a megabyte into it on a GET would put back on
#: that path the very write this was taken off (#220). A worker restart forgets the lot, which
#: costs one render and is the right trade for something nothing depends on.
DRAFT_CACHE_ENTRIES = 4
DRAFT_CACHE_MAX_BYTES = 4 * 1024 * 1024

WEASYPRINT_HINT = _(
    "WeasyPrint is installed with Postulo, but it needs Pango and its system libraries. "
    "On Debian or Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0. On Windows they "
    "are awkward to obtain, so use the chromium backend instead. "
    "See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
)

CHROMIUM_HINT = _(
    "Install it with: uv sync --extra chromium, then: uv run playwright install chromium"
)

#: What WeasyPrint is asked to write, kept out here so that what a document asks for can be
#: read -- and asserted -- on a machine where WeasyPrint itself will not import (#235).
#:
#: ``pdf/ua-1`` is PDF/UA-1, and in WeasyPrint it means exactly two things: PDF 1.7, and a
#: **tag tree**. Without one a PDF is a picture of a document -- a screen reader has no
#: headings to jump between, and an applicant tracking system reading the file back gets the
#: words in whatever order they were drawn in. The `lang` every document has declared since
#: #67 reaches a reader through that tree and through nothing else, so until this it was a
#: promise made to nobody.
#:
#: **Not PDF/A**, though `pdf/a-3a` is tagged as well and archival besides. PDF/A is a
#: promise that the file will still render identically in fifty years, and it is kept by
#: embedding an ICC output intent and every font the document uses. Postulo cannot make that
#: promise about a theme a plugin ships (#132): the theme names fonts, the fonts are
#: whatever the server happens to have, and a variant that cannot be honoured is worse than
#: one that was never claimed. PDF/UA claims only what this markup can actually deliver.
#:
#: Note what is *not* here: `stylesheets` and `xmp_metadata`, the two `write_pdf` arguments
#: that built their own fetcher until WeasyPrint 70 (CVE-2026-55073). A variant writes its
#: own XMP from the document already in hand and fetches nothing, so #163 still holds.
WEASYPRINT_PDF_OPTIONS = {"pdf_variant": "pdf/ua-1"}

#: The same two things asked of Chromium, which spells them differently. ``outline`` is the
#: bookmark tree, which WeasyPrint writes from the headings without being asked.
CHROMIUM_PDF_OPTIONS = {"tagged": True, "outline": True}


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

    def session(self) -> contextlib.AbstractContextManager[PDFBackend]: ...


class WeasyPrintBackend:
    """Render with WeasyPrint. The default, and preferred wherever it will run."""

    name = "weasyprint"
    install_hint = WEASYPRINT_HINT

    def is_available(self) -> bool:
        return _is_importable("weasyprint")

    @contextlib.contextmanager
    def session(self):
        """Nothing to hold open: WeasyPrint is a library, and a second call costs a second call."""
        yield self

    def render(self, html: str) -> bytes:
        # Nothing but the document and the variant goes to `write_pdf`. Its `stylesheets` and
        # `xmp_metadata` arguments were the two that ignored the document's fetcher and built
        # one of their own until WeasyPrint 70 (CVE-2026-55073); Postulo passes neither (#163).
        return self.document(html).write_pdf(**WEASYPRINT_PDF_OPTIONS)

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
    """Render with headless Chromium through Playwright. The fallback.

    Launching the browser is most of the cost, so ``session`` launches one and hands back a
    backend that draws every document in it. A backend nobody asked for a session still works
    on its own: it opens one for the single document and closes it again, which is what every
    caller got before there was a choice (#220).
    """

    name = "chromium"
    install_hint = CHROMIUM_HINT

    def __init__(self, browser=None) -> None:
        #: A browser a session holds open, or ``None`` for a backend that starts its own.
        self._browser = browser

    def is_available(self) -> bool:
        return _is_importable("playwright")

    @contextlib.contextmanager
    def session(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                yield ChromiumBackend(browser)
            finally:
                browser.close()

    def render(self, html: str) -> bytes:
        if self._browser is not None:
            return self._draw(self._browser, html)
        with self.session() as backend:
            return backend.render(html)

    @staticmethod
    def _draw(browser, html: str) -> bytes:
        page = browser.new_page()
        try:
            # The document is self-contained: themes inline their CSS, so there is nothing
            # to fetch. Every request the page makes is refused anyway rather than trusted
            # not to happen -- the counterpart of WeasyPrint's fetcher. A `data:` address is
            # not a request, so what a document embeds still draws (#163).
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
                **CHROMIUM_PDF_OPTIONS,
            )
        finally:
            # One page per document rather than one browser: a page left open holds the
            # document it drew in the browser's memory for as long as the session lasts.
            page.close()


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


@contextlib.contextmanager
def pdf_session(backend: PDFBackend | None = None):
    """One renderer for a run of documents, closed when the run ends.

    Opened before the first document rather than around each, so that a backend which is
    unusable says so once, before anything has been written down — and so that freezing a CV
    and a letter together is one Chromium instead of two (#220).

    A backend with no ``session`` is used as it is. Nothing in Postulo is one, but the
    backend interface is a protocol rather than a base class, and something written against
    the interface as it stood — a plugin's renderer, a test's stand-in — is still a renderer.
    """
    chosen = backend or get_pdf_backend()
    opener = getattr(chosen, "session", None)
    if opener is None:
        yield chosen
        return
    with opener() as ready:
        yield ready


#: The drafts this worker has drawn, oldest first.
_drafts: OrderedDict[str, bytes] = OrderedDict()


def forget_drafts() -> None:
    """Drop what this worker is holding, so nothing carries between one thing and the next."""
    _drafts.clear()


def draft_pdf(html: str, *, backend: PDFBackend | None = None) -> bytes:
    """Render a document that is handed over and filed nowhere, keeping it in case of a twin.

    The key is the SHA-256 of the HTML, so a hit means the input was identical byte for byte
    and the answer therefore is too. That is the whole of the safety argument: two people
    cannot share an entry without having asked for the same document, and a report carrying
    a name and a date is not a document two people ask for.

    Never used for a snapshot. What an employer received is drawn afresh and kept as a file,
    and handing one render's bytes to a second document would make the record a copy of
    something else.
    """
    key = hashlib.sha256(html.encode("utf-8")).hexdigest()
    held = _drafts.get(key)
    if held is not None:
        _drafts.move_to_end(key)
        return held

    content = html_to_pdf(html, backend=backend)
    if len(content) <= DRAFT_CACHE_MAX_BYTES:
        _drafts[key] = content
        while len(_drafts) > DRAFT_CACHE_ENTRIES:
            _drafts.popitem(last=False)
    return content


def html_to_pdf(html: str, *, backend: PDFBackend | None = None) -> bytes:
    """Render a complete HTML document to PDF bytes."""
    return (backend or get_pdf_backend()).render(html)
