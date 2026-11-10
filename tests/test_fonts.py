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
import unicodedata
from pathlib import Path

import pytest

from postulo.core import languages

DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "Dockerfile"

#: Which Debian font package covers which script, by its ISO 15924 code, which is how a
#: language tag and `languages.SCRIPTS` name one (#337). Deliberately short: adding a script
#: means deciding which package draws it, which is a decision worth making explicitly.
COVERAGE: dict[str, tuple[str, ...]] = {
    "Latn": ("fonts-dejavu-core", "fonts-noto-core"),
    "Grek": ("fonts-dejavu-core", "fonts-noto-core"),
    "Cyrl": ("fonts-dejavu-core", "fonts-noto-core"),
    "Arab": ("fonts-noto-core",),
    "Armn": ("fonts-noto-core",),
    "Geor": ("fonts-noto-core",),
    "Ethi": ("fonts-noto-core",),
    "Hebr": ("fonts-noto-core",),
    "Deva": ("fonts-noto-core",),
    "Beng": ("fonts-noto-core",),
    "Thai": ("fonts-noto-core",),
    # CJK is its own package and its own size. It arrived with the two Chinese catalogues
    # (#728), which state their script (`zh-Hans`, `zh-Hant`) rather than leave it to the
    # language, so what is offered is `Hans` and `Hant`, not `Hani`.
    "Hani": ("fonts-noto-cjk",),
    "Hans": ("fonts-noto-cjk",),
    "Hant": ("fonts-noto-cjk",),
    "Hira": ("fonts-noto-cjk",),
    "Hang": ("fonts-noto-cjk",),
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
    """A language whose script is not recorded is one nobody checked the fonts for.

    Which languages are not written in Latin is the registry's to say, not a list kept
    here by hand: a fixed list is what let Armenian and Georgian through (#455).
    """
    from postulo.core.languages import NATIVE_NAMES, SCRIPTS
    from tests.test_language_registry import registry

    for code in NATIVE_NAMES:
        usual = registry()["language", languages.primary(code)].get("Suppress-Script", "")
        if usual and usual != "Latn":
            assert languages.primary(code) in SCRIPTS or languages.script_of(code) != "Latn", (
                f"{code} is not written in Latin and says nothing about it"
            )
            assert languages.script_of(code) in languages.scripts_offered(), code


def test_the_scripts_offered_are_the_ones_the_languages_need():
    offered = languages.scripts_offered()
    assert {"Arab", "Ethi", "Grek", "Cyrl"} <= offered
    # The scripts #728 brought in with its languages: Hindi, Bengali and the two Chinese.
    assert {"Deva", "Beng", "Hans", "Hant"} <= offered
    # Nothing else from a later phase has crept in without its fonts being decided.
    assert "Hani" not in offered and "Thai" not in offered


def test_the_dockerfile_reader_ignores_comments():
    """The comment above the install names packages; a comment does not install one."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "# tests/test_fonts.py holds this list" in text, "the comment moved; check this still"
    # The Dockerfile's comment about the door for extra packages names this one; it is a
    # comment, so it is not an install.
    assert "fonts-noto-extra" in text
    assert "fonts-noto-extra" not in installed_font_packages()


# ------------------------------------- the decision #74 made, and what #728 changed in it


def test_cjk_is_in_the_default_image_since_chinese_is_offered():
    """#74 left CJK out of the image, for a language almost none of the installations would
    use, and left a door (POSTULO_EXTRA_APT_PACKAGES). #728 offers Chinese in 0.5.0, and a
    language that is offered without a font that draws it is a CV of empty boxes, so the
    package is in the image and the test above holds the two together."""
    assert "fonts-noto-cjk" in installed_font_packages()


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


def _mocked_probe_handles(table: bytes, language: bytes = b"ara", resolved: bytes = b""):
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
    family_name = ffi.new("char[]", resolved)
    unrefed = []
    asked_for = []

    class Pango:
        def pango_font_map_create_context(self, _font_map):
            return "context"

        def pango_language_from_string(self, tag):
            assert tag == language
            return "language"

        def pango_context_set_language(self, context, language):
            assert (context, language) == ("context", "language")

        def pango_font_description_new(self):
            return "description"

        def pango_font_map_load_font(self, _font_map, context, description):
            assert (context, description) == ("context", "description")
            return "font"

        def pango_font_description_free(self, description):
            assert description in ("description", "described")

        def pango_font_description_set_family(self, description, family):
            assert description == "description"
            asked_for.append(family)

        def pango_font_describe_with_absolute_size(self, font):
            assert font == "font"
            return "described"

        def pango_font_description_get_family(self, described):
            assert described == "described"
            return family_name

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

    pango = Pango()
    pango.asked_for = asked_for
    return ffi, GObject(), pango, Harfbuzz(), unrefed


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
    unprobed_known = set(COVERAGE) - {"Latn"} - set(fonts.PROBES)
    assert not unprobed_known, (
        f"the declaration knows but the check cannot probe: {sorted(unprobed_known)}"
    )


def test_the_probe_of_a_script_is_a_character_of_that_script():
    """A probe that is not in the script's block would pass for the wrong reason."""
    import unicodedata

    from postulo.documents import fonts

    for script, (_tag, sample) in fonts.PROBES.items():
        name = unicodedata.name(sample)
        written = languages.script_name(script)
        assert script in languages.SCRIPT_NAMES, f"{script} has no name to say it with"
        assert written.upper() in name or "CJK" in name, (script, sample, name)


# ------------------------------------------------ the style a script is written in (#1054)

#: Which Debian font package draws which style of a script, as `COVERAGE` does for scripts.
STYLE_COVERAGE: dict[str, tuple[str, ...]] = {
    "Aran": ("fonts-noto-core",),
}


def test_urdu_is_asked_about_in_nastaliq():
    """Urdu is offered (#1051), so the question about its fonts is the style it is written in,
    under ISO 15924's code for that variant, and in words on the page and the command."""
    from postulo.documents import fonts

    assert languages.script_of("ur") == "Arab", "the script is still Arabic; the style is not"
    assert languages.styles_offered() == {"Aran"}
    assert languages.script_name("Aran") == "Arabic (Nastaliq)"
    assert set(languages.STYLES.values()) <= set(fonts.STYLE_PROBES)
    for _language, family, sample in fonts.STYLE_PROBES.values():
        assert "NASTALIQ" in family.upper()
        assert unicodedata.name(sample).startswith("ARABIC LETTER")


def test_the_image_installs_the_family_every_offered_style_is_drawn_in():
    installed = installed_font_packages()
    for style in sorted(languages.styles_offered()):
        options = STYLE_COVERAGE.get(style)
        assert options, f"{style} is offered but no font package is recorded for it"
        assert installed & set(options), f"the image cannot draw {style}: install one of {options}"


def test_the_style_probe_asks_for_the_family_and_takes_only_that_family():
    """Named for Urdu and drawn by Naskh is the wrong style, however much it maps (#1054)."""
    from postulo.documents import fonts

    table = _cmap12([(0x0600, 0x06FF, 5)])
    ffi, gobject, pango, harfbuzz, unrefed = _mocked_probe_handles(
        table, language=b"urd", resolved=b"Noto Nastaliq Urdu"
    )
    answer = fonts._script_drawable(
        ffi, "font-map", gobject, pango, harfbuzz, "urd", "\u06ba", family="Noto Nastaliq Urdu"
    )
    assert answer is True
    assert pango.asked_for == [b"Noto Nastaliq Urdu"]
    assert unrefed == ["font", "context"]

    ffi, gobject, pango, harfbuzz, unrefed = _mocked_probe_handles(
        table, language=b"urd", resolved=b"Noto Naskh Arabic"
    )
    answer = fonts._script_drawable(
        ffi, "font-map", gobject, pango, harfbuzz, "urd", "\u06ba", family="Noto Nastaliq Urdu"
    )
    assert answer is False, "the map's substitute for a family it does not have"
    assert unrefed == ["font", "context"]


def _answered(monkeypatch, nastaliq: bool) -> None:
    from postulo.documents import fonts

    answer = dict.fromkeys(languages.scripts_offered(), True)
    answer["Aran"] = nastaliq
    monkeypatch.setattr(fonts, "renderable_scripts_now", lambda: answer)


def test_check_fonts_says_whether_urdu_is_drawn_in_nastaliq(monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    _answered(monkeypatch, nastaliq=True)
    out = StringIO()
    call_command("check_fonts", stdout=out)
    assert "draws      Arabic (Nastaliq)" in out.getvalue()
    assert "though not" not in out.getvalue()

    _answered(monkeypatch, nastaliq=False)
    out = StringIO()
    # Said, and not a failure: Urdu is still drawn, legibly, in Naskh.
    call_command("check_fonts", stdout=out)
    assert "cannot draw  Arabic (Nastaliq)" in out.getvalue()
    assert "though not in every style" in out.getvalue()


def test_the_image_names_libthai_for_breaking_thai():
    """Pango breaks a line of Thai with libthai's dictionary (#1052). Debian's Pango depends
    on it today; the image names it so that a later Pango cannot drop it unnoticed."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r"\blibthai0\b", body), "the image does not install libthai0"


# ------------------------------------------ asked once, not at every visit (#1133)


class _Machine:
    """A machine's fonts, stood in for: what it draws, and how often it was asked.

    `loads` counts the times fontconfig was loaded (`fonts._handles`) and `probes` the
    fonts looked up (`fonts._script_drawable`), the two costs a visit of the Overview
    paid. Nothing here depends on the fonts of the machine the suite runs on, and it runs
    where WeasyPrint's libraries cannot be loaded at all.
    """

    def __init__(self) -> None:
        self.loads = 0
        self.probes: list[tuple[str, str]] = []
        #: The languages, and the families, this machine has no font for.
        self.missing: set[str] = set()
        self.askable = True

    def handles(self):
        self.loads += 1
        if not self.askable:
            return None
        return "ffi", f"font-map-{self.loads}", "gobject", "pango", "harfbuzz"

    def drawable(
        self, ffi, font_map, gobject, pango, harfbuzz, language_tag, sample, *, family=None
    ):
        self.probes.append((font_map, family or language_tag))
        return (family or language_tag) not in self.missing


@pytest.fixture
def machine(monkeypatch):
    from postulo.documents import fonts

    stood_in = _Machine()
    monkeypatch.setattr(fonts, "_handles", stood_in.handles)
    monkeypatch.setattr(fonts, "_script_drawable", stood_in.drawable)
    return stood_in


def _everything_offered() -> list[str]:
    """The codes an answer carries, in its order: the scripts, then the styles."""
    return sorted(languages.scripts_offered()) + sorted(languages.styles_offered())


@pytest.mark.django_db
def test_the_overview_asks_the_fonts_once_and_not_at_every_visit(client, machine):
    """Every visit loaded fontconfig again and looked a font up for every script (#1133).

    Loading fontconfig reads the cache of every font the machine has, several hundred faces
    in the image, to say what it said at the last visit. A machine that draws everything
    offered is asked once by a process, and the row reads the same afterwards.
    """
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    admin = get_user_model().objects.create_user(
        email="admin@example.org", password="not-a-real-password", is_staff=True
    )
    client.force_login(admin)

    def row() -> str:
        html = client.get(reverse("server:overview")).content.decode()
        found = html[html.index("data-fonts=") :]
        return found[: found.index("</dd>")]

    first = row()
    assert (machine.loads, len(machine.probes)) == (1, len(_everything_offered()))

    second = row()
    assert (machine.loads, len(machine.probes)) == (1, len(_everything_offered())), (
        "the second visit asked the fonts again"
    )
    assert second == first
    assert 'data-fonts="listed"' in second and "Arabic (Nastaliq)" in second


def test_the_answer_kept_is_the_answer_given_in_the_same_order(machine):
    """What is remembered is what the machine said, and a caller's copy is its own (#1133)."""
    from postulo.documents import fonts

    first = fonts.renderable_scripts()
    assert list(first) == _everything_offered()
    assert all(first.values())

    first.clear()
    again = fonts.renderable_scripts()

    assert list(again) == _everything_offered()
    assert all(again.values())
    assert machine.loads == 1


@pytest.mark.parametrize(
    ("missing", "code"),
    [("ara", "Arab"), ("Noto Nastaliq Urdu", "Aran")],
    ids=["a script", "a style"],
)
def test_an_answer_with_something_missing_is_asked_again(machine, missing, code):
    """Only a complete answer is kept (#1133).

    Something missing is what an administrator installs a font package to put right, and
    a font installed a minute ago has to count at the next visit, without a restart. So
    the machine is asked again until it draws everything, and not after.
    """
    from postulo.documents import fonts

    machine.missing = {missing}
    assert fonts.renderable_scripts()[code] is False
    assert fonts.renderable_scripts()[code] is False
    assert machine.loads == 2

    machine.missing = set()
    assert fonts.renderable_scripts()[code] is True
    assert machine.loads == 3, "the fonts as they are now, not the ones loaded before"

    assert fonts.renderable_scripts()[code] is True
    assert machine.loads == 3


def test_a_machine_that_could_not_be_asked_is_asked_again(machine):
    """*Cannot be checked* is not an answer to keep either (#1133)."""
    from postulo.documents import fonts

    machine.askable = False
    assert fonts.renderable_scripts() is None
    assert fonts.renderable_scripts() is None

    machine.askable = True
    assert fonts.renderable_scripts() == dict.fromkeys(_everything_offered(), True)


def test_a_language_offered_since_is_asked_about(machine, monkeypatch):
    """The answer kept is for what was offered when it was given (#1133).

    An administrator who offers a language in another script, or one set in a style of its
    own, is shown that one too, asked of the fonts as they are then.
    """
    from postulo.documents import fonts

    monkeypatch.setattr(languages, "scripts_offered", lambda: {"Grek"})
    monkeypatch.setattr(languages, "styles_offered", lambda: set())
    assert fonts.renderable_scripts() == {"Grek": True}
    assert fonts.renderable_scripts() == {"Grek": True}
    assert machine.loads == 1

    monkeypatch.setattr(languages, "scripts_offered", lambda: {"Grek", "Arab"})
    assert fonts.renderable_scripts() == {"Arab": True, "Grek": True}
    assert machine.loads == 2

    monkeypatch.setattr(languages, "styles_offered", lambda: {"Aran"})
    assert fonts.renderable_scripts() == {"Arab": True, "Grek": True, "Aran": True}
    assert machine.loads == 3

    assert fonts.renderable_scripts() == {"Arab": True, "Grek": True, "Aran": True}
    assert machine.loads == 3


def test_check_fonts_asks_the_machine_as_it_is_now(machine):
    """The command is the fresh check, whatever the process remembers (#1133)."""
    from io import StringIO

    from django.core.management import CommandError, call_command

    from postulo.documents import fonts

    assert all(fonts.renderable_scripts().values())
    assert machine.loads == 1

    out = StringIO()
    call_command("check_fonts", stdout=out)
    assert machine.loads == 2, "the command answered from what the process remembered"
    assert "Every script the offered languages need, this machine draws." in out.getvalue()

    machine.missing = {"ara"}
    with pytest.raises(CommandError, match="Arabic would come out as boxes"):
        call_command("check_fonts", stdout=StringIO())
    assert machine.loads == 3


def test_the_questions_the_tests_ask_share_one_font_map(machine):
    """`script_draws` and `family_draws` loaded fontconfig at every call (#1133).

    The files that skip by them ask once in every test. The map they share is this
    machine's own, so it is put down before and after a test that stands in for it.
    """
    from postulo.documents import fonts

    fonts._kept_handles.cache_clear()
    try:
        assert fonts.script_draws("Arab") is True
        assert fonts.script_draws("Grek") is True
        assert fonts.family_draws("Noto Nastaliq Urdu", "urd", "ں") is True
        machine.missing = {"hye"}
        assert fonts.script_draws("Armn") is False
        assert machine.loads == 1
        assert {font_map for font_map, _asked in machine.probes} == {"font-map-1"}
    finally:
        fonts._kept_handles.cache_clear()
