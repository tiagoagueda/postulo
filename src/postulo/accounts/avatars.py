"""Pictures of people: an upload, a Gravatar fetched once by the server, or initials.

Two rules shape this. **Postulo makes no request on a reader's behalf.** An avatar
referenced by URL would have every page view ask Automattic for it, carrying the reader's
address and a hash of the person's email; so when somebody opts into Gravatar the server
fetches the picture once, keeps a copy under private media, and serves it itself. The
content security policy stays exactly as it is. **A photograph carries more than a face.**
A phone's picture holds the place it was taken; every upload is decoded and re-encoded,
which drops the metadata along with everything else the file knew.

**Bounded by file size, not by dimensions** (#265). Every picture used to be normalised to
exactly 256×256 — and the profile page draws one at `size-24`, which is 96 CSS pixels and
288 physical ones on a phone at 3×. The one page whose subject is the picture was upscaling
it. The ceiling is gone: what is kept is what was given, reduced only as far as it has to be
to fit a byte budget. `core.pictures` holds that rule, and the company logo obeys the same
one (#264).

**The square crop stays, and differs from a logo on purpose.** `ImageOps.fit` rather than
`contain`: a face belongs cropped to the tile, the tile is square everywhere it appears, and
the initials that stand in for a missing picture are a square tile too. A wordmark must not
be cropped and a face should be, so `jobs/logos.py` and this file disagree deliberately.

**No SVG here.** #264's sanitiser is for logos. Nobody uploads a vector of their own face,
so extending it to this would be attack surface bought for nothing.
"""

from __future__ import annotations

import hashlib
import logging

from django.core.files.base import ContentFile
from django.utils import timezone

from postulo.core import pictures
from postulo.plugins import http

logger = logging.getLogger(__name__)

#: What is asked of Gravatar, and the largest a stored picture is likely to need. Gravatar
#: serves up to 2048; this is the size at which a passport-style photograph on a printed
#: CV -- 35 x 45 mm at 300 dpi is about 413 x 531 -- is comfortably covered, and a page
#: header on a large screen with it (#265). The old constant asked for 256 and was the
#: output dimension as well; it is neither now.
GRAVATAR_SIZE = 1024

#: Uploads above this are refused before they are decoded.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

#: What a stored picture may weigh once re-encoded. The reduction in `core.pictures`
#: effectively never runs: a face at a thousand pixels is well inside this.
MAX_STORED_BYTES = 1024 * 1024

#: Re-exported for the tests that name it. The guard is `core.pictures`': it bounds what
#: decoding allocates, not what is kept.
MAX_PIXELS = pictures.MAX_PIXELS

ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

GRAVATAR_ENDPOINT = "https://gravatar.com/avatar/"


class UnusableImage(ValueError):
    """The bytes are not an image Postulo will keep."""


def process(data: bytes) -> ContentFile:
    """Decode, straighten, crop to a square and re-encode as PNG, at the size it came in.

    Re-encoding is the point, not a side effect: it is what strips EXIF and anything else
    the file carried, including where the photograph was taken. A file that will not decode,
    or would decode to something enormous, is refused.

    Cropped square but **not resized to a constant** (#265): the shorter edge decides the
    square, and the byte budget decides whether anything has to be reduced at all.
    """
    try:
        return ContentFile(pictures.as_stored(data, budget=MAX_STORED_BYTES, square=True))
    except pictures.UnusablePicture as error:
        raise UnusableImage(str(error)) from error


def gravatar_hash(email: str) -> str:
    """Gravatar's current scheme: SHA-256 of the trimmed, lower-cased address."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def gravatar_url(email: str, size: int = GRAVATAR_SIZE) -> str:
    """The picture for ``email``, or a clean 404 rather than a generated placeholder.

    Asked for at `GRAVATAR_SIZE` rather than at whatever the stored square used to be. The
    fetch happens once, from the server, when somebody opts in — so asking for a picture
    that can be shown large costs one larger response, once, and asking for a small one
    means it never can be (#265).
    """
    return f"{GRAVATAR_ENDPOINT}{gravatar_hash(email)}?s={size}&d=404"


def picture_name(profile, prefix: str) -> str:
    return f"{prefix}-{profile.user_id}.png"


def store(profile, field: str, content: ContentFile, prefix: str) -> None:
    """Replace the file behind ``field`` with ``content``, deleting what was there."""
    existing = getattr(profile, field)
    if existing:
        existing.delete(save=False)
    getattr(profile, field).save(picture_name(profile, prefix), content, save=False)


def fetch_gravatar(profile) -> str:
    """Ask Gravatar once for the primary address's picture. Returns found, none or error.

    One request, from the server, when the person asks — never from a page view. A 404 is
    the normal answer for most addresses and leaves the initials showing.
    """
    outcome = "error"
    try:
        with http.client(timeout=8.0) as client:
            response = client.get(gravatar_url(profile.user.email))
        if response.status_code == 404:
            if profile.gravatar_image:
                profile.gravatar_image.delete(save=False)
            outcome = "none"
        elif response.status_code == 200 and response.content:
            store(profile, "gravatar_image", process(response.content), "gravatar")
            outcome = "found"
        else:
            logger.warning("Gravatar answered %s for %s", response.status_code, profile.user_id)
    except Exception:
        logger.exception("Gravatar could not be fetched for user %s", profile.user_id)
    profile.gravatar_checked_at = timezone.now()
    profile.save(update_fields=["gravatar_image", "gravatar_checked_at", "updated_at"])
    return outcome


def forget_gravatar(profile) -> None:
    """Drop the stored copy: switched off means nothing of theirs is kept."""
    if profile.gravatar_image:
        profile.gravatar_image.delete(save=False)
    profile.gravatar_checked_at = None
    profile.save(update_fields=["gravatar_image", "gravatar_checked_at", "updated_at"])


def remove_upload(profile) -> None:
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.save(update_fields=["avatar", "updated_at"])
