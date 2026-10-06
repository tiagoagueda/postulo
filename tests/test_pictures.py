"""The cost of keeping a picture (#482)."""

import io
import random
from unittest import mock

from PIL import Image

from postulo.core import pictures


def photograph(width, height):
    """Noisy enough that, like a camera's, it does not compress as PNG."""
    rng = random.Random(1)  # noqa: S311 - a fixture, not a secret
    small = Image.frombytes(
        "RGB", (width // 8, height // 8), rng.randbytes((width // 8) * (height // 8) * 3)
    )
    image = small.resize((width, height), Image.Resampling.BICUBIC)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def test_a_camera_sized_photograph_is_encoded_at_most_twice():
    data = photograph(4000, 3000)
    saves = []
    real_save = Image.Image.save

    def counting(self, fp, format=None, **kwargs):
        if format == "PNG":
            saves.append(kwargs)
        return real_save(self, fp, format=format, **kwargs)

    with mock.patch.object(Image.Image, "save", counting):
        stored = pictures.as_stored(data, square=True)

    assert len(saves) <= 2
    assert len(stored) <= pictures.DEFAULT_BUDGET
    with Image.open(io.BytesIO(stored)) as result:
        assert result.format == "PNG"
        assert result.width == result.height


def test_a_small_flat_picture_keeps_its_size():
    out = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(out, format="PNG")
    with Image.open(io.BytesIO(pictures.as_stored(out.getvalue()))) as result:
        assert result.size == (300, 200)
