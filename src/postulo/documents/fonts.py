"""Whether this instance can actually draw the scripts Postulo offers.

``tests/test_fonts.py`` checks a *declaration*: it reads the Dockerfile and insists
the image installs a package that draws every script Postulo offers. The operator's
machine is not the Dockerfile. An instance running from a virtualenv under systemd,
or on a distribution whose font packages are named differently, draws with whatever
that machine has — and until now the first sign of a gap was a CV that went out full
of boxes (#74).

So this asks the question a render would have asked, and it asks it the way a render
asks it. For each script the offered languages need, the same font map WeasyPrint
resolves through is asked which font draws a text in that script's language, and the
answer is checked against that font's own cmap — the table of characters it actually
contains. A font that fontconfig *names* for a language is a match; a cmap entry is
the fact, and a match without the fact is a document that will come out boxes.

Nothing is asked of the network, and nothing is read but the font tables this machine
has. On a machine without WeasyPrint's native libraries the question cannot be
answered at all, and the answer is ``None`` rather than a guess: the page and the
command say *cannot check*, and that is the truth.
"""

from __future__ import annotations

import struct

#: Script → the probe the renderer's own matching would answer for it.
#:
#: The first member is the language fontconfig matches by — ISO 639-3, the code its
#: per-font language sets are computed in — and it is a language Postulo offers in
#: that script, so the probe is a case the application actually has. The second is
#: the character whose cmap entry is the fact: one from the core of the script, not
#: an edge a font may legitimately leave to a neighbour. A script is drawable when
#: the font the map resolves for that language maps that character.
#:
#: The map runs ahead of the language phases on purpose: #71's scripts are here
#: before #71's languages arrive, so the phase that brings them cannot quietly ship
#: a document nobody can read, the way the African phase once did.
PROBES: dict[str, tuple[str, str]] = {
    "Arabic": ("ara", "\u0628"),
    "Bengali": ("ben", "\u0985"),
    "Cyrillic": ("bul", "\u0414"),
    "Devanagari": ("hin", "\u0905"),
    "Ethiopic": ("amh", "\u1200"),
    "Greek": ("ell", "\u0395"),
    "Gujarati": ("guj", "\u0a85"),
    "Gurmukhi": ("pan", "\u0a05"),
    "Hangul": ("kor", "\ud55c"),
    "Han": ("zho", "\u4e2d"),
    "Hebrew": ("heb", "\u05d1"),
    "Hiragana": ("jpn", "\u3042"),
    "Katakana": ("jpn", "\u30ab"),
    "Khmer": ("khm", "\u1780"),
    "Lao": ("lao", "\u0e81"),
    "Myanmar": ("mya", "\u1000"),
    "Sinhala": ("sin", "\u0d85"),
    "Tamil": ("tam", "\u0b85"),
    "Telugu": ("tel", "\u0c05"),
    "Thai": ("tha", "\u0e01"),
    "Tifinagh": ("tfr", "\u2d30"),
}


def cmap_covers(cmap_table: bytes, codepoint: int) -> bool:
    """Whether a character has a glyph in a font's cmap table.

    ``cmap_table`` is the raw ``cmap`` table of a face, as
    ``hb_face_reference_table`` hands it over. The subtables are read in the order a
    platform picks them, and formats 4 and 12 are the two that carry non-Latin text:
    a table with neither is one that cannot say yes, and it says no. OpenType writes
    its integers big-endian, and the unpacks below are written to match.
    """
    if len(cmap_table) < 4:
        return False
    (num_tables,) = struct.unpack_from(">H", cmap_table, 2)
    if 4 + num_tables * 8 > len(cmap_table):
        return False
    candidates: list[tuple[int, int, int]] = []
    for index in range(num_tables):
        platform_id, encoding_id, offset = struct.unpack_from(">HHI", cmap_table, 4 + index * 8)
        candidates.append((platform_id, encoding_id, offset))

    # A Windows-format-4 first, then the Unicode ones: the order the platforms that
    # render Postulo's documents pick their subtables in.
    preferred = ((3, 1), (0, 4), (3, 0), (0, 3), (0, 6))
    for platform_id, encoding_id in preferred:
        for found_platform, found_encoding, offset in candidates:
            if (found_platform, found_encoding) != (platform_id, encoding_id):
                continue
            if offset + 2 > len(cmap_table):
                continue
            (format_id,) = struct.unpack_from(">H", cmap_table, offset)
            if format_id == 12 and _format12_covers(cmap_table, offset, codepoint):
                return True
            if format_id == 4 and _format4_covers(cmap_table, offset, codepoint):
                return True
    return False


def _format12_covers(table: bytes, offset: int, codepoint: int) -> bool:
    """Format 12: sorted ranges of ``(start, end) → first glyph``."""
    if offset + 16 > len(table):
        return False
    (num_ranges,) = struct.unpack_from(">I", table, offset + 12)
    for index in range(num_ranges):
        start, end, first_glyph = struct.unpack_from(">III", table, offset + 16 + index * 12)
        if start <= codepoint <= end:
            return first_glyph != 0
    return False


def _format4_covers(table: bytes, offset: int, codepoint: int) -> bool:
    """Format 4: the segmented table every Latin-era font still carries.

    The layout, one word per entry: the seven header words, then ``endCode`` for
    every segment, one padding word, then ``startCode``, ``idDelta`` and
    ``idRangeOffset`` for every segment in that order, and after the last
    range offset the glyph array the nonzero offsets reach into.
    """
    if offset + 14 > len(table):
        return False
    (segment_count, _) = struct.unpack_from(">HH", table, offset + 6)
    segment_count //= 2
    if segment_count == 0 or offset + 14 + segment_count * 6 > len(table):
        return False
    base = offset + 14
    for index in range(segment_count):
        end_code = struct.unpack_from(">H", table, base + index * 2)[0]
        start_code = struct.unpack_from(">H", table, base + segment_count * 2 + 2 + index * 2)[0]
        delta = struct.unpack_from(">h", table, base + segment_count * 4 + 2 + index * 2)[0]
        range_offset = struct.unpack_from(">H", table, base + segment_count * 6 + 2 + index * 2)[0]
        if not (start_code <= codepoint <= end_code):
            continue
        if range_offset == 0:
            return (codepoint + delta) & 0xFFFF != 0
        # The offset is measured from just after this segment's own range-offset word.
        glyph_index = (
            base + segment_count * 6 + 2 + index * 2 + range_offset + (codepoint - start_code) * 2
        )
        if glyph_index + 2 > len(table):
            return False
        (glyph,) = struct.unpack_from(">H", table, glyph_index)
        return glyph != 0
    return False


def renderable_scripts() -> dict[str, bool] | None:
    """For every script the offered languages need, whether this machine draws it.

    ``None`` when WeasyPrint's native libraries are absent from this machine: the
    question cannot be answered here, and an answer of *no* would be a lie. The
    page and the command say what they actually know.
    """
    from postulo.core import languages
    from postulo.documents.pdf import _is_importable

    offered = languages.scripts_offered()
    if not offered:
        return {}
    if not _is_importable("weasyprint"):
        return None
    try:
        from weasyprint.text.ffi import ffi, fontconfig, gobject, harfbuzz, pango, pangoft2
    except Exception:
        # Deliberately broad, the way ``pdf._is_importable`` is: a native library
        # missing is an OSError, a library present but broken is something else,
        # and every one of them means the same thing here.
        return None

    # The same wiring as WeasyPrint's own FontConfiguration: a fresh fontconfig
    # configuration handed to the font map that does the resolving.
    config = fontconfig.FcInitLoadConfigAndFonts()
    font_map = ffi.gc(pangoft2.pango_ft2_font_map_new(), gobject.g_object_unref)
    pangoft2.pango_fc_font_map_set_config(ffi.cast("PangoFcFontMap *", font_map), config)
    fontconfig.FcConfigDestroy(config)

    answer: dict[str, bool] = {}
    for script in sorted(offered):
        probe = PROBES.get(script)
        if probe is None:
            # A script the language list offers but this table cannot probe: it
            # cannot be passed off as drawn. The test in tests/test_fonts.py holds
            # the two tables together, so this is a bug the suite finds, not a
            # state the page has to survive.
            answer[script] = False
            continue
        language_tag, sample = probe
        answer[script] = _script_drawable(
            ffi, font_map, gobject, pango, harfbuzz, language_tag, sample
        )
    return answer


def _script_drawable(
    ffi, font_map, gobject, pango, harfbuzz, language_tag: str, sample: str
) -> bool:
    """The font the map resolves for that language, and its cmap's word on the sample."""
    context = pango.pango_font_map_create_context(font_map)
    try:
        language = pango.pango_language_from_string(language_tag.encode("utf-8"))
        if language == ffi.NULL:
            return False
        pango.pango_context_set_language(context, language)
        description = pango.pango_font_description_new()
        try:
            font = pango.pango_font_map_load_font(font_map, context, description)
        finally:
            pango.pango_font_description_free(description)
        if font == ffi.NULL:
            return False
        try:
            # The hb_ calls go to HarfBuzz's own handle, the way WeasyPrint's shaping does:
            # its functions are a library of their own, and the FFI object itself has never
            # carried them — asking it was the first red streak of #74.
            face = harfbuzz.hb_font_get_face(pango.pango_font_get_hb_font(font))
            if face == ffi.NULL:
                return False
            blob = harfbuzz.hb_face_reference_table(face, harfbuzz.hb_tag_from_string(b"cmap", 4))
            if blob == ffi.NULL:
                return False
            try:
                length = ffi.new("unsigned int *")
                data = harfbuzz.hb_blob_get_data(blob, length)
                if data == ffi.NULL:
                    return False
                return cmap_covers(bytes(ffi.buffer(data)[: length[0]]), ord(sample))
            finally:
                harfbuzz.hb_blob_destroy(blob)
        finally:
            gobject.g_object_unref(font)
    finally:
        # PangoLanguage is interned by Pango: pango_language_from_string hands out
        # (transfer none) objects held in a process-lifetime table, so they must
        # never be unref'd (doing so corrupts Pango and segfaults). The context is
        # a caller-owned GObject and is unref'd, matching WeasyPrint.
        gobject.g_object_unref(context)
