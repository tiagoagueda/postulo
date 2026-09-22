"""The committed stylesheet is what the templates compile to, checked where it is written (#164).

The compiled CSS is committed, so a template that gains a class and a stylesheet that is not
rebuilt ship a page whose class does nothing -- silently. That is how the report page reached
0.3.0 without two of its own utilities: its button sat out of line and its tallies stacked on
a wide screen, and the only thing that noticed was the `styles` job in CI, after the push.

So the same comparison runs in the ordinary suite, on the machine where the template changed.
It needs the Tailwind CLI the project already pins, and skips where that is not installed --
CI's test job has Node but not `node_modules`, and the `styles` job is what covers it there.

The second half of this file is about Basecoat (#262): which of its files are imported, and
that no class is ever defined on both sides of that import.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "css" / "app.css"
STYLE_PACK = ROOT / "assets" / "css" / "basecoat.css"
COMPILED = ROOT / "src" / "postulo" / "static" / "css" / "app.css"
BASECOAT = ROOT / "node_modules" / "basecoat-css"


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


# ------------------------------------------------------------------ basecoat (#262)

IMPORTED = re.compile(r'^@import\s+"basecoat-css(/[^"]*)?"', re.MULTILINE)

#: A class name at the start of a selector, in a stylesheet that has had its `@apply` lines
#: taken out first: those name utilities, not classes the file defines.
DEFINED = re.compile(r"(?<![\w-])\.([a-zA-Z][\w-]*)")
APPLIED = re.compile(r"@apply[^;]*;")
UTILITY = re.compile(r"@utility\s+([\w-]+)")


def basecoat_imports() -> list[str]:
    """The Basecoat paths `app.css` imports, as written."""
    return [match.group(0).split('"')[1] for match in IMPORTED.finditer(SOURCE.read_text("utf-8"))]


def classes_defined_in(text: str) -> set[str]:
    stripped = APPLIED.sub("", re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL))
    return set(DEFINED.findall(stripped)) | set(UTILITY.findall(stripped))


def test_basecoat_arrives_one_component_at_a_time():
    """Never the bundle, never the base tokens, never a style pack: each brings a `.card`, an
    `.alert` and a `dark` variant under names this project already owns, and a base layer
    that restyles every border and corner on every page. A component's structural file is
    imported the day the component is adopted, and `basecoat.css` paints it.
    """
    imports = basecoat_imports()
    assert imports, "app.css imports nothing from basecoat-css"
    for path in imports:
        assert path.startswith("basecoat-css/components/") and path.endswith(".css"), (
            f"{path}: import a component's structural file, not the bundle, the base or a pack"
        )
    assert '@import "./basecoat.css";' in SOURCE.read_text("utf-8"), "the style pack is not loaded"

    pinned = json.loads((ROOT / "package.json").read_text("utf-8"))["devDependencies"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned["basecoat-css"]), (
        "basecoat-css decides how every button looks; pin it exactly, as the icons are"
    )


def test_no_class_is_defined_on_both_sides_of_the_import():
    """The collision the issue named up front: Basecoat defines `btn` and `card`, and so did
    this project. Importing both without a decision produces whichever the cascade happens to
    pick. So the decision is a rule: `app.css` never defines a class or a utility that an
    imported Basecoat file defines, and `basecoat.css` -- the style pack -- paints only
    classes that an imported Basecoat file defines. Import `card.css` while `.card` is still
    this project's own, and this fails before any page does.
    """
    if not BASECOAT.is_dir():
        pytest.skip("basecoat-css is not installed here; run `npm ci` to check this")

    theirs: set[str] = set()
    for path in basecoat_imports():
        file = BASECOAT / "dist" / path.removeprefix("basecoat-css/")
        assert file.is_file(), f"{path} is not in the installed package"
        theirs |= classes_defined_in(file.read_text("utf-8"))
    assert "btn" in theirs, "the parser no longer sees Basecoat's button; check it"

    ours = classes_defined_in(SOURCE.read_text("utf-8"))
    shared = sorted(ours & theirs)
    assert not shared, f"defined by app.css and by an imported Basecoat file: {shared}"

    painted = classes_defined_in(STYLE_PACK.read_text("utf-8"))
    invented = sorted(painted - theirs)
    assert not invented, f"basecoat.css paints classes Basecoat does not define: {invented}"


def test_the_style_pack_reaches_the_page():
    """Basecoat's structure and Postulo's paint, both in the compiled file, on one selector."""
    compiled = COMPILED.read_text(encoding="utf-8")
    assert ".btn {" in compiled
    assert '.btn[data-variant="outline"]' in compiled
    assert '.btn[data-size="icon-xs"]' in compiled
    assert "--color-primary: var(--color-brand-600)" in compiled
    # The form family (#290): structure from field.css, paint from the pack, one selector.
    assert ".field {" in compiled and ".field > label" in compiled
    assert '.field[data-orientation="horizontal"]' in compiled
    assert ".input-group {" in compiled


def test_the_parser_knows_a_definition_from_a_use():
    css = """
    /* .not-this */
    @utility tap { @apply .also-not-this inline-flex; }
    .btn { @apply gap-2; }
    .btn[data-variant="ghost"], .card > header { color: red; }
    """
    assert classes_defined_in(css) == {"tap", "btn", "card"}
