"""A logo is decoded as one of the few kinds a logo is, not as anything Pillow can read (#777)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from postulo.core import pictures
from postulo.jobs import logos
from postulo.plugins import logos as plugin_logos


def encoded(kind: str, size=(8, 8)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, kind)
    return out.getvalue()


@pytest.mark.parametrize("kind", ["PNG", "JPEG", "GIF", "WEBP"])
def test_the_kinds_a_logo_is_are_kept(kind):
    assert logos.process(encoded(kind)).read().startswith(b"\x89PNG")


@pytest.mark.parametrize("kind", ["BMP", "TIFF", "PPM", "TGA", "PCX", "SGI"])
def test_every_other_format_pillow_reads_is_refused_before_it_is_decoded(kind):
    with pytest.raises(logos.UnusableLogo):
        logos.process(encoded(kind))
    with pytest.raises(plugin_logos.Unusable):
        plugin_logos._render(encoded(kind))


def test_the_list_pillow_may_try_leaves_out_what_a_logo_is_not():
    formats = pictures._formats_for(pictures.LOGO_KINDS)

    assert {"PNG", "JPEG", "GIF", "WEBP", "ICO"} <= set(formats)
    assert not {"EPS", "TIFF", "BMP", "PDF", "SGI", "DDS"} & set(formats)
    assert pictures._formats_for(None) is None


def test_decoding_puts_pillows_pixel_guard_back_as_it_found_it():
    was = Image.MAX_IMAGE_PIXELS

    pictures.decode(encoded("PNG"), max_pixels=1000).close()
    with pytest.raises(pictures.UnusablePicture):
        pictures.decode(b"not a picture", max_pixels=1000)

    assert Image.MAX_IMAGE_PIXELS == was
