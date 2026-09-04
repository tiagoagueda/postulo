"""Taking your data out through the web interface."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .export import build_document, suggested_filename, write_archive


@login_required
def export_overview(request: HttpRequest):
    """Explain what an export contains before handing one over."""
    document = build_document(request.user)
    return render(
        request,
        "core/export.html",
        {"counts": document.get("counts", {}), "filename": suggested_filename(request.user)},
    )


@require_POST
@login_required
def export_download(request: HttpRequest) -> FileResponse:
    """Build and send the archive.

    A POST rather than a GET: it is not expensive enough to be dangerous, but it reads
    every record and every file the account owns, which is not something a prefetching
    browser should be able to trigger by following a link.
    """
    archive = write_archive(request.user)
    response = FileResponse(archive, as_attachment=True, filename=suggested_filename(request.user))
    response["Cache-Control"] = "private, max-age=0, no-store"
    return response
