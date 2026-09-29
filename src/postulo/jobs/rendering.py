"""Drawing a kept source as a PDF, by a renderer that can run none of it (#256).

A rendering of a captured page comes from one of two places. The browser that was looking
at the page may send one, and that is the faithful one: the advert as it looked. Or this
instance draws one from the kept source -- with scripts, the network and every outside
resource switched off, because the source is a stranger's markup and drawing it is the one
moment it is handed to something that interprets it.

So what is drawn here is not the page as it looked. It is the words of the page, laid out
by their own markup, with none of its stylesheets, images or scripts: readable, printable,
searchable, and the same whenever it is drawn, since nothing outside the source goes into
it. That last part is why it is drawn when somebody asks rather than at every capture.

**Which renderer, and what happens where there is none.** `rendering_child` says why it
is WeasyPrint and nothing else. Where WeasyPrint cannot run -- a machine without Pango,
which is most Windows machines -- there is no safe renderer, :func:`renderer` answers
``None``, and the instance keeps the source only. :func:`why_not` is the sentence the
pages show, because a button that is missing with no explanation looks like a fault.

**In a process of its own, against the clock.** The parent hands the source over on
standard input, waits :data:`DRAW_SECONDS`, and takes a PDF back or nothing. A document
that never finishes is killed; one that exhausts its memory dies alone.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from postulo.core import site

from . import rendering_child

logger = logging.getLogger(__name__)

#: How long one document may take, on the clock on the wall. A long advert is drawn in a
#: few seconds; on an instance with no worker somebody is waiting for this, and the web
#: server's own patience is two minutes.
DRAW_SECONDS = 45

#: What is run to draw. A list so that a test can stand something else in its place.
COMMAND: tuple[str, ...] = (sys.executable, "-B", "-m", "postulo.jobs.rendering_child")


class CannotDraw(Exception):
    """The source could not be drawn. The message is a sentence for the person."""


@dataclass(frozen=True)
class Renderer:
    """A renderer that is safe to hand a stranger's page to."""

    name: str
    command: tuple[str, ...]


def renderer() -> Renderer | None:
    """The safe renderer this instance has, or ``None`` where it has none.

    Asked of the same check the PDF backends use, so *can WeasyPrint run here* has one
    answer. Whatever `POSTULO_PDF_BACKEND` says is not consulted: that setting chooses who
    draws a CV, which is a question about fidelity, and this is a question about what may
    be handed a stranger's markup.
    """
    from postulo.documents.pdf import WeasyPrintBackend

    if not WeasyPrintBackend().is_available():
        return None
    return Renderer(name="weasyprint", command=COMMAND)


#: Said wherever a rendering would have been offered and is not.
NO_RENDERER = gettext_lazy(
    "This instance cannot draw a page itself: the renderer that does it safely, with "
    "scripts and the network off, is not installed here. The source is kept, and a "
    "rendering sent by the browser extension is kept too."
)


def why_not() -> str:
    """Why nothing is drawn here, as a sentence; empty where something is."""
    return "" if renderer() is not None else str(NO_RENDERER)


def draw(html: str, *, using: Renderer | None = None) -> bytes:
    """The source as a PDF, or :class:`CannotDraw` saying why not."""
    chosen = using or renderer()
    if chosen is None:
        raise CannotDraw(str(NO_RENDERER))

    most = site.capture_rendering_max_bytes()
    try:
        finished = subprocess.run(  # noqa: S603 - the argument list is built here
            [*chosen.command, str(most)],
            input=html.encode("utf-8", errors="replace"),
            capture_output=True,
            timeout=DRAW_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CannotDraw(
            str(
                _("The page was not drawn within %(seconds)s seconds, so it was left.")
                % {"seconds": DRAW_SECONDS}
            )
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("Could not start the page renderer: %s", error)
        raise CannotDraw(str(_("The renderer could not be started."))) from error

    if finished.returncode == rendering_child.TOO_LARGE:
        raise CannotDraw(str(_("The rendering came out larger than this instance keeps.")))
    if finished.returncode == rendering_child.NO_RENDERER:
        raise CannotDraw(str(NO_RENDERER))
    if finished.returncode != rendering_child.DREW:
        # What the renderer said goes to the log, where an operator can read it. The page
        # gets a sentence: it is about a stranger's markup, and nothing the person can fix.
        logger.warning(
            "The page renderer ended with %s: %s",
            finished.returncode,
            (finished.stderr or b"").decode("utf-8", errors="replace")[-500:],
        )
        raise CannotDraw(str(_("The page could not be drawn.")))
    if not finished.stdout.startswith(b"%PDF-"):
        raise CannotDraw(str(_("The page could not be drawn.")))
    return finished.stdout
