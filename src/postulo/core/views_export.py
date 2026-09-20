"""Taking your data out through the web interface."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import errands
from .export import counts, suggested_filename
from .models import ExportArchive


@login_required
def export_overview(request: HttpRequest):
    """Explain what an export contains before handing one over.

    The numbers come from eight `COUNT(*)` queries. They used to come from building the
    whole archive's document and measuring its lists, which read every record the account
    owns so that a page could print six of them (#220).
    """
    return render(
        request,
        "core/export.html",
        {
            "counts": counts(request.user),
            "filename": suggested_filename(request.user),
            "keep_hours": _keep_hours(),
            "ready": ExportArchive.objects.for_user(request.user).first(),
        },
    )


@transaction.non_atomic_requests
@require_POST
@login_required
def export_download(request: HttpRequest) -> HttpResponse:
    """Ask for the archive, and go somewhere that watches it being made (#247).

    A POST rather than a GET: it is not expensive enough to be dangerous, but it reads every
    record and every file the account owns, which is not something a prefetching browser
    should be able to trigger by following a link.

    It used to build the archive here and answer with it, which was one long request holding
    a gunicorn worker for every second of it. It now asks for the work and answers with the
    page that watches it; on an instance with no worker that page is already finished when it
    is drawn, so the only difference there is one click more to the file.

    Still outside a transaction of its own, for the reason #220 gave: the build reads
    everything and writes one row, and neither belongs inside a request-long lock.
    """
    errand = errands.send("export", request.user)
    return redirect("core:errand", pk=errand.pk)


@login_required
def export_archive(request: HttpRequest, pk: int) -> HttpResponse:
    """Hand over a built archive, to the person whose account it holds and nobody else.

    An export is the whole of somebody's job search in one file, so it is served the way
    every other personal document is -- through a view that has established who is asking --
    and never from the media directory. An archive that has expired since the page named it
    says so rather than 404ing on a file the reaper has taken: *it expired* is an answer
    somebody can act on, and *not found* is not.
    """
    from .files import serve_private_file

    archive = get_object_or_404(ExportArchive.objects.for_user(request.user), pk=pk)
    if archive.has_expired or not archive.file:
        raise Http404(_("That export has expired. Ask for a new one."))
    response = serve_private_file(
        request, archive.file, download_name=archive.filename, as_attachment=True
    )
    response["Cache-Control"] = "private, max-age=0, no-store"
    return response


def _keep_hours() -> int:
    from django.conf import settings

    return int(getattr(settings, "POSTULO_EXPORT_KEEP_HOURS", 24) or 24)
