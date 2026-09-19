"""The stylesheet answers forced colours and print (#277); the browser suite checks what
that looks like, and this holds the rules to their shape without a browser."""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "css"
#: Both source stylesheets: the project's own, and the style pack that paints Basecoat's
#: classes, which is where `.btn` and the menu items get their forced-colours border.
CSS = (ASSETS / "app.css").read_text("utf-8") + (ASSETS / "basecoat.css").read_text("utf-8")
COMPILED = (
    Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "css" / "app.css"
).read_text("utf-8")


def blocks(css: str, query: str) -> list[str]:
    """The bodies of every `@media <query>` block, braces balanced."""
    found = []
    for match in re.finditer(r"@media " + re.escape(query) + r"\s*\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        found.append(css[match.end() : index - 1])
    return found


def test_every_control_keeps_a_border_under_forced_colours():
    forced = "\n".join(blocks(CSS, "(forced-colors: active)"))
    for selector in (".btn", ".menu-item", ".nav-link", ".chip-remove"):
        assert re.search(rf"{re.escape(selector)},?\s", forced), selector
    assert "border: 1px solid ButtonText" in forced
    assert "forced-color-adjust: none" in forced and "background-color: Highlight" in forced
    assert "border-inline-end: 1px solid ButtonText" in forced, "the resize handle"


def test_print_turns_the_dark_scale_over_and_leaves_the_chrome_off():
    (printed,) = blocks(CSS, "print")
    assert ':root[data-theme="dark"]' in printed
    assert "--color-ink-100: oklch(0.19" in printed, "100 becomes 900's shade"
    assert "--color-ink-900: oklch(0.968" in printed, "and 900 becomes 100's"
    assert "overflow: visible" in printed
    for chrome in ("[data-site-header]", ".skip-link", "footer", ".page-alert"):
        assert chrome in printed, chrome


def test_both_blocks_reach_the_compiled_stylesheet():
    assert "@media print" in COMPILED
    assert COMPILED.count("@media (forced-colors: active)") >= 2
