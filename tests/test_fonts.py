"""The container image must be able to draw the scripts Postulo offers.

A missing glyph is not a degraded experience, it is a row of empty boxes, and a row of
empty boxes on somebody's CV is worse than the same CV in English. WeasyPrint draws with
whatever fonts the image has, and a Debian slim base has almost none — Postulo installed
`fonts-dejavu-core`, which covers Latin, Greek and Cyrillic and stops there. That was
enough for the European Union set and stopped being enough the moment Amharic, Tigrinya
and Arabic arrived (#70).

This reads the language list and the Dockerfile and insists they agree, so a language added
in a later phase cannot quietly ship a PDF nobody can read. It checks the *declaration*
rather than a running container: it costs milliseconds, needs no Docker, and fails on the
laptop of whoever added the language rather than in somebody's hands.
"""

import re
import struct
from pathlib import Path

import pytest

from postulo.core import languages

DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "Dockerfile"

#: Which Debian font package covers which script. Deliberately short: adding a script
#: means deciding which package draws it, which is a decision worth making explicitly.
COVERAGE: dict[str, tuple[str, ...]] = {
    "Latin": ("fonts-dejavu-core", "fonts-noto-core"),
    "Greek": ("fonts-dejavu-core", "fonts-noto-core"),
    "Cyrillic": ("fonts-dejavu-core", "fonts-noto-core"),
    "Arabic": ("fonts-noto-core",),
    "Ethiopic": ("fonts-noto-core",),
    "Hebrew": ("fonts-noto-core",),
    "Devanagari": ("fonts-noto-core",),
    "Thai": ("fonts-noto-core",),
    # CJK is its own package and its own size; #71 decides when it arrives.
    "Han": ("fonts-noto-cjk",),
    "Hiragana": ("fonts-noto-cjk",),
    "Hangul": ("fonts-noto-cjk",),
}


def installed_font_packages() -> set[str]:
    """The font packages the runtime image installs, read from the Dockerfile."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    # Only the lines that are actually instructions; the comment above them names
    # packages too, and a comment is not an install.
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(r"\bfonts-[a-z0-9-]+\b", body))


def test_the_image_can_draw_every_script_postulo_offers():
    installed = installed_font_packages()
    missing = {}
    for script in sorted(languages.scripts_offered()):
        options = COVERAGE.get(script)
        assert options, (
            f"{script} is offered but no font package is recorded for it. Add it to "
            "COVERAGE here and to the Dockerfile."
        )
        if not installed & set(options):
            missing[script] = options
    assert not missing, "the image cannot draw: " + ", ".join(
        f"{script} (install one of {opts})" for script, opts in missing.items()
    )


def test_every_non_latin_language_declares_its_script():
    """A language whose script is not recorded is one nobody checked the fonts for."""
    from postulo.core.languages import LANGUAGES, SCRIPTS

    # The ones known to be written in something other than the Latin alphabet.
    not_latin = {"ar", "am", "ti", "bg", "el"}
    offered = {code for code, _name in LANGUAGES}
    for code in not_latin & offered:
        assert code in SCRIPTS, f"{code} is not written in Latin and says nothing about it"


def test_the_scripts_offered_are_the_ones_the_languages_need():
    offered = languages.scripts_offered()
    assert {"Arabic", "Ethiopic", "Greek", "Cyrillic"} <= offered
    # Nothing from a later phase has crept in without its fonts being decided.
    assert "Han" not in offered and "Devanagari" not in offered


def test_the_dockerfile_reader_ignores_comments():
    """The comment above the install names packages; a comment does not install one."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "# tests/test_fonts.py holds this list" in text, "the comment moved; check this still"
    assert "fonts-noto-cjk" not in installed_font_packages()


# --------------------------------------- the decision #74 made and the door it left


def test_cjk_is_not_the_default_image():
    """Doubling the image for a language almost none of the installations will use (#74)."""
    assert "fonts-noto-cjk" not in installed_font_packages()


def test_the_image_has_a_door_for_the_fonts_it_does_not_carry():
    """The opt-in is an argument the runtime stage consumes, not a comment (#74)."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'ARG POSTULO_EXTRA_APT_PACKAGES=""' in text
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "POSTULO_EXTRA_APT_PACKAGES" in body, "declared, but nothing installs it"
    assert "apt-get install" in body


# ------------------------------------------------- the runtime check, the half that
# -------------------------------------------- can be asked of any machine, anywhere


def _cmap12(ranges) -> bytes:
    """A whole cmap table carrying one format-12 subtable with these (start, end, glyph)."""
    body = struct.pack(">HHIII", 12, 0, 16 + 12 * len(ranges), 0, len(ranges))
    body += b"".join(struct.pack(">III", *r) for r in ranges)
    return struct.pack(">HH", 0, 1) + struct.pack(">HHI", 0, 4, 12) + body


def _cmap4(start, end, delta, range_offset=0, glyph_array=b"") -> bytes:
    """A whole cmap table carrying one format-4 subtable with one segment."""
    body = struct.pack(">HHHHHHH", 4, 0, 0, 2, 2, 0, 0)
    body += struct.pack(">H", end)
    body += struct.pack(">H", 0)
    body += struct.pack(">H", start)
    body += struct.pack(">h", delta)
    body += struct.pack(">H", range_offset)
    body += glyph_array
    return struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + body


def test_cmap_says_yes_for_a_character_in_a_mapped_range():
    from postulo.documents.fonts import cmap_covers

    assert cmap_covers(_cmap12([(0x0600, 0x06FF, 5)]), 0x0628) is True


def test_cmap_says_no_when_the_range_maps_to_nothing():
    """A range whose first glyph is zero is the font saying *not here* (#74)."""
    from postulo.documents.fonts import cmap_covers

    assert cmap_covers(_cmap12([(0x0600, 0x06FF, 0)]), 0x0628) is False
    assert cmap_covers(_cmap12([(0x0600, 0x06FF, 0), (0x1200, 0x137F, 10)]), 0x1200) is True


def test_cmap_reads_format_four_through_the_delta_and_the_glyph_array():
    from postulo.documents.fonts import cmap_covers

    assert cmap_covers(_cmap4(0x0600, 0x06FF, 5), 0x0628) is True
    assert cmap_covers(_cmap4(0x0600, 0x06FF, -0x0600), 0x0600) is False

    through_array = _cmap4(0x0600, 0x0601, 0, range_offset=2, glyph_array=struct.pack(">HH", 7, 0))
    assert cmap_covers(through_array, 0x0600) is True
    assert cmap_covers(through_array, 0x0601) is False


def test_cmap_with_no_answerable_subtable_cannot_say_yes():
    """Format 0 is Mac Roman: a table with only that is a table with no answer (#74)."""
    from postulo.documents.fonts import cmap_covers

    table = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 1, 0, 12) + struct.pack(">H", 0)
    assert cmap_covers(table, 0x0628) is False
    assert cmap_covers(b"", 0x41) is False
    assert cmap_covers(b"\x00\x01\x00\x02", 0x41) is False, "a directory that is not there"


def _mocked_probe_handles(table: bytes):
    """The probe's native side, mocked: the map resolves one font, HarfBuzz hands its
    ``cmap`` over as ``table``.

    The first red streak of #74 taught the shape of this: the probe asked the raw FFI
    object for the HarfBuzz functions, and every page that shows the scripts 500'd on
    any machine whose font map answers the question. The mocks are the handles the
    code must ask, and they run where WeasyPrint itself cannot be imported.
    """
    import cffi

    ffi = cffi.FFI()
    data = ffi.new("char[]", table)
    unrefed = []

    class Pango:
        def pango_font_map_create_context(self, _font_map):
            return "context"

        def pango_language_from_string(self, tag):
            assert tag == b"ara"
            return "language"

        def pango_context_set_language(self, context, language):
            assert (context, language) == ("context", "language")

        def pango_font_description_new(self):
            return "description"

        def pango_font_map_load_font(self, _font_map, context, description):
            assert (context, description) == ("context", "description")
            return "font"

        def pango_font_description_free(self, description):
            assert description == "description"

        def pango_font_get_hb_font(self, font):
            assert font == "font"
            return "hb-font"

    class Harfbuzz:
        def hb_font_get_face(self, hb_font):
            assert hb_font == "hb-font"
            return "face"

        def hb_tag_from_string(self, tag, length):
            assert (tag, length) == (b"cmap", 4)
            return tag

        def hb_face_reference_table(self, face, tag):
            assert (face, tag) == ("face", b"cmap")
            return "blob"

        def hb_blob_get_data(self, blob, length):
            assert blob == "blob"
            length[0] = len(table)
            return data

        def hb_blob_destroy(self, blob):
            assert blob == "blob"

    class GObject:
        def g_object_unref(self, obj):
            unrefed.append(obj)

    return ffi, GObject(), Pango(), Harfbuzz(), unrefed


def test_the_probe_reads_the_cmap_of_the_font_the_map_resolves():
    """A cmap that maps the sample is the machine saying *I draw this* (#74)."""
    from postulo.documents import fonts

    ffi, gobject, pango, harfbuzz, unrefed = _mocked_probe_handles(_cmap12([(0x0600, 0x06FF, 5)]))
    answer = fonts._script_drawable(ffi, "font-map", gobject, pango, harfbuzz, "ara", "\u0628")
    assert answer is True
    # The font the map loaded and the context are ours to unref; the interned
    # PangoLanguage is Pango's, and it is left alone.
    assert unrefed == ["font", "context"]


def test_the_probe_says_no_where_the_cmap_does_not_map_the_sample():
    """Named for the language but lacking the glyph is a document of boxes (#74)."""
    from postulo.documents import fonts

    ffi, gobject, pango, harfbuzz, _unrefed = _mocked_probe_handles(_cmap12([(0x1200, 0x137F, 10)]))
    answer = fonts._script_drawable(ffi, "font-map", gobject, pango, harfbuzz, "ara", "\u0628")
    assert answer is False


def test_the_check_says_cannot_check_where_it_cannot_be_asked():
    """A machine without Pango answers ``None``, never a guess (#74)."""
    from postulo.documents import fonts
    from postulo.documents.pdf import _is_importable

    if _is_importable("weasyprint"):
        pytest.skip("this machine can be asked; the check is answered, not skipped")
    assert fonts.renderable_scripts() is None


def test_every_script_the_declaration_knows_the_check_can_probe():
    """The two tables are two views of the same fact; they are held to each other (#74).

    Latin is the one script with no probe by design: it is what every font the map can
    resolve already draws, so the check never asks about it.
    """
    from postulo.core import languages
    from postulo.documents import fonts

    unprobed_offered = languages.scripts_offered() - set(fonts.PROBES)
    assert not unprobed_offered, f"offered but the check cannot probe: {sorted(unprobed_offered)}"
    unprobed_known = set(COVERAGE) - {"Latin"} - set(fonts.PROBES)
    assert not unprobed_known, (
        f"the declaration knows but the check cannot probe: {sorted(unprobed_known)}"
    )


def test_the_probe_of_a_script_is_a_character_of_that_script():
    """A probe that is not in the script's block would pass for the wrong reason."""
    import unicodedata

    from postulo.documents import fonts

    for script, (_tag, sample) in fonts.PROBES.items():
        name = unicodedata.name(sample)
        assert script.upper() in name or "CJK" in name, (script, sample, name)
