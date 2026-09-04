"""Delivery of private files.

Uploaded CVs and cover letters carry a home address, a phone number, and a full
employment history. They are stored under ``MEDIA_ROOT``, which is deliberately not
served by the web server or WhiteNoise: the only way out is through a view that has
already established who is asking.

Three delivery strategies are supported, in order of preference:

``X-Accel-Redirect``
    nginx serves the file itself after Django authorises it. Set
    ``POSTULO_MEDIA_ACCEL_PREFIX`` to an ``internal`` location.

``X-Sendfile``
    The Apache equivalent. Set ``POSTULO_MEDIA_SENDFILE``.

``FileResponse``
    Django streams the bytes. Correct everywhere, and the default, but it occupies an
    application worker for the duration of the download.
"""

from __future__ import annotations

import mimetypes
import posixpath
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponse


class UnsafeMediaPath(Exception):
    """Raised when a stored path resolves outside ``MEDIA_ROOT``."""


def resolve_media_path(name: str) -> Path:
    """Resolve a stored file name to an absolute path inside ``MEDIA_ROOT``.

    Storage names come from the database, but a bug elsewhere, a careless migration or
    a crafted upload name could still produce something like ``../../etc/passwd``. The
    check is cheap and the failure mode is severe, so it happens on every request.
    """
    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (media_root / name).resolve()
    if candidate != media_root and media_root not in candidate.parents:
        raise UnsafeMediaPath(f"{name!r} resolves outside MEDIA_ROOT")
    return candidate


def serve_private_file(
    request: HttpRequest,
    file_field,
    *,
    download_name: str | None = None,
    as_attachment: bool = False,
) -> HttpResponse:
    """Return a response delivering ``file_field`` to an already-authorised requester.

    This function performs **no** permission checking. The caller is responsible for
    establishing that the requester may see the file; keeping that decision at the call
    site is what stops it from being forgotten inside a generic helper.
    """
    if not file_field or not getattr(file_field, "name", ""):
        raise Http404("No file associated with this record.")

    try:
        path = resolve_media_path(file_field.name)
    except UnsafeMediaPath as exc:  # pragma: no cover - defensive
        raise Http404("File not found.") from exc

    if not path.is_file():
        raise Http404("File not found.")

    filename = download_name or posixpath.basename(file_field.name)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = "attachment" if as_attachment else "inline"
    # RFC 6266: an ASCII fallback plus a UTF-8 form for names with accents.
    content_disposition = (
        f'{disposition}; filename="{filename.encode("ascii", "ignore").decode()}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

    accel_prefix = getattr(settings, "POSTULO_MEDIA_ACCEL_PREFIX", "")
    if accel_prefix:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = posixpath.join(
            accel_prefix.rstrip("/") + "/", quote(file_field.name)
        )
    elif getattr(settings, "POSTULO_MEDIA_SENDFILE", False):
        response = HttpResponse(content_type=content_type)
        response["X-Sendfile"] = str(path)
    else:
        response = FileResponse(path.open("rb"), content_type=content_type)

    response["Content-Disposition"] = content_disposition
    # Private documents have no business in a shared cache.
    response["Cache-Control"] = "private, max-age=0, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
