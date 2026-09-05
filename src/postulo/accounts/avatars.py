"""Pictures of people: an upload, a Gravatar fetched once by the server, or initials.

Two rules shape this. **Postulo makes no request on a reader's behalf.** An avatar
referenced by URL would have every page view ask Automattic for it, carrying the reader's
address and a hash of the person's email; so when somebody opts into Gravatar the server
fetches the picture once, keeps a copy under private media, and serves it itself. The
content security policy stays exactly as it is. **A photograph carries more than a face.**
A phone's picture holds the place it was taken; every upload is decoded and re-encoded to
a fixed square, which drops the metadata along with the size.
"""

from __future__ import annotations

import hashlib
import io
import logging

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from postulo.plugins import http

logger = logging.getLogger(__name__)

#: The square every picture is brought to. Enough for the header and a document.
AVATAR_SIZE = 256

#: Uploads above this are refused before they are decoded.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

#: Decoded images above this many pixels are refused: a decompression bomb, or a mistake.
MAX_PIXELS = 40_000_000

ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

GRAVATAR_ENDPOINT = "https://gravatar.com/avatar/"


class UnusableImage(ValueError):
    """The bytes are not an image Postulo will keep."""


def process(data: bytes) -> ContentFile:
    """Decode, straighten, crop to a square, resize and re-encode as PNG.

    Re-encoding is the point, not a side effect: it is what strips EXIF and anything else
    the file carried. A file that will not decode, or would decode to something enormous,
    is refused.
    """
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise UnusableImage("That image is far larger than a picture needs to be.")
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA")
            square = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    except UnusableImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UnusableImage("That file could not be read as an image.") from exc
    out = io.BytesIO()
    square.save(out, format="PNG", optimize=True)
    return ContentFile(out.getvalue())


def gravatar_hash(email: str) -> str:
    """Gravatar's current scheme: SHA-256 of the trimmed, lower-cased address."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def gravatar_url(email: str, size: int = AVATAR_SIZE) -> str:
    """The picture for ``email``, or a clean 404 rather than a generated placeholder."""
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
