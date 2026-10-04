"""Drawing a kept source as a PDF, by something that can run none of it (#256).

Two halves. The first is the arrangement around the renderer -- a process of its own, a
clock, a ceiling on what it may answer with -- and is tested with a stand-in, so that it
runs on every machine. The second hands a hostile page to the real thing and listens for
what it does, and needs Pango: it skips where there is none, as
`tests/security/test_pdf_fetching.py` does and for the reason given there.
"""

from __future__ import annotations

import http.server
import sys
import textwrap
import threading
from ctypes.util import find_library

import pytest

from postulo.jobs import rendering, rendering_child

#: The ceiling a rendering is held to is the instance's policy, which is a row.
pytestmark = pytest.mark.django_db

needs_weasyprint = pytest.mark.skipif(
    find_library("pango-1.0") is None,
    reason="Pango is not installed here, and WeasyPrint draws with it",
)
without_weasyprint = pytest.mark.skipif(
    find_library("pango-1.0") is not None,
    reason="WeasyPrint can draw here, so its absence cannot be shown",
)


def stand_in(tmp_path, body: str) -> rendering.Renderer:
    """A renderer made of a few lines of Python, run the way the real one is."""
    script = tmp_path / "stand_in.py"
    script.write_text(
        "import hashlib, sys, time\npage = sys.stdin.buffer.read()\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    return rendering.Renderer("stand-in", (sys.executable, "-B", str(script)))


# ----------------------------------------------------------- the arrangement around it


def test_the_source_goes_in_and_a_pdf_comes_out(tmp_path):
    drawer = stand_in(
        tmp_path,
        """
        sys.stdout.buffer.write(b"%PDF-1.7 " + hashlib.sha256(page).hexdigest().encode())
        """,
    )
    import hashlib

    drawn = rendering.draw("<p>héllo</p>", using=drawer)

    assert drawn.startswith(b"%PDF-1.7 ")
    assert drawn.endswith(hashlib.sha256("<p>héllo</p>".encode()).hexdigest().encode()), (
        "what it was handed is the source, as UTF-8, and nothing else"
    )


def test_it_is_told_the_most_it_may_answer_with(tmp_path, settings):
    settings.POSTULO_CAPTURE_RENDERING_MAX_BYTES = 12_345
    drawer = stand_in(
        tmp_path,
        """
        sys.stdout.buffer.write(b"%PDF-1.7 " + sys.argv[1].encode())
        """,
    )

    assert rendering.draw("<p>x</p>", using=drawer).endswith(b" 12345")


def test_a_page_that_never_finishes_is_left(tmp_path, monkeypatch):
    """A document somebody else wrote does not get to hold a worker for ever."""
    monkeypatch.setattr(rendering, "DRAW_SECONDS", 1)
    drawer = stand_in(tmp_path, "time.sleep(60)\n")

    with pytest.raises(rendering.CannotDraw, match="within 1 second,"):
        rendering.draw("<p>x</p>", using=drawer)


@pytest.mark.parametrize(
    ("ending", "said"),
    [
        (rendering_child.FAILED, "could not be drawn"),
        (rendering_child.TOO_LARGE, "larger than this instance keeps"),
        (rendering_child.NO_RENDERER, "cannot draw a page itself"),
        (70, "could not be drawn"),
    ],
    ids=["failed", "too large", "no renderer", "something unexpected"],
)
def test_each_way_it_can_end_badly_is_a_sentence(tmp_path, ending, said):
    drawer = stand_in(tmp_path, f"sys.stderr.write('Traceback: secret path')\nsys.exit({ending})\n")

    with pytest.raises(rendering.CannotDraw) as refused:
        rendering.draw("<p>x</p>", using=drawer)

    assert said in str(refused.value)
    assert "secret path" not in str(refused.value), "what it said goes to the log"


def test_an_answer_that_is_not_a_pdf_is_not_kept_as_one(tmp_path):
    drawer = stand_in(tmp_path, "sys.stdout.buffer.write(b'<html><script>x</script></html>')\n")

    with pytest.raises(rendering.CannotDraw):
        rendering.draw("<p>x</p>", using=drawer)


def test_a_renderer_that_cannot_be_started_is_a_sentence_too(tmp_path):
    missing = rendering.Renderer("gone", (str(tmp_path / "no-such-program"),))

    with pytest.raises(rendering.CannotDraw, match="could not be started"):
        rendering.draw("<p>x</p>", using=missing)


def test_a_page_that_dies_takes_nothing_with_it(tmp_path):
    """The process it died in was its own."""
    drawer = stand_in(tmp_path, "import os\nos._exit(9)\n")

    with pytest.raises(rendering.CannotDraw):
        rendering.draw("<p>x</p>", using=drawer)

    assert rendering.draw(
        "<p>x</p>", using=stand_in(tmp_path, "sys.stdout.buffer.write(b'%PDF-1.7')\n")
    )


# ------------------------------------------------------------------ which renderer


def test_the_safe_renderer_is_weasyprint_where_it_can_run(monkeypatch):
    from postulo.documents.pdf import WeasyPrintBackend

    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: True)

    chosen = rendering.renderer()

    assert chosen.name == "weasyprint"
    assert chosen.command[0] == sys.executable
    assert chosen.command[-1] == "postulo.jobs.rendering_child"
    assert rendering.why_not() == ""


def test_where_it_cannot_run_there_is_none_and_the_instance_says_why(monkeypatch):
    from postulo.documents.pdf import WeasyPrintBackend

    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: False)

    assert rendering.renderer() is None
    assert "cannot draw a page itself" in rendering.why_not()
    with pytest.raises(rendering.CannotDraw, match="cannot draw a page itself"):
        rendering.draw("<p>x</p>")


def test_a_browser_is_never_the_safe_renderer(monkeypatch, settings):
    """`POSTULO_PDF_BACKEND` chooses who draws a CV. It does not choose what a stranger's
    markup is handed to: a browser has a script engine and a network stack, and can only
    be asked to stand them down."""
    from postulo.documents.pdf import ChromiumBackend, WeasyPrintBackend

    settings.POSTULO_PDF_BACKEND = "chromium"
    monkeypatch.setattr(ChromiumBackend, "is_available", lambda self: True)
    monkeypatch.setattr(WeasyPrintBackend, "is_available", lambda self: False)

    assert rendering.renderer() is None


def test_nothing_in_the_renderer_mentions_a_browser():
    from pathlib import Path

    root = Path(rendering.__file__).parent
    for name in ("rendering.py", "rendering_child.py"):
        code = "\n".join(
            line
            for line in (root / name).read_text(encoding="utf-8").splitlines()
            if "import" in line
        )
        assert "playwright" not in code and "ChromiumBackend" not in code, name


# ----------------------------------------------------------------- the child itself


@pytest.mark.parametrize("arguments", [[], ["a lot"], [""]])
def test_the_child_wants_to_be_told_its_ceiling(arguments):
    assert rendering_child.main(arguments) == rendering_child.FAILED


def test_the_ceilings_are_set_without_complaint_wherever_this_runs():
    """Where there is no such thing as a ceiling -- Windows -- that is not a failure."""
    import subprocess

    finished = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from postulo.jobs import rendering_child as c; c.keep_within_bounds(); print('ok')",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert finished.stdout.strip() == "ok", finished.stderr


def test_the_child_imports_nothing_of_postulos():
    """It is started for one stranger's document. It has no database, no settings and no
    secrets to be talked out of."""
    from pathlib import Path

    source = Path(rendering_child.__file__).read_text(encoding="utf-8")

    assert "django" not in source.lower().replace("postulo is", "")
    assert "from postulo" not in source and "import postulo" not in source


def run_the_child(page: bytes, most: int):
    """The child, started the way `rendering.draw` starts it.

    **Never called in this process.** The first thing it does with a valid argument is
    put a ceiling on its own memory and computing time, and a test that called `main`
    here would put that ceiling on the suite: sixty seconds of processor, for a run that
    has usually spent more than that before it reaches this file.
    """
    import subprocess

    return subprocess.run(  # noqa: S603 - the renderer's own command, and a number
        [*rendering.COMMAND, str(most)],
        input=page,
        capture_output=True,
        timeout=120,
        check=False,
    )


@without_weasyprint
def test_without_weasyprint_the_child_says_there_is_no_renderer():
    finished = run_the_child(b"<p>x</p>", 1_000_000)

    assert finished.returncode == rendering_child.NO_RENDERER
    assert finished.stdout == b""


@without_weasyprint
@pytest.mark.django_db
def test_without_weasyprint_nothing_is_drawn_and_the_sentence_says_why():
    """The whole way through, on a machine that has no safe renderer: this one."""
    from postulo.documents.pdf import WeasyPrintBackend

    assert not WeasyPrintBackend().is_available()
    assert rendering.renderer() is None
    forced = rendering.Renderer("weasyprint", rendering.COMMAND)
    with pytest.raises(rendering.CannotDraw, match="cannot draw a page itself"):
        rendering.draw("<p>x</p>", using=forced)


# --------------------------------------------------------- the real thing, listened to


@pytest.fixture
def listener():
    """A server on this machine that notes every request it is sent."""
    heard: list[str] = []

    class Note(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            heard.append(self.path)
            self.send_response(404)
            self.end_headers()

        do_POST = do_GET
        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Note)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", heard
    finally:
        server.shutdown()
        server.server_close()


#: One transparent pixel, as a page would carry an image inside itself.
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def hostile(address: str, secret: str) -> str:
    """Everything a page can name that a renderer might go and get."""
    return f"""<!doctype html>
<html><head><title>A role</title>
<base href="{address}/base/">
<meta http-equiv="refresh" content="0; url={address}/refresh">
<link rel="stylesheet" href="{address}/sheet.css">
<link rel="stylesheet" href="{secret}">
<link rel="attachment" href="{secret}">
<link rel="preload" href="{address}/preload" as="image">
<style>
  @import url("{address}/import.css");
  @font-face {{ font-family: x; src: url("{address}/font.woff2"); }}
  body {{ background: url("{address}/background.png"); font-family: x; }}
</style>
<script src="{address}/script.js"></script>
<script>new Image().src = "{address}/from-a-script";</script>
</head>
<body onload="new Image().src = '{address}/onload'">
<h1>A role at Aperture Science</h1>
<p>The words of the advert.</p>
<img src="{address}/pixel.png" alt="">
<img src="relative.png" alt="">
<img src="{PIXEL}" alt="">
<iframe src="{address}/frame"></iframe>
<object data="{address}/object"></object>
<embed src="{address}/embed">
<video src="{address}/video" poster="{address}/poster"></video>
<svg xmlns="http://www.w3.org/2000/svg"><image href="{address}/svg.png"/></svg>
<form action="{address}/form" method="post"><input name="q"></form>
</body></html>"""


@needs_weasyprint
def test_a_hostile_page_is_drawn_and_nothing_is_fetched(listener, tmp_path):
    address, heard = listener
    secret = tmp_path / "secret.css"
    secret.write_text("h1::after { content: 'READ-FROM-THE-DISK' }", encoding="utf-8")

    drawn = rendering.draw(hostile(address, secret.as_uri()))

    assert drawn.startswith(b"%PDF-")
    assert heard == [], f"the renderer asked for {heard}"


@needs_weasyprint
def test_what_a_page_carries_inside_itself_is_not_opened_either():
    """An embedded image is a stranger's bytes for an image decoder."""
    drawn = rendering.draw(f'<html><body><p>x</p><img src="{PIXEL}" alt=""></body></html>')

    assert drawn.startswith(b"%PDF-")
    assert b"/Subtype /Image" not in drawn


@needs_weasyprint
def test_the_child_draws_through_its_own_front_door():
    finished = run_the_child(b"<p>hello</p>", 10_000_000)

    assert finished.returncode == rendering_child.DREW, finished.stderr
    assert finished.stdout.startswith(b"%PDF-")


@needs_weasyprint
def test_a_rendering_larger_than_the_ceiling_is_not_answered_with():
    finished = run_the_child(b"<p>hello</p>", 10)

    assert finished.returncode == rendering_child.TOO_LARGE, finished.stderr
    assert finished.stdout == b""


@needs_weasyprint
@pytest.mark.django_db
def test_a_capture_is_drawn_from_its_kept_source_end_to_end(user, settings, tmp_path):
    from postulo.core import site
    from postulo.core.models import SiteSettings
    from postulo.jobs import pages
    from postulo.jobs.models import Capture, RenderedBy, RenderingKind

    settings.MEDIA_ROOT = str(tmp_path / "media")
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"capture_keep_source": True, "capture_keep_rendering": True}
    )
    site.forget_current()
    profile = user.profile
    profile.keep_page_source = profile.keep_page_rendering = True
    profile.save()
    capture = Capture.objects.create(owner=user, url="https://example.org/j/7", data={})
    pages.keep_source(capture, "<html><body><h1>A role</h1><script>x()</script></body></html>")

    page = pages.draw_rendering(capture)

    assert page.rendering_type == RenderingKind.PDF
    assert page.rendered_by == RenderedBy.INSTANCE
    with page.rendering.open("rb") as handle:
        assert handle.read(5) == b"%PDF-"
