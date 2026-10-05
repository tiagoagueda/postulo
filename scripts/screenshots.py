#!/usr/bin/env python
"""Take every picture the documentation shows, from a throwaway demo instance (#353).

    uv run python scripts/screenshots.py                 # retake them all
    uv run python scripts/screenshots.py --check         # say which are stale, change nothing
    uv run python scripts/screenshots.py --list          # names and the Markdown to paste
    uv run python scripts/screenshots.py --only board cv-plain
    uv run python scripts/screenshots.py --language fr   # the same pictures, in French

What it does, in order:

1. Makes a database in a temporary directory and fills it with `manage.py seed_demo
   --seed 2026`: the same fictional person, companies and dates every time. Nothing of the
   machine's own instance is read; its `.env` is ignored and every `POSTULO_*` variable
   in the environment is dropped, so no real address or instance name can get into a picture.
2. Freezes the clock at `FROZEN`, on the server (`django.utils.timezone.now`) and in the
   browser, so "3 days ago" and today's cell on the calendar are the same on every run.
3. Starts the application on a free local port, in this process, and draws every page in the
   suite's own DejaVu Sans (`tests/e2e/fonts`), as the browser suite does, so a picture does
   not depend on which fonts the machine has.
4. Visits each page in `SHOTS` with Chromium, and writes the picture to a stable file name:
   the wiki's `images/` (found beside this repository as `postulo.wiki`, or `--wiki`) and
   `assets/screenshots/` for the README. A picture is rewritten only when it differs
   visibly, so a second run on an unchanged tree changes nothing.
5. Renders a CV and a letter in each theme that sets them: the print document the PDF
   renderer is given, drawn at A4 width.

`--check` retakes into memory and compares: a picture is *stale* when more than 0.01% of its
pixels (about a hundred, at desktop size) differ from the one on disk, *missing* when there is
none. It exits 1 if any is, which is what the release handbook asks for. The tolerance is for
the sub-pixel anti-aliasing noise between two runs, a pixel or two, and not for a changed page.
A picture is rewritten under the same rule, so a second run on an unchanged tree writes nothing.

Needs the browser: `uv run playwright install chromium`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
FONTS = REPO / "tests" / "e2e" / "fonts"
README_IMAGES = REPO / "assets" / "screenshots"
DEFAULT_WIKI = REPO.parent / "postulo.wiki"

#: Today, as every picture sees it. A Monday, so the week and the month views are full.
FROZEN = dt.datetime(2026, 10, 5, 9, 30, tzinfo=dt.UTC)
TIME_ZONE = "Europe/Paris"

EMAIL = "alex.morgan@example.org"
PASSWORD = "correct-horse-battery-staple"  # noqa: S105 - a throwaway database's

#: One width for every desktop picture, so a page's Markdown never needs a size.
DESKTOP = (1280, 800)
PHONE = (390, 844)
#: A4 at 96 dpi, which is the width a print document is laid out at.
A4 = (794, 1123)

#: A picture is stale when more than this share of its pixels differ.
TOLERANCE = 0.0001


@dataclass(frozen=True)
class Shot:
    """One picture: where it is taken, where it goes; the alt is for a reader who cannot see it."""

    name: str
    path: Callable[[Demo], str]
    alt: str
    #: "wiki", "readme" or "both".
    to: str = "wiki"
    size: tuple[int, int] = DESKTOP
    theme: str = "light"
    #: A CSS selector to wait for, where the page draws something after it loads.
    wait_for: str = ""
    #: The whole page rather than the first screen, for a page whose point is its length.
    full_page: bool = False


@dataclass
class Demo:
    """What the seeded account holds, looked up once for the pages that need a row."""

    interviewing: int = 0
    ended: int = 0
    company: int = 0
    capture: int = 0
    cv: int = 0
    letter: int = 0
    posting: int = 0
    paths: dict[str, str] = field(default_factory=dict)


# Every page a picture is taken of. The name is the file name, so it is permanent: renaming one
# changes the Markdown that shows it. Written for the reader of the page the picture is on.
SHOTS: tuple[Shot, ...] = (
    Shot(
        "dashboard",
        lambda d: "/",
        "The dashboard: what is due today, the applications still alive and the latest "
        "events across the whole search.",
        to="both",
    ),
    Shot(
        "listings",
        lambda d: "/listings/",
        "The listings table: postings noticed but not yet applied to, each with its "
        "company, where it was found and its state.",
    ),
    Shot(
        "listing",
        lambda d: f"/jobs/postings/{d.posting}/",
        "One listing: its description, the company, where it was found and the button that "
        "turns it into an application.",
    ),
    Shot(
        "applications",
        lambda d: "/applications/",
        "The applications table, one row per application with its status, priority and "
        "the day it was sent.",
    ),
    Shot(
        "board",
        lambda d: "/applications/?view=board",
        "The board: one column per status from Draft to Offer, each application a card "
        "that can be dragged to the next column.",
        to="both",
    ),
    Shot(
        "application",
        lambda d: f"/applications/{d.interviewing}/",
        "One application's page: its status and how to move it along, a form for adding to "
        "its timeline, and the posting's details, documents and interviews beside it.",
        to="both",
    ),
    Shot(
        "application-timeline",
        lambda d: f"/applications/{d.interviewing}/",
        "The whole of one application's page, down to the timeline of everything that "
        "happened, newest first.",
        full_page=True,
    ),
    Shot(
        "application-ended",
        lambda d: f"/applications/{d.ended}/",
        "An application that ended, with the reason and the timeline that led to it.",
        full_page=True,
    ),
    Shot(
        "captures",
        lambda d: "/jobs/captures/",
        "The captures list: postings sent to Postulo from a browser or pasted in, waiting "
        "to be reviewed.",
    ),
    Shot(
        "capture-review",
        lambda d: f"/jobs/captures/{d.capture}/review/",
        "Reviewing a capture: the fields Postulo read from the page, each one editable "
        "before the listing is made.",
    ),
    Shot(
        "calendar",
        lambda d: "/applications/calendar/",
        "The calendar for the month: interviews and reminders on the days they fall.",
        to="both",
    ),
    Shot(
        "agenda",
        lambda d: "/applications/calendar/?view=agenda",
        "The agenda: the same interviews and reminders as a list, in date order.",
    ),
    Shot(
        "report",
        lambda d: "/applications/report/",
        "A report on a period: how many applications were sent, how regularly, and what "
        "came back, with a button to file it as a PDF.",
    ),
    Shot(
        "dashboard-arrange",
        lambda d: "/?arrange=1",
        "The dashboard in arrange mode, where each widget, the Insights ones among them, "
        "is switched on, moved or put away.",
    ),
    Shot(
        "companies",
        lambda d: "/jobs/companies/",
        "The companies table: every employer in the search, with how many applications each has.",
    ),
    Shot(
        "company",
        lambda d: f"/jobs/companies/{d.company}/",
        "One company's page: its details, the people there and every application made to it.",
    ),
    Shot(
        "cvs",
        lambda d: "/documents/cvs/",
        "The list of CVs, each with its theme, its language and when it was last changed.",
    ),
    Shot(
        "cv",
        lambda d: f"/documents/cvs/{d.cv}/",
        "A CV's page: the entries it is made of on one side and a live preview of the "
        "page as it will print on the other.",
        to="both",
    ),
    Shot(
        "letters",
        lambda d: "/documents/letters/",
        "The list of cover letters, each with the application it was written for.",
    ),
    Shot(
        "sent",
        lambda d: "/documents/sent/",
        "Documents sent: the exact version of each CV and letter that went with each application.",
    ),
    Shot(
        "career",
        lambda d: "/career/",
        "The career record: experience, education, skills and the rest, kept once and used "
        "by every CV.",
    ),
    Shot(
        "settings",
        lambda d: "/settings/",
        "Settings: the page that leads to appearance, accessibility, language, account and "
        "connections.",
    ),
    Shot(
        "settings-appearance",
        lambda d: "/settings/appearance/",
        "Appearance settings in the dark theme, where the theme, the density and the "
        "accent are chosen.",
        theme="dark",
    ),
    Shot(
        "settings-accessibility",
        lambda d: "/settings/accessibility/",
        "Accessibility settings: motion, text size and the other ways the interface can "
        "be made easier to read.",
    ),
    Shot(
        "settings-connections",
        lambda d: "/settings/connections/",
        "Connections: where the plugins that act for you find their services, empty until "
        "one is added.",
    ),
    Shot(
        "server-overview",
        lambda d: "/server/overview/",
        "Server settings, Overview: the version, the health of the instance and what is "
        "waiting to be done.",
    ),
    Shot(
        "server-people",
        lambda d: "/server/people/",
        "Server settings, People: everyone with an account on this instance.",
    ),
    Shot(
        "server-design",
        lambda d: "/server/design/",
        "The design gallery: every component of the interface on one page.",
    ),
    Shot(
        "dashboard-phone",
        lambda d: "/",
        "The dashboard on a phone: one column, with the menu folded into a button.",
        size=PHONE,
    ),
    Shot(
        "board-phone",
        lambda d: "/applications/?view=board",
        "The board on a phone: the columns sit side by side and scroll sideways, with the "
        "main menu along the bottom of the screen.",
        size=PHONE,
    ),
)


def renderings(kinds: list[tuple[str, str]]) -> list[Shot]:
    """A CV and a letter in each theme that sets them, as the print document draws them."""
    made: list[Shot] = []
    for theme, label in kinds:
        made.append(
            Shot(
                f"cv-{theme}",
                lambda d: f"/documents/cvs/{d.cv}/preview/",
                f"The demo CV set in the {label} theme, as the first page of its PDF.",
                size=A4,
            )
        )
        made.append(
            Shot(
                f"letter-{theme}",
                lambda d: f"/documents/letters/{d.letter}/preview/",
                f"A cover letter set in the {label} theme, as the first page of its PDF.",
                size=A4,
            )
        )
    return made


# ----------------------------------------------------------------------------- environment


def isolate_environment(workdir: Path) -> None:
    """Point Django at a database and files about to be thrown away, and at nothing else."""
    for name in [n for n in os.environ if n.startswith("POSTULO_")]:
        del os.environ[name]
    os.environ.update(
        {
            "DJANGO_SETTINGS_MODULE": "postulo.config.settings.dev",
            "DJANGO_ALLOW_ASYNC_UNSAFE": "true",
            "POSTULO_SECRET_KEY": "screenshots-not-a-secret",
            "POSTULO_DEBUG": "true",
            "POSTULO_DATABASE_URL": f"sqlite:///{(workdir / 'demo.sqlite3').as_posix()}",
            "POSTULO_MEDIA_ROOT": str(workdir / "media"),
            "POSTULO_PLUGINS_DIR": str(workdir / "plugins"),
            "POSTULO_TIME_ZONE": TIME_ZONE,
            "POSTULO_UPDATE_CHECK": "false",
            "POSTULO_BACKGROUND_WORK": "false",
            "POSTULO_LOG_DIR": "",
        }
    )
    # A developer's `.env` belongs to their own instance; nothing of it may reach a picture.
    import environ

    environ.Env.read_env = classmethod(lambda cls, *args, **kwargs: None)


def freeze_the_clock() -> None:
    """Make "now" the same on every run, wherever Django asks for it."""
    from django.apps import apps
    from django.utils import timezone

    original = timezone.now

    def frozen() -> dt.datetime:
        return FROZEN

    timezone.now = frozen
    # `default=timezone.now` bound the function when the model was defined, so patching the
    # module is not enough for those columns.
    for model in apps.get_models():
        for model_field in model._meta.get_fields():
            if getattr(model_field, "default", None) is original:
                model_field.default = frozen


def mask_the_machine() -> None:
    """Server settings, Overview, describes the machine it runs on; say what a container would.

    The page reads the database file, the media and backup directories, the platform, the
    interpreter, the PDF renderer and the fonts from this machine, which is exactly what a
    picture in the documentation must not carry (and what differs from one run to the next:
    the directory is a new temporary one each time).
    """
    from postulo.core import server_views

    original = server_views.OverviewView.get_context_data

    def masked(self, **kwargs):
        context = original(self, **kwargs)
        context.update(
            {
                "database_name": "/data/postulo.sqlite3",
                "media_root": PurePosixPath("/data/media"),
                "backup_dir": PurePosixPath("/data/backups"),
                "platform": "Linux-6.1-x86_64-with-glibc2.36",
                "executable": "/usr/local/bin/python",
                "pdf_backend": "weasyprint",
                "font_scripts": [("Cyrillic", True), ("Greek", True), ("Latin", True)],
                "python_version": "3.14.0",
            }
        )
        return context

    server_views.OverviewView.get_context_data = masked


def seed(language: str) -> Demo:
    from django.contrib.auth import get_user_model
    from django.core.management import call_command

    from postulo.applications.models import Application, Status
    from postulo.documents.models import CV, CoverLetter
    from postulo.jobs.models import Capture, CaptureStatus, Company, JobPosting

    call_command("migrate", verbosity=0)
    call_command("seed_demo", EMAIL, seed=2026, password=PASSWORD, no_pdf=True, verbosity=0)
    user = get_user_model().objects.get(email=EMAIL)
    user.is_staff = user.is_superuser = True
    user.save()

    demo = Demo()
    mine = Application.objects.for_user(user)
    demo.interviewing = (
        mine.filter(status=Status.INTERVIEWING).order_by("pk").values_list("pk", flat=True).first()
        or mine.order_by("pk").values_list("pk", flat=True).first()
    )
    demo.ended = (
        mine.filter(status=Status.REJECTED).order_by("pk").values_list("pk", flat=True).first()
        or demo.interviewing
    )
    demo.company = (
        Company.objects.for_user(user).order_by("pk").values_list("pk", flat=True).first()
    )
    demo.capture = (
        Capture.objects.for_user(user)
        .filter(status=CaptureStatus.PENDING)
        .order_by("pk")
        .values_list("pk", flat=True)
        .first()
        or Capture.objects.for_user(user).order_by("pk").values_list("pk", flat=True).first()
    )
    demo.cv = CV.objects.for_user(user).order_by("pk").values_list("pk", flat=True).first()
    demo.letter = (
        CoverLetter.objects.for_user(user).order_by("pk").values_list("pk", flat=True).first()
    )
    demo.posting = (
        JobPosting.objects.for_user(user).order_by("pk").values_list("pk", flat=True).first()
    )
    return demo


def theme_the_documents(theme: str) -> None:
    from postulo.documents.models import CV, CoverLetter

    CV.objects.update(theme=theme)
    CoverLetter.objects.update(theme=theme)


def serve_in_the_suites_font() -> None:
    """Answer the stylesheet with the DejaVu rules appended, as `tests/e2e/conftest.py` does."""
    from django.conf import settings
    from django.contrib.staticfiles import handlers
    from django.http import HttpResponse

    faces = (
        ("Postulo Sans", "normal", "DejaVuSans.ttf"),
        ("Postulo Sans", "bold", "DejaVuSans-Bold.ttf"),
        ("Postulo Mono", "normal", "DejaVuSansMono.ttf"),
        ("Postulo Mono", "bold", "DejaVuSansMono-Bold.ttf"),
    )
    prefix = "screenshot-fonts/"
    files = {name: (FONTS / name).read_bytes() for _family, _weight, name in faces}
    rules = "".join(
        f'\n@font-face {{ font-family: "{family}"; font-weight: {weight}; '
        f'src: url("{settings.STATIC_URL}{prefix}{name}") format("truetype"); }}'
        for family, weight, name in faces
    )
    rules += '\n*, ::before, ::after { font-family: "Postulo Sans" !important; }'
    rules += '\ncode, code *, kbd, samp, pre, pre * { font-family: "Postulo Mono" !important; }\n'
    served = handlers.serve

    def in_the_font(request, path, **kwargs):
        asked = path.replace("\\", "/")
        if asked.startswith(prefix) and asked[len(prefix) :] in files:
            return HttpResponse(files[asked[len(prefix) :]], content_type="font/ttf")
        response = served(request, path, **kwargs)
        if response.status_code != 200 or "/css/app" not in request.path:
            return response
        body = b"".join(response.streaming_content) if response.streaming else response.content
        response.close()
        return HttpResponse(body + rules.encode(), content_type=response["Content-Type"])

    handlers.serve = in_the_font


class Server:
    """The application, answering on a free local port from a thread of this process."""

    def __init__(self) -> None:
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.servers.basehttp import (
            ThreadedWSGIServer,
            WSGIRequestHandler,
        )
        from django.core.wsgi import get_wsgi_application

        class Quiet(WSGIRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

        self.httpd = ThreadedWSGIServer(("127.0.0.1", 0), Quiet)
        self.httpd.daemon_threads = True
        self.httpd.set_app(StaticFilesHandler(get_wsgi_application()))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------------------------- taking


def take(shots: list[Shot], demo: Demo, language: str, server: Server) -> dict[str, bytes]:
    """Visit every page in `shots` and return its PNG, by name."""
    from playwright.sync_api import sync_playwright

    pictures: dict[str, bytes] = {}
    with sync_playwright() as playwright:
        # The browser's own controls (a date field's month) speak its UI language, not the page's.
        browser = playwright.chromium.launch(args=[f"--lang={language}"])
        contexts: dict[tuple, object] = {}

        def context_for(size: tuple[int, int], theme: str):
            key = (size, theme)
            if key not in contexts:
                context = browser.new_context(
                    viewport={"width": size[0], "height": size[1]},
                    color_scheme=theme,
                    locale=language,
                    timezone_id=TIME_ZONE,
                    reduced_motion="reduce",
                    device_scale_factor=1,
                )
                context.clock.set_fixed_time(FROZEN)
                page = context.new_page()
                page.goto(f"{server.url}/accounts/login/")
                page.locator("input[name=login]").fill(EMAIL)
                page.locator("input[name=password]").fill(PASSWORD)
                page.locator("form input[name=password]").press("Enter")
                page.wait_for_url(f"{server.url}/")
                page.close()
                contexts[key] = context
            return contexts[key]

        for shot in shots:
            context = context_for(shot.size, shot.theme)
            page = context.new_page()
            response = page.goto(f"{server.url}{shot.path(demo)}")
            if response is None or response.status != 200:
                status = response.status if response else "no response"
                raise SystemExit(f"{shot.name}: {shot.path(demo)} answered {status}")
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            if shot.wait_for:
                page.wait_for_selector(shot.wait_for)
            pictures[shot.name] = page.screenshot(
                full_page=shot.full_page, animations="disabled", caret="hide"
            )
            page.close()
        browser.close()
    return pictures


def optimised(png: bytes) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def differing(a: bytes, b: bytes) -> float:
    """The share of pixels that differ visibly between two PNGs; 1.0 if their sizes differ."""
    from PIL import Image, ImageChops

    first = Image.open(io.BytesIO(a)).convert("RGB")
    second = Image.open(io.BytesIO(b)).convert("RGB")
    if first.size != second.size:
        return 1.0
    grey = ImageChops.difference(first, second).convert("L")
    changed = sum(grey.histogram()[17:])
    return changed / (first.width * first.height)


def destinations(shot: Shot, language: str, wiki: Path) -> list[Path]:
    folder = "" if language == "en" else f"{language}/"
    found: list[Path] = []
    if shot.to in ("wiki", "both"):
        found.append(wiki / "images" / folder / f"{shot.name}.png")
    if shot.to in ("readme", "both"):
        found.append(README_IMAGES / folder / f"{shot.name}.png")
    return found


def markdown(shot: Shot, language: str) -> str:
    folder = "" if language == "en" else f"{language}/"
    return f"![{shot.alt}](images/{folder}{shot.name}.png)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--wiki", type=Path, default=Path(os.environ.get("POSTULO_WIKI", DEFAULT_WIKI))
    )
    parser.add_argument("--language", default="en", help="interface language code (default en)")
    parser.add_argument("--only", nargs="*", default=[], metavar="NAME", help="take only these")
    parser.add_argument("--check", action="store_true", help="report stale pictures, write nothing")
    parser.add_argument(
        "--list", action="store_true", help="print every picture's name and alt text"
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO / "src"))
    workdir = Path(tempfile.mkdtemp(prefix="postulo-screenshots-"))
    isolate_environment(workdir)
    import django

    django.setup()
    from postulo.documents import themes

    shots = list(SHOTS)
    shots += renderings(
        [(t.name, str(t.label)) for t in themes.all_themes() if t.sets(themes.Kind.CV)]
    )
    if args.list:
        for shot in shots:
            print(f"{shot.name} ({shot.to})\n  {markdown(shot, 'en')}")
        shutil.rmtree(workdir, ignore_errors=True)
        return 0
    unknown = set(args.only) - {s.name for s in shots}
    if unknown:
        raise SystemExit(f"no such picture: {', '.join(sorted(unknown))}")
    if args.only:
        shots = [s for s in shots if s.name in args.only]
    if not args.wiki.is_dir() and any(s.to != "readme" for s in shots):
        raise SystemExit(f"the wiki is not at {args.wiki}; clone it there or pass --wiki")

    try:
        freeze_the_clock()
        mask_the_machine()
        demo = seed(args.language)
        serve_in_the_suites_font()
        server = Server()
        try:
            pictures: dict[str, bytes] = {}
            # The documents are drawn once per theme, so the renderings are taken a theme at
            # a time and the pages that are not renderings in the first pass.
            plain = [s for s in shots if not s.name.startswith(("cv-", "letter-"))]
            pictures.update(take(plain, demo, args.language, server))
            for theme in {
                s.name.split("-", 1)[1] for s in shots if s.name.startswith(("cv-", "letter-"))
            }:
                theme_the_documents(theme)
                of_this_theme = [
                    s for s in shots if s.name.endswith(f"-{theme}") and s not in plain
                ]
                pictures.update(take(of_this_theme, demo, args.language, server))
        finally:
            server.stop()
    finally:
        from django.db import connections

        connections.close_all()
        shutil.rmtree(workdir, ignore_errors=True)

    stale: list[str] = []
    for shot in shots:
        png = optimised(pictures[shot.name])
        for target in destinations(shot, args.language, args.wiki):
            label = target.as_posix()
            if not target.exists():
                stale.append(f"missing  {label}")
                changed = True
            else:
                share = differing(png, target.read_bytes())
                changed = share > TOLERANCE
                if changed:
                    stale.append(f"stale    {label}  ({share:.3%} of its pixels differ)")
            if changed and not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(png)
                print(f"wrote    {label}")
    if args.check:
        print("\n".join(stale) if stale else "every picture is current")
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
