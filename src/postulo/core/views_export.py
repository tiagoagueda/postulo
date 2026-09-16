"""Taking your data out through the web interface."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, HttpRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .export import counts, suggested_filename, write_archive


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
        {"counts": counts(request.user), "filename": suggested_filename(request.user)},
    )


@transaction.non_atomic_requests
@require_POST
@login_required
def export_download(request: HttpRequest) -> FileResponse:
    """Build and send the archive.

    A POST rather than a GET: it is not expensive enough to be dangerous, but it reads
    every record and every file the account owns, which is not something a prefetching
    browser should be able to trigger by following a link.

    And outside a transaction of its own, because "every record and every file the account
    owns" is exactly how long the write lock was held on SQLite while it happened (#220).
    Nothing here writes. `write_archive` still reads the records inside one transaction, so
    the manifest is a consistent picture; the files are copied outside it.
    """
    archive = write_archive(request.user)
    response = FileResponse(archive, as_attachment=True, filename=suggested_filename(request.user))
    response["Cache-Control"] = "private, max-age=0, no-store"
    return response
