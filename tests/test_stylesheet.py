"""The committed stylesheet is what the templates compile to, checked where it is written (#164).

The compiled CSS is committed, so a template that gains a class and a stylesheet that is not
rebuilt ship a page whose class does nothing -- silently. That is how the report page reached
0.3.0 without two of its own utilities: its button sat out of line and its tallies stacked on
a wide screen, and the only thing that noticed was the `styles` job in CI, after the push.

So the same comparison runs in the ordinary suite, on the machine where the template changed.
It needs the Tailwind CLI the project already pins, and skips where that is not installed --
CI's test job has Node but not `node_modules`, and the `styles` job is what covers it there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "css" / "app.css"
COMPILED = ROOT / "src" / "postulo" / "static" / "css" / "app.css"


def tailwind() -> str | None:
    """The CLI `npm ci` installs, and never one fetched from the network to run a test."""
    return shutil.which("tailwindcss", path=str(ROOT / "node_modules" / ".bin"))


def test_the_committed_stylesheet_is_what_the_templates_compile_to(tmp_path):
    cli = tailwind()
    if cli is None:
        pytest.skip("the Tailwind CLI is not installed here; run `npm ci` to check this")

    fresh = tmp_path / "app.css"
    subprocess.run(  # noqa: S603 - a fixed argument list, and the binary is the project's own
        [cli, "--input", str(SOURCE), "--output", str(fresh)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=180,
    )

    def text(path: Path) -> str:
        # Line endings are the checkout's business, not the build's.
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert text(fresh) == text(COMPILED), (
        "src/postulo/static/css/app.css is stale: a template uses a class the committed "
        "stylesheet does not have, or no longer uses one it does. Run `npm run build:css` "
        "and commit the result."
    )


def test_the_scan_is_the_interface_and_nothing_else():
    """What compiles depends on what the interface is made of -- not on prose, not on where the
    result is written, not on the previous build.

    Three leaks, all found while fixing a stale build (#164):

    - **automatic detection** scanned the whole repository, so a word in a test's docstring,
      the wiki or the changelog could put a class in the stylesheet every page loads -- the
      template lint's own list of forbidden physical utilities was compiling them;
    - **the compiled file** sits inside the scanned tree, and Tailwind skips its output only when
      it is writing to it, so a build written anywhere else read the last build as a source;
    - **the PDF templates** carry their own inline CSS and never load this stylesheet, and the
      scanner read `border-collapse: collapse;` in the report's print template as a class.
    """
    source = SOURCE.read_text(encoding="utf-8")

    assert '@import "tailwindcss" source(none);' in source
    assert '@source "../../src/postulo";' in source
    assert '@source not "../../src/postulo/static/css";' in source
    assert '@source not "../../src/postulo/templates/documents/themes";' in source
    assert '@source not "../../src/postulo/templates/applications/report_print.html";' in source


def test_no_physical_utility_is_compiled_at_all():
    """The template lint forbids naming a side of the page; with the scan fixed, nothing
    compiles one either -- they were only here because the lint's source named them.
    """
    compiled = COMPILED.read_text(encoding="utf-8")

    for physical in (".ml-auto", ".text-left", ".text-right", ".border-l ", ".rounded-r "):
        assert physical not in compiled, physical
