"""The documentation's pictures are generated, and the list they are generated from holds (#353).

`scripts/screenshots.py` drives a browser, which is the browser job's business; what is held
here is what needs none: that every picture has a permanent name and an alternative text,
that the README's pictures exist where it says, and that the comparison which decides a
picture is stale says so for a changed page and for nothing else.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("screenshots", ROOT / "scripts" / "screenshots.py")
screenshots = importlib.util.module_from_spec(_spec)
sys.modules["screenshots"] = screenshots  # a dataclass looks its own module up
_spec.loader.exec_module(screenshots)


def _png(colour: tuple[int, int, int], size=(40, 20), patch: int = 0) -> bytes:
    image = Image.new("RGB", size, colour)
    for x in range(patch):
        image.putpixel((x, 0), (0, 0, 0))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_every_picture_has_a_name_that_is_a_file_name_and_an_alternative_text():
    names = [shot.name for shot in screenshots.SHOTS]
    assert len(names) == len(set(names)), "two pictures share a file name"
    for shot in screenshots.SHOTS:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", shot.name), shot.name
        assert shot.to in {"wiki", "readme", "both"}, shot.name
        # Said to somebody who cannot see it: a sentence, not a label.
        assert len(shot.alt.split()) >= 6 and shot.alt.endswith("."), shot.name


def test_a_rendering_is_taken_of_a_cv_and_a_letter_in_every_theme_given():
    made = screenshots.renderings([("plain", "Plain"), ("classic", "Classic")])
    assert [shot.name for shot in made] == [
        "cv-plain",
        "letter-plain",
        "cv-classic",
        "letter-classic",
    ]
    assert all(shot.size == screenshots.A4 for shot in made)


def test_the_readmes_pictures_exist_and_each_has_alternative_text():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"!\[([^\]]*)\]\((assets/screenshots/[^)]+)\)", readme)
    assert found, "the README shows no picture of the product"
    named = {shot.name for shot in screenshots.SHOTS if shot.to in ("readme", "both")}
    for alt, path in found:
        assert alt.strip(), f"{path} has no alternative text"
        assert Path(path).stem in named, f"{path} is not one the script takes for the README"
        if (ROOT / path).parent.is_dir() and any((ROOT / path).parent.iterdir()):
            assert (ROOT / path).is_file(), f"{path} is not there; run scripts/screenshots.py"


def test_where_a_picture_goes_follows_its_destination_and_the_language(tmp_path):
    wiki = tmp_path / "wiki"
    both = next(s for s in screenshots.SHOTS if s.to == "both")
    only_wiki = next(s for s in screenshots.SHOTS if s.to == "wiki")
    assert screenshots.destinations(only_wiki, "en", wiki) == [
        wiki / "images" / f"{only_wiki.name}.png"
    ]
    assert screenshots.destinations(both, "fr", wiki) == [
        wiki / "images" / "fr" / f"{both.name}.png",
        screenshots.README_IMAGES / "fr" / f"{both.name}.png",
    ]
    assert screenshots.markdown(only_wiki, "fr").endswith(f"(images/fr/{only_wiki.name}.png)")


def test_identical_pictures_do_not_differ_and_a_changed_page_does():
    base = _png((255, 255, 255))
    assert screenshots.differing(base, _png((255, 255, 255))) == 0
    assert screenshots.differing(base, _png((255, 255, 255), patch=40)) == pytest.approx(0.05)
    assert screenshots.differing(base, _png((255, 255, 255), size=(40, 21))) == 1.0


def test_a_shade_too_faint_to_read_is_not_a_difference():
    # Another machine's anti-aliasing moves a pixel by a few levels, which is not a change.
    assert screenshots.differing(_png((255, 255, 255)), _png((250, 250, 250))) == 0


def test_the_pictures_are_taken_of_a_demo_and_nothing_of_the_machine_reaches_them():
    source = (ROOT / "scripts" / "screenshots.py").read_text(encoding="utf-8")
    # The environment is emptied of POSTULO_ variables and the developer's .env is not read.
    assert 'startswith("POSTULO_")' in source
    assert "read_env" in source
    # The fixed account is the one the browser suite and the documentation already use.
    assert screenshots.EMAIL.endswith("@example.org")
