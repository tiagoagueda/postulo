"""What a page says with colour has to be sayable at 3:1 (#274).

axe measures text against its background on every page; it does not measure the boundary
of a field, which SC 1.4.11 holds to 3:1 as well, and Postulo's was about 1.5:1 in both
themes. The tokens live in `assets/css/app.css`, so the ratio is computed from them here,
the way a person would with a contrast checker, and held.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css").read_text("utf-8")

TOKEN = re.compile(r"--color-([\w-]+):\s*oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)")


def luminance(lightness: float, chroma: float, hue: float) -> float:
    """Relative luminance of an oklch colour, through OKLab and linear sRGB."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lms = (l_**3, m_**3, s_**3)
    red = 4.0767416621 * lms[0] - 3.3077115913 * lms[1] + 0.2309699292 * lms[2]
    green = -1.2684380046 * lms[0] + 2.6097574011 * lms[1] - 0.3413193965 * lms[2]
    blue = -0.0041960863 * lms[0] - 0.7034186147 * lms[1] + 1.7076147010 * lms[2]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: float, second: float) -> float:
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def tokens() -> tuple[dict[str, float], dict[str, float]]:
    """Luminance per token, for the light theme and then with the dark overrides on top."""
    dark_block = CSS.index("@variant dark {")
    dark_end = CSS.index("}", CSS.index("--color-input", dark_block))
    light: dict[str, float] = {}
    dark: dict[str, float] = {}
    for match in TOKEN.finditer(CSS):
        value = luminance(float(match.group(2)), float(match.group(3)), float(match.group(4)))
        if dark_block <= match.start() < dark_end:
            dark[match.group(1)] = value
        elif match.group(1) not in light:
            light[match.group(1)] = value
    # Tokens defined as a reference to another token, in the dark block.
    for match in re.finditer(
        r"--color-([\w-]+):\s*var\(--color-([\w-]+)\)", CSS[dark_block:dark_end]
    ):
        known = {**light, **dark}
        if match.group(2) in known:  # a Tailwind red is not one of ours, and not needed
            dark[match.group(1)] = known[match.group(2)]
    return light, {**light, **dark}


def test_the_field_boundary_clears_three_to_one_in_both_themes():
    light, dark = tokens()
    white = 1.0
    assert contrast(light["input"], white) >= 3, "border against the field, light"
    assert contrast(light["input"], light["ink-50"]) >= 3, "border against the page, light"
    assert contrast(dark["input"], dark["ink-950"]) >= 3, "border against the field and page, dark"


def test_the_parser_reads_the_tokens_it_is_supposed_to():
    light, dark = tokens()
    assert "ink-300" in light and "input" in light and "brand-600" in light
    assert dark["ink-400"] != light["ink-400"], "the dark override was read"
    assert math.isclose(luminance(1.0, 0, 0), 1.0, abs_tol=0.01)
    assert math.isclose(luminance(0.0, 0, 0), 0.0, abs_tol=0.001)


def test_focus_on_a_field_is_an_opaque_ring():
    """At 20% alpha the ring read at about 1.2:1 against white -- the faintest focus in the
    application, on the controls people type into (#274)."""
    # The rule on the element itself, in the base layer, since #290 retired `field-input`.
    start = CSS.index("input:where(")
    field = CSS[start : CSS.index("}", start)]
    assert "focus:ring-2 focus:ring-brand-500 " in field or "focus:ring-brand-500\n" in field
    assert "ring-brand-500/20" not in field


def test_the_current_navigation_link_is_marked_by_more_than_colour():
    """The default, which is what the conformance claim rests on."""
    active = CSS[CSS.index(".nav-link-active {") : CSS.index("}", CSS.index(".nav-link-active {"))]
    assert "font-semibold" in active and "underline" in active


def test_switching_the_underline_off_takes_only_the_underline():
    """#289 made it a preference. Off must not leave colour as the only cue, so the rule
    may drop the decoration and nothing else -- the weight and the tint stay on the class
    it is layered over."""
    rule = CSS[
        CSS.index('body[data-nav-underline="off"] .nav-link-active') : CSS.index(
            "}", CSS.index('body[data-nav-underline="off"] .nav-link-active')
        )
    ]
    assert "text-decoration-line: none" in rule
    for cue in ("font-", "background", "color:", "bg-ink"):
        assert cue not in rule, f"the preference may not touch {cue!r}"


def test_forced_colours_underlines_it_whatever_the_preference_says():
    """There the tint is discarded and the weight is flattened, so the underline is the
    only cue left; the preference does not reach that far (#289)."""
    forced = CSS[CSS.index("@media (forced-colors: active)") :]
    rule = forced[
        forced.index('body[data-nav-underline="off"] .nav-link-active') : forced.index(
            "}", forced.index('body[data-nav-underline="off"] .nav-link-active')
        )
    ]
    assert "text-decoration-line: underline" in rule


def test_more_contrast_is_answered():
    assert "@media (prefers-contrast: more)" in CSS


# ------------------------------------------------------------ the translation bar


COMPILED = (
    Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "css" / "app.css"
).read_text("utf-8")

#: Tailwind's own palette as the compiled stylesheet carries it: `oklch(59.6% 0.145 163.225)`.
PALETTE = re.compile(r"--color-([\w-]+):\s*oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)")


def palette() -> dict[str, float]:
    return {
        match.group(1): luminance(
            float(match.group(2)) / 100, float(match.group(3)), float(match.group(4))
        )
        for match in PALETTE.finditer(COMPILED)
    }


def bar_fills() -> dict[str, tuple[str, str]]:
    """Each part of the bar and the colour it takes in the light theme and in the dark,
    read from the rule that paints it rather than written down a second time here."""
    fills = {}
    for part in ("reviewed", "draft", "untranslated"):
        start = CSS.index(f'.translation-bar > [data-part="{part}"] {{')
        rule = CSS[start : CSS.index("}", start)]
        light = re.search(r"(?<![:\w-])fill-([\w-]+)", rule).group(1)
        dark = re.search(r"dark:fill-([\w-]+)", rule)
        fills[part] = (light, dark.group(1) if dark else light)
    return fills


def test_every_part_of_the_translation_bar_clears_three_to_one_in_both_themes():
    """SC 1.4.11, measured the way a contrast checker would (#312).

    Against the card the bar sits on and against the tint of the row under the pointer.
    Not against one another: three colours and a background cannot all be 3:1 apart, so
    the parts never touch and the card between them is their neighbour.
    """
    from postulo.core import languages

    light, dark = tokens()
    colours = palette()
    light, dark = {**colours, **light}, {**colours, **dark}
    grounds = {
        "light": {"card": 1.0, "row under the pointer": light["ink-100"]},
        "dark": {"card": dark["card"], "row under the pointer": dark["ink-800"]},
    }
    fills = bar_fills()
    assert fills["reviewed"][0].startswith("emerald") and fills["draft"][0].startswith("amber")
    for part, (light_token, dark_token) in fills.items():
        for theme, token, table in (("light", light_token, light), ("dark", dark_token, dark)):
            for ground, value in grounds[theme].items():
                ratio = contrast(table[token], value)
                assert ratio >= 3, f"{part} ({token}) on the {ground}, {theme}: {ratio:.2f}"
    assert languages.BAR_GAP >= 1, "the parts touch, so each would need 3:1 from the next"


def test_the_translation_bar_is_painted_in_system_colours_when_forced():
    """Three parts that still differ from one another under a high-contrast theme."""
    forced = CSS[CSS.rindex("@media (forced-colors: active)") :]
    assert ".translation-bar {\n    forced-color-adjust: none;" in forced
    painted = {
        part: re.search(
            rf'\.translation-bar > \[data-part="{part}"\] \{{\s*fill: (\w+);', forced
        ).group(1)
        for part in ("reviewed", "draft", "untranslated")
    }
    assert len(set(painted.values())) == 3, painted
