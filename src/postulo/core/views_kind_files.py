"""One kind of record as a file: taking it out, and putting one back (#659).

Two addresses for each of four kinds, and none of them takes an argument that names a
record: what is downloaded is what the signed-in account holds of that kind, and what is
uploaded is added to the account of whoever is signed in. The kind is fixed by the route.
The page is the candidate file's, in the candidate file's shape (`resume.views_candidate`):
a file is read and held in the session, the page says what was found and what would
happen to each row, and nothing is added until somebody has seen that and pressed the button.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import TemplateView

from . import kind_files
from .file_review import Refused

#: Where a file that has been read waits to be confirmed, one place per kind: what is held
#: is what was read of it, not the file.
SESSION_KEY = "kind_file_{kind}"


def _held(request: HttpRequest, kind: str) -> dict | None:
    """What is waiting in this session for this kind, or nothing."""
    key = SESSION_KEY.format(kind=kind)
    held = request.session.get(key)
    if kind_files.is_held(held, kind):
        return held
    request.session.pop(key, None)
    return None


class KindFileView(LoginRequiredMixin, TemplateView):
    """Offer the file, read one, show what adding it would do, and add it when told to."""

    template_name = "core/kind_file.html"

    def dispatch(self, request, *args, **kwargs):
        self.kind = kwargs["kind"]
        return super().dispatch(request, *args, **kwargs)

    @property
    def _address(self) -> str:
        return f"core:file_{self.kind}"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        held = _held(self.request, self.kind)
        context["kind"] = self.kind
        context["title"] = kind_files.TITLES[self.kind]
        context["review"] = kind_files.plan(self.request.user, held) if held else None
        context["download"] = f"core:file_{self.kind}_download"
        if not held:
            context["total"] = kind_files.counts(self.request.user)[self.kind]
            context["filename"] = kind_files.filename(self.request.user, self.kind)
            context["max_mb"] = kind_files.MAX_BYTES // (1024 * 1024)
            context["max_rows"] = kind_files.MAX_ROWS
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        key = SESSION_KEY.format(kind=self.kind)
        action = request.POST.get("action")
        if action == "forget":
            request.session.pop(key, None)
            return redirect(self._address)
        if action == "confirm":
            return self._add(request)

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, _("Choose a file first."))
            return redirect(self._address)
        # Asked of the upload before a byte of it is read, and asked again of the bytes by
        # `kind_files.read`, which is what anything else calling it relies on.
        if upload.size > kind_files.MAX_BYTES:
            messages.error(
                request,
                _("That file is larger than %(limit)s MB, so it was not read.")
                % {"limit": kind_files.MAX_BYTES // (1024 * 1024)},
            )
            return redirect(self._address)
        try:
            held = kind_files.read(self.kind, upload.read(), upload.name or "")
        except Refused as refused:
            messages.error(request, str(refused))
            return redirect(self._address)
        if kind_files.is_empty(held):
            messages.warning(
                request, _("That file was read, and there was nothing in it to import.")
            )
            return redirect(self._address)
        request.session[key] = held
        return redirect(self._address)

    def _add(self, request: HttpRequest) -> HttpResponse:
        held = _held(request, self.kind)
        if not held:
            messages.error(request, _("There is nothing waiting to be imported."))
            return redirect(self._address)
        report = kind_files.apply(request.user, held)
        request.session.pop(SESSION_KEY.format(kind=self.kind), None)
        if report.added:
            messages.success(
                request,
                ngettext(
                    "One record was added. Nothing you already had was changed or removed.",
                    "%(total)s records were added. Nothing you already had was changed or removed.",
                    report.added,
                )
                % {"total": report.added},
            )
        else:
            messages.info(
                request,
                _(
                    "Nothing was added. What that file holds is already in your account, "
                    "or could not be added."
                ),
            )
        return redirect(self._address)


class KindDownloadView(LoginRequiredMixin, View):
    """The records of whoever is asking, as one JSON document arriving as a download.

    A link rather than a button that posts: it changes nothing, so asking for it twice is
    asking for it once. Never kept by anything between here and the person.
    """

    def get(self, request: HttpRequest, kind: str) -> HttpResponse:
        response = JsonResponse(
            kind_files.build_document(request.user, kind),
            json_dumps_params={"ensure_ascii": False, "indent": 2},
        )
        response["Content-Disposition"] = content_disposition_header(
            True, kind_files.filename(request.user, kind)
        )
        response["Cache-Control"] = "private, max-age=0, no-store"
        return response
