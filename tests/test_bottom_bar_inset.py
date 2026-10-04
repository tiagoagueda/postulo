"""The bottom bar counts the safe-area inset only in an installed app (#338)."""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "assets" / "css" / "app.css").read_text(
    encoding="utf-8"
)


def test_the_bar_does_not_pad_for_a_safe_area_inset_in_a_browser_tab():
    bar = re.search(r"@media \(width < 48rem\) \{\s*\.nav-main \{(.*?)\n  \}", CSS, re.S)
    assert bar and "env(safe-area-inset-bottom)" not in bar.group(1)


def test_the_inset_applies_only_to_an_installed_app():
    match = re.search(
        r"@media \(display-mode: standalone\), \(display-mode: fullscreen\) \{\s*"
        r"\.nav-main \{[^}]*env\(safe-area-inset-bottom\)",
        CSS,
    )
    assert match
