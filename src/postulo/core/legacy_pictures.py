"""Moving the avatars and the logos from files to rows (#662), for the two migrations.

The pictures were files under private media and are rows of their own now. The migration
reads each one through the configured storage and writes the row; this holds what the two
migrations share, so that a file which is missing or no longer decodes is dealt with in one
way. **Goes with the old file fields**, in the release after the one that moves the data, so
that a rollback in between still finds its files.
"""

from __future__ import annotations

import logging
import sys

from django.core.files.storage import default_storage

from . import pictures

logger = logging.getLogger(__name__)


def stored_form_of(name: str) -> tuple[bytes, str] | tuple[None, str]:
    """The bytes to keep for the file ``name``, and their media type -- or no bytes and why.

    A PNG that decodes and fits is kept as it is, since it is what `as_stored` wrote. An SVG
    is sanitised again, which is the safe direction for a sanitiser to have moved in. Any
    other file that still decodes is written out as `as_stored` would have, and one that is
    missing or will not decode is reported and left out: that picture becomes "none", as if
    it had been removed.
    """
    try:
        with default_storage.open(name, "rb") as handle:
            data = handle.read()
    except (FileNotFoundError, OSError):
        return None, "the file is missing"
    try:
        if pictures.looks_like_svg(data):
            return pictures.sanitise_svg(data), "image/svg+xml"
        if data.startswith(pictures.PNG_SIGNATURE) and len(data) <= pictures.DEFAULT_BUDGET:
            pictures.decode(data).close()
            return data, "image/png"
        return pictures.as_stored(data), "image/png"
    except pictures.UnusablePicture:
        return None, "it no longer decodes"


def report(left_out: list[str]) -> None:
    """Say which pictures could not be moved, where an operator running ``migrate`` sees it."""
    if not left_out:
        return
    for line in left_out:
        logger.warning("Picture not moved to the database: %s", line)
    sys.stdout.write(
        f"\n  {len(left_out)} picture(s) could not be moved to the database and are now "
        "none:\n" + "".join(f"    {line}\n" for line in left_out)
    )
