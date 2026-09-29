"""Draw one stranger's page as a PDF, in a process of its own, and run none of it (#256).

    python -m postulo.jobs.rendering_child <most bytes to answer with>

The page arrives on standard input as UTF-8 and the PDF leaves on standard output. Nothing
else goes in or comes out, and this module imports nothing of Postulo's: it is started for
one document and ends with it.

**Why WeasyPrint, and only WeasyPrint.** What is being drawn was written by a stranger, so
the renderer has to be one that *cannot* run it rather than one asked not to. WeasyPrint
has no script engine at all, and every address a document names reaches it through one
fetcher, which here refuses all of them. A browser has both an engine and a network stack
and can only be configured to stand down, which is a promise about a configuration rather
than about the program -- so where WeasyPrint will not run, no rendering is drawn, and the
page that would have offered one says so.

**Nothing is fetched, not even what the page carries inside itself.** A ``data:`` address
is not a request, and it is refused all the same: an embedded image is a stranger's bytes
for an image decoder and an embedded font is a stranger's bytes for a font library, and
both of those are written in C. What is drawn is the words of the page in the fonts this
machine has, which is what a rendering made with everything outside switched off is.

**A process of its own**, for the reason `plugins.installing` gives for importing a
plugin in one: what is handed over is somebody else's, and a document that exhausts memory
or never finishes should take this process with it and nothing else. The ceilings below
are this process's own, set before the document is read; the time limit is the parent's.
"""

from __future__ import annotations

import sys

#: What this process may allocate, and how long it may compute. Generous beside what an
#: advert needs -- a long one is drawn in a few seconds and a few hundred megabytes -- and
#: small beside what a machine has. The parent stops the clock on the wall a little before
#: the second of these would.
MEMORY_BYTES = 1024 * 1024 * 1024
CPU_SECONDS = 60

#: How the process ends, for the parent to read. Anything else is a failure it did not
#: expect, and is treated as one.
DREW = 0
FAILED = 1
NO_RENDERER = 2
TOO_LARGE = 3


def keep_within_bounds() -> None:
    """Put a ceiling on this process's memory and its computing time, where there is one.

    `resource` exists on the systems Postulo is deployed on and not on Windows, where
    WeasyPrint does not run either. A ceiling that cannot be set is not a reason to refuse
    the work: the parent's time limit still holds.
    """
    try:
        import resource
    except ImportError:
        return
    for name, ceiling in (("RLIMIT_DATA", MEMORY_BYTES), ("RLIMIT_CPU", CPU_SECONDS)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            _soft, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY:
                ceiling = min(ceiling, hard)
            resource.setrlimit(limit, (ceiling, hard))
        except (OSError, ValueError):
            continue


def draw(html: str) -> bytes:
    """The page as a PDF. Imports WeasyPrint here, so its absence is an answer."""
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    def document():
        # No protocol is allowed, so no address is opened: not `http`, not `file`, not
        # `data`. WeasyPrint catches the refusal and draws the document without whatever
        # it named. A fetcher for each attempt: it is an opener, and holds what it opened.
        nothing = URLFetcher(allowed_protocols=set(), allow_redirects=False)
        # `screen`, because what was captured was a page on a screen; `print` is what a
        # site writes for paper and is as likely to hide the advert as to show it.
        return HTML(string=html, url_fetcher=nothing, media_type="screen")

    try:
        # Tagged, as every PDF Postulo writes is (#235): a rendering somebody cannot read
        # with a screen reader is a picture of the text rather than the text.
        return document().write_pdf(pdf_variant="pdf/ua-1")
    except (ImportError, OSError, MemoryError):
        raise
    except Exception as error:
        # Building the tag tree walks the whole document, and this document is whatever a
        # stranger wrote: markup nobody tested a tagger against. A rendering without tags
        # is worth more than none, so it is drawn again plainly rather than given up on.
        sys.stderr.write(f"tagged rendering failed, drawing it plain: {error!r}\n")
        return document().write_pdf()


def main(arguments: list[str]) -> int:
    try:
        most = int(arguments[0])
    except (IndexError, ValueError):
        return FAILED

    keep_within_bounds()
    # Standard output is the PDF and nothing else, so whatever a library prints on the
    # way is sent where the log is. WeasyPrint prints a paragraph there when its
    # libraries are missing, and a paragraph in front of a PDF is not a PDF.
    answer = sys.stdout.buffer
    sys.stdout = sys.stderr
    html = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        drawn = draw(html)
    except (ImportError, OSError) as error:
        # OSError as well as ImportError: WeasyPrint is a Python package that loads Pango
        # through the system linker, so where the libraries are missing it is present,
        # findable and unusable, and says so with an OSError.
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return NO_RENDERER
    except MemoryError:
        sys.stderr.write("MemoryError\n")
        return FAILED
    except Exception as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return FAILED

    if len(drawn) > most:
        return TOO_LARGE
    answer.write(drawn)
    answer.flush()
    return DREW


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
