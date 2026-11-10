"""The script the browser is served is the commented source, built, and never a stale one (#1149).

`app.js` is documented where it is written: more than a third of the file was comments, and
every browser downloaded them, 101 kB compressed where the code alone is under 45. So the
source lives in `assets/js/app.js`, as the stylesheet's does in `assets/css/`, and the page
loads `static/js/app.min.js`, which `npm run build:js` writes from it with esbuild: comments
and indentation gone, every name left as it was written.

The built copy is committed, so that the application runs without Node, and a committed copy
can be forgotten. A change to the source that is not rebuilt ships a page that still runs the
old script, silently, which is the stylesheet's failure of #164 over again. So the comparison
CI's Checks job makes runs here as well, on the machine where the source changed. It needs
the esbuild that `npm ci` installs and skips where that is missing; the rest of this file
needs nothing but the repository.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "js" / "app.js"
STATIC = ROOT / "src" / "postulo" / "static"
BUILT = STATIC / "js" / "app.min.js"
TEMPLATES = ROOT / "src" / "postulo" / "templates"
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
COMMAND = PACKAGE["scripts"]["build:js"]


def esbuild() -> str | None:
    """The CLI `npm ci` installs, and never one fetched from the network to run a test."""
    return shutil.which("esbuild", path=str(ROOT / "node_modules" / ".bin"))


def test_the_committed_script_is_what_the_source_builds_to(tmp_path):
    """The command is read from `package.json`, so what is compared is what `npm run build:js`
    writes and not this test's idea of it."""
    cli = esbuild()
    if cli is None:
        pytest.skip("esbuild is not installed here; run `npm ci` to check this")

    name, *arguments = shlex.split(COMMAND)
    assert name == "esbuild"
    fresh = tmp_path / "app.min.js"
    written_to = [argument for argument in arguments if argument.startswith("--outfile=")]
    assert written_to == ["--outfile=src/postulo/static/js/app.min.js"]
    arguments[arguments.index(written_to[0])] = f"--outfile={fresh}"

    subprocess.run(  # noqa: S603 - the project's own pinned binary and its own arguments
        [cli, *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=120,
    )

    # Bytes, not text: nothing between the build and the browser may touch a line ending.
    assert fresh.read_bytes() == BUILT.read_bytes(), (
        "src/postulo/static/js/app.min.js is stale: assets/js/app.js changed and the copy the "
        "browser is served was not rebuilt. Run `npm run build:js` and commit the result."
    )


def test_the_build_drops_comments_and_indentation_and_renames_nothing():
    """Whitespace and syntax, and no renaming: an error a browser reports from the built copy
    still names the function it happened in."""
    arguments = shlex.split(COMMAND)

    assert arguments[:2] == ["esbuild", "assets/js/app.js"]
    assert "--minify-whitespace" in arguments and "--minify-syntax" in arguments
    assert "--minify" not in arguments and "--minify-identifiers" not in arguments
    assert "--bundle" not in arguments, "one classic script in, the same script out"
    assert not any(a.startswith("--sourcemap") for a in arguments), "nothing with a path in it"


def test_the_built_script_is_the_same_bytes_on_every_machine():
    built = BUILT.read_bytes()

    assert b"\r" not in built, "written with LF, whatever the machine that built it"
    first, _, rest = built.partition(b"\n")
    assert first.startswith(b"/* Postulo, AGPL-3.0-or-later. Built from assets/js/app.js")
    assert rest.startswith(b'(function(){"use strict";'), "still one function, still strict"
    assert built.endswith(b"})();\n")


def test_the_built_script_is_the_code_without_the_commentary():
    source = SOURCE.read_text(encoding="utf-8")
    built = BUILT.read_text(encoding="utf-8")

    assert "Postulo's client-side behaviour, such as it is." in source
    assert "such as it is" not in built
    assert built.count("/*") == 1, "the one comment left is the line saying where it came from"
    assert len(built) < len(source) / 2
    # Names are what the source wrote, so a stack trace reads.
    for name in ("function onContentReady(ready)", "onContentReady(readyPickers)"):
        assert name in built, name


def test_every_page_loads_the_built_script_and_no_template_names_the_source():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "<script src=\"{% static 'js/app.min.js' %}\" defer></script>" in base

    named = re.compile(r"""js/app\.js['"]""")
    for template in TEMPLATES.rglob("*.html"):
        assert not named.search(template.read_text(encoding="utf-8")), (
            f"{template.name} loads the commented source, which is not served"
        )


@pytest.mark.django_db
def test_a_page_asks_for_the_built_script(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert re.search(r'<script src="[^"]*js/app\.min\.js" defer></script>', html)
    assert "js/app.js" not in html


def test_the_commented_source_is_not_among_the_served_files():
    """One copy is published. `collectstatic` takes everything under `static/`, so the source
    left there would be hashed, compressed three ways and offered beside the copy pages use."""
    assert SOURCE.is_file()
    assert not (STATIC / "js" / "app.js").exists()
    assert finders.find("js/app.min.js")
    assert finders.find("js/app.js") is None


def test_the_tool_is_pinned_to_one_version():
    """Another esbuild may write the same script differently, and then the committed copy is
    stale on one machine and current on the next."""
    wanted = PACKAGE["devDependencies"]["esbuild"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", wanted), "an exact version, not a range"

    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/esbuild"]["version"] == wanted
    # The binary is a package per platform; the lock has to hold the ones CI and the image use.
    for platform in ("linux-x64", "linux-arm64", "win32-x64", "darwin-arm64"):
        assert lock["packages"][f"node_modules/@esbuild/{platform}"]["version"] == wanted


def test_ci_rebuilds_it_and_the_image_builds_it():
    """A stale copy cannot reach `main`, and the image is right even if one did."""
    checks = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: npm run build:js" in checks
    assert "git diff --exit-code -- src/postulo/static/js/app.min.js" in checks
    assert "::error::app.min.js is stale. Run 'npm run build:js' and commit the result." in checks

    image = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    styles, _, runtime = image.partition("AS build\n")
    assert "RUN npm run build:js" in styles
    assert styles.index("COPY assets ./assets") < styles.index("RUN npm run build:js")
    assert (
        "COPY --from=styles /build/src/postulo/static/js/app.min.js "
        "./src/postulo/static/js/app.min.js"
    ) in runtime
    assert runtime.index("COPY --from=styles /build/src/postulo/static/js/app.min.js") < (
        runtime.index("python manage.py collectstatic")
    ), "collected after the built copy is in place"


def test_the_stylesheet_reads_the_source_and_not_the_built_copy():
    """The script names classes, so Tailwind reads it. It reads the source: were it the built
    copy, the stylesheet would depend on which of the two builds ran first."""
    stylesheet = (ROOT / "assets" / "css" / "app.css").read_text(encoding="utf-8")

    assert '@source "../js";' in stylesheet
    assert '@source not "../../src/postulo/static/js/app.min.js";' in stylesheet
