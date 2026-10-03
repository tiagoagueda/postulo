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

Behind nginx or Apache the proxy's own response is the one the browser gets, and nginx
carries over only a few of an upstream's headers on an internal redirect. The proxy must
therefore add ``Content-Security-Policy`` (``FILE_POLICY``) and ``X-Content-Type-Options``
itself for the files it sends. An SVG is never handed over: it is always streamed by
Django, so the one type the policy exists for does not depend on the proxy (#415).
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


#: What a response carrying somebody's file says about what that file may do.
#:
#: An SVG served from our own origin is a same-origin *document* when it is visited
#: directly, not the `<img>` a page renders it as -- so anything it carries runs as us.
#: `core.pictures` sanitises one on the way in; this is the other half, and it is the half
#: that holds if the sanitiser is ever wrong about something (#264). `sandbox` with no
#: tokens denies scripts, plugins, forms, popups and an origin of its own; `default-src
#: 'none'` denies every fetch the document might make.
#:
#: Applied to every private file rather than to SVGs alone, because a rule that is only
#: on for one content type is a rule somebody has to remember to extend.
FILE_POLICY = "default-src 'none'; sandbox"


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
    handed_over = content_type != "image/svg+xml"
    if accel_prefix and handed_over:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = posixpath.join(
            accel_prefix.rstrip("/") + "/", quote(file_field.name)
        )
    elif getattr(settings, "POSTULO_MEDIA_SENDFILE", False) and handed_over:
        response = HttpResponse(content_type=content_type)
        response["X-Sendfile"] = str(path)
    else:
        response = FileResponse(path.open("rb"), content_type=content_type)

    response["Content-Disposition"] = content_disposition
    # Private documents have no business in a shared cache.
    response["Cache-Control"] = "private, max-age=0, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    # See `FILE_POLICY`. Set here rather than at each call site, so a view added later
    # carries it without anybody having to remember (#264).
    response["Content-Security-Policy"] = FILE_POLICY
    return response


#: What text a stranger wrote is answered as, and the whole of it. Never anything a name
#: or a client suggested: the point of the function below is that this cannot vary.
PLAIN_TEXT = "text/plain; charset=utf-8"


def serve_private_text(request: HttpRequest, text: bytes, *, download_name: str) -> HttpResponse:
    """Hand over text that somebody else wrote, as a download, and as nothing but text.

    `serve_private_file` for the one kind of file that must never be drawn: the source of
    a captured page is a stranger's markup, and answered as a page from this origin it
    would be that stranger's code running as the person who opened it (#256, and #218
    before it). So three things here are fixed rather than worked out:

    * the media type is ``text/plain``, whatever the name ends in -- `serve_private_file`
      guesses a type from the name, and a guess is exactly what this must not be;
    * it is an **attachment**, so a browser saves it rather than showing it;
    * ``nosniff`` stops a browser deciding for itself that text full of tags is a page,
      and the file policy is a second answer to the same question if it ever did.

    Takes the bytes rather than a file, and always answers them itself. What is kept is
    gzipped, so there is no file on disk a web server could be handed that a browser
    would read; and it is small, being capped where it was kept. Like its sibling it
    performs **no** permission checking: the caller has established who is asking.
    """
    name = download_name if download_name.endswith(".txt") else f"{download_name}.txt"
    response = HttpResponse(text, content_type=PLAIN_TEXT)
    response["Content-Disposition"] = (
        f'attachment; filename="{name.encode("ascii", "ignore").decode()}"; '
        f"filename*=UTF-8''{quote(name)}"
    )
    response["Cache-Control"] = "private, max-age=0, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = FILE_POLICY
    return response
