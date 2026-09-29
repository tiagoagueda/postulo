"""Reading a kept source back: the bytes that were kept, unpacked, never more than allowed.

What `pages` keeps (#256), `remembered` reads -- a review learns the places of the page a
capture came from, and keeps the lesson before a kept source is thrown away (#267). `pages`
hands that source to `remembered` as it goes, so the reading could not stay in `pages`
without the two importing each other (#248). `pages` still hands out every name here.
"""

from __future__ import annotations

import zlib

from postulo.core import site

from .models import CapturedPage


class Unreadable(Exception):
    """A kept source is not gzip, or unpacks to more than it is allowed to be."""


def unpack(packed: bytes, limit: int) -> bytes:
    """Gunzip at most ``limit`` bytes, and refuse rather than truncate.

    Bounded because what is on disk is not always what this module wrote: an archive can be
    imported, a volume restored, and a few kilobytes of gzip can unpack to gigabytes. One
    byte past the limit is :class:`Unreadable`, not a shortened file pretending to be whole.
    """
    unpacker = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)  # 16: expect a gzip header
    try:
        unpacked = unpacker.decompress(packed, limit + 1)
    except zlib.error as error:
        raise Unreadable("not a gzip file") from error
    if len(unpacked) > limit:
        raise Unreadable(f"more than {limit} bytes")
    return unpacked


def read_source_bytes(page: CapturedPage) -> bytes:
    """The kept source as the bytes that were kept, never more than the instance's cap."""
    with page.source.open("rb") as handle:
        packed = handle.read()
    return unpack(packed, site.capture_source_max_bytes())


def read_source(page: CapturedPage) -> str:
    """The kept source as text: what the parser was handed."""
    return read_source_bytes(page).decode("utf-8", errors="replace")
