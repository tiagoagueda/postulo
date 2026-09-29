"""What a capture kept of its page, shown to the person whose capture it is (#256).

Five addresses, and every one of them narrows to the requester's own captures before it
looks anything up, so somebody else's capture is a 404 here as it is everywhere.

**The source is never answered as a page.** On the page that shows it, it is text inside a
`<pre>`, escaped by the template like any other text a stranger wrote. As a download it is
``text/plain`` and an attachment, through `serve_private_text`. There is no address in
Postulo that answers a kept source as HTML, in a frame or out of one, and
`tests/security/test_captured_pages.py` is what holds that.

**The rendering is a file like any other private file**: through `serve_private_file`,
under a media type taken from the four Postulo keeps rather than from anything a client
said. A picture is drawn by the page; a PDF is handed over as a download.
"""

from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View

from postulo.core import errands
from postulo.core.files import serve_private_file, serve_private_text
from postulo.core.mixins import OwnedObjectMixin

from . import pages, rendering
from .models import Capture, RenderingKind


class CapturedPageMixin(OwnedObjectMixin):
    """The requester's own capture, and what it kept."""

    def get_queryset(self):
        return Capture.objects.for_user(self.request.user).select_related("posting")

    def capture_or_404(self, pk: int) -> Capture:
        return get_object_or_404(self.get_queryset(), pk=pk)

    def page_or_404(self, pk: int):
        page = self.capture_or_404(pk).kept_page
        if page is None:
            raise Http404(_("Nothing was kept of that page."))
        return page


def download_stem(capture: Capture) -> str:
    """What a download of this capture is called: its title, made safe for a file name.

    The title is a stranger's, read off their page, so it is reduced to letters, digits
    and hyphens before it goes anywhere near a header.
    """
    stem = slugify(capture.data.get("title", ""))[:60].strip("-")
    return stem or f"capture-{capture.pk}"


class CapturedPageView(CapturedPageMixin, View):
    """What was kept, beside what was read: the rendering, and the source as text."""

    template_name = "jobs/capture_page.html"

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = self.capture_or_404(pk)
        page = capture.kept_page
        keeping = pages.keeping_for(request.user)

        excerpt, more, unreadable = "", False, False
        if page is not None and page.source:
            try:
                excerpt, more = pages.excerpt_of(page)
            except (pages.Unreadable, OSError):
                # A file that has gone from the disk, or one that is not what was written.
                # The page still opens: it is where somebody finds out, and where they can
                # throw the row away.
                unreadable = True

        cannot_draw = pages.why_nothing_is_drawn(capture, keeping)
        return render(
            request,
            self.template_name,
            {
                "capture": capture,
                "page": page,
                "keeping": keeping,
                "excerpt": excerpt,
                "excerpt_is_cut": more,
                "source_unreadable": unreadable,
                "can_draw": cannot_draw is None,
                # Said only where a rendering was wanted and there is a source to draw
                # from: the one case in which a missing button needs explaining.
                "no_renderer": (
                    rendering.why_not()
                    if cannot_draw is not None and cannot_draw.reason == "no-renderer"
                    else ""
                ),
            },
        )


class CapturedSourceView(CapturedPageMixin, View):
    """The kept source as a download: text, an attachment, and never a page."""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        page = self.page_or_404(pk)
        if not page.source:
            raise Http404(_("No source was kept of that page."))
        try:
            text = pages.read_source_bytes(page)
        except (pages.Unreadable, OSError) as error:
            raise Http404(_("The kept source could not be read back.")) from error
        return serve_private_text(
            request, text, download_name=f"{download_stem(page.capture)}-source.txt"
        )


class CapturedRenderingView(CapturedPageMixin, View):
    """The kept rendering: a picture for the page to draw, or a PDF to download."""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        page = self.page_or_404(pk)
        if not page.rendering or page.rendering_type not in RenderingKind.values:
            raise Http404(_("No rendering was kept of that page."))
        extension = pages.EXTENSIONS[page.rendering_type]
        response = serve_private_file(
            request,
            page.rendering,
            download_name=f"{download_stem(page.capture)}.{extension}",
            # A PDF is a document, and one a client sent is a stranger's document. It is
            # handed over rather than opened here.
            as_attachment=not page.rendering_is_a_picture,
        )
        # Said, not guessed. `serve_private_file` reads a type off the name; the name is
        # ours and would guess right, and this is what makes that not matter.
        response["Content-Type"] = page.rendering_type
        return response


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CapturedPageDrawView(CapturedPageMixin, View):
    """*Draw it from the source*: sent off as slow work, and watched.

    **Outside a transaction of its own**, for the reason #220 gave every other slow button:
    on an instance with no worker the drawing happens where this request stands, and under
    `ATOMIC_REQUESTS` on SQLite every second of it would be a second nothing else could
    write. What is written is one row when the drawing is done.
    """

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        capture = self.capture_or_404(pk)
        refusal = pages.why_nothing_is_drawn(capture)
        if refusal is not None:
            messages.error(request, str(refusal))
            return redirect("jobs:capture_page", pk=capture.pk)
        errand = errands.send(
            "page_rendering", request.user, subject=capture, capture_id=capture.pk
        )
        return redirect("core:errand", pk=errand.pk)


#: What the address of the page that deletes may name, and what each takes with it. Anything
#: else is read as the whole of what was kept, which is what the bare address means.
PARTS = {
    "rendering": {"source": False, "rendering": True},
    "source": {"source": True, "rendering": False},
    "": {"source": True, "rendering": True},
}


class CapturedPageForgetView(CapturedPageMixin, View):
    """Throw away what was kept: asked first, and the capture itself stays."""

    template_name = "jobs/capture_page_forget.html"

    def part(self, request: HttpRequest) -> str:
        asked = (request.POST.get("what") or request.GET.get("what") or "").strip()
        return asked if asked in PARTS else ""

    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        page = self.page_or_404(pk)
        return render(
            request,
            self.template_name,
            {"capture": page.capture, "page": page, "what": self.part(request)},
        )

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        page = self.page_or_404(pk)
        capture = page.capture
        pages.forget(page, **PARTS[self.part(request)])
        messages.success(request, _("Deleted. The capture itself is unchanged."))
        return redirect("jobs:capture_page", pk=capture.pk)
