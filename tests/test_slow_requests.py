"""The views that do something slow, and what they hold while they do it (#220).

`ATOMIC_REQUESTS` is on, and on SQLite Postulo opens every transaction *immediate*, so a
request takes the database's write lock before the view body runs and keeps it until the
response. For a page that is milliseconds and it is the price of two requests never
colliding (`config/sqlite.py`). For a capture waiting on somebody else's web server, a CV
being drawn by Chromium, or an archive of every file an account owns, it meant one person's
request stopped every other worker, the scheduler and `db_worker` — and anything that waited
past the twenty-second busy timeout failed with *database is locked*.

Those views opt out and wrap their own writes instead. This file is what holds them to it:
which views opt out, that ordinary views still do not, and that each write which used to be
covered by the request's transaction is covered by one of its own.
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import DEFAULT_DB_ALIAS, connection
from django.urls import resolve, reverse

from postulo.documents.models import CV, CVItem, RenderedDocument
from postulo.jobs.models import Company
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


#: Every view that renders, fetches or reads a whole account, by the address it answers.
#: A name here is a promise that the view opens its own transactions; the tests below check
#: the writes each one makes.
SLOW = [
    ("jobs:capture_create", ()),
    ("jobs:company_logo_action", (1, "refresh")),
    ("documents:cv_export", (1,)),
    ("documents:send", (1,)),
    ("applications:report_pdf", ()),
    ("core:export_download", ()),
]

#: Ordinary pages, which stay inside the request's transaction. Listed so that a future
#: change which reaches for `non_atomic_requests` as a general performance measure fails
#: here and has to be argued for: leaving a transaction is a decision about what must
#: succeed or fail together, not a dial.
ORDINARY = [
    ("core:export", ()),
    ("jobs:company_list", ()),
    ("applications:list", ()),
]


def opts_out(name: str, args: tuple) -> bool:
    """Whether the view answering this address has left the request's transaction."""
    match = resolve(reverse(name, args=args))
    return DEFAULT_DB_ALIAS in getattr(match.func, "_non_atomic_requests", set())


@pytest.mark.parametrize("name,args", SLOW, ids=lambda value: str(value))
def test_the_slow_views_do_not_hold_the_database_while_they_work(name, args):
    assert opts_out(name, args), f"{name} still runs inside the request's transaction"


@pytest.mark.parametrize("name,args", ORDINARY, ids=lambda value: str(value))
def test_an_ordinary_view_still_runs_inside_one(name, args):
    assert not opts_out(name, args), f"{name} left the request's transaction"


def depth() -> int:
    """How deep in transactions this connection is.

    The suite itself runs inside one, so the number is never zero; what matters is that it
    goes *up* while a write happens, which is what a `transaction.atomic()` around that write
    means here.
    """
    return len(connection.savepoint_ids)


def test_a_logo_is_written_in_a_transaction_of_its_own(db, user, monkeypatch):
    """`CompanyLogoActionView` fetches a page and then up to six images before this."""
    from postulo.jobs import logos

    company = Company.objects.create(owner=user, name="Aperture Science")
    outside = depth()
    seen: dict[str, int] = {}
    original = Company.save

    def spy(self, *args, **kwargs):
        seen["depth"] = depth()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Company, "save", spy)
    logos.store(company, ContentFile(b"not really a png"), source="upload")

    assert seen["depth"] > outside, "the logo was saved outside any transaction"


def test_clearing_a_logo_is_written_in_one_too(db, user, monkeypatch):
    """The same act in the other direction, and the same fields, so they cannot drift."""
    from postulo.jobs import logos

    company = Company.objects.create(owner=user, name="Black Mesa")
    outside = depth()
    seen: dict[str, int] = {}
    original = Company.save

    def spy(self, *args, **kwargs):
        seen["depth"] = depth()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Company, "save", spy)
    logos.clear(company)

    assert seen["depth"] > outside


def test_a_snapshot_is_written_in_a_transaction_of_its_own(db, user, monkeypatch):
    """The row and what saving it sets off — a pending copy per connected store — are one act."""
    from postulo.documents.rendering import snapshot_cv

    class Backend:
        name = "fake"

        def is_available(self) -> bool:
            return True

        def render(self, html: str) -> bytes:
            return b"%PDF-1.7 fake"

    experience = Experience.objects.create(
        owner=user,
        organisation="Aperture",
        role="Engineer",
        start_date=datetime.date(2021, 3, 1),
    )
    cv = CV.objects.create(owner=user, name="Backend")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        order=0,
    )

    outside = depth()
    seen: dict[str, int] = {}
    original = RenderedDocument.save

    def spy(self, *args, **kwargs):
        seen["depth"] = depth()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RenderedDocument, "save", spy)
    snapshot_cv(cv, backend=Backend())

    assert seen["depth"] > outside, "the snapshot row was saved outside any transaction"


def test_a_capture_is_written_in_a_transaction_of_its_own(db, user, monkeypatch):
    """The one view that makes this server dial an address somebody else chose."""
    from postulo.jobs.models import Capture

    outside = depth()
    seen: dict[str, int] = {}
    original = Capture.save

    def spy(self, *args, **kwargs):
        seen["depth"] = depth()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Capture, "save", spy)
    _create_a_capture_through_the_view(user, monkeypatch)

    assert seen["depth"] > outside, "the capture was created outside any transaction"


def _create_a_capture_through_the_view(user, monkeypatch) -> None:
    """Drive `CaptureCreateView.post` with the fetch and the parse stood in for.

    The fetch and the parse live in `jobs/slow.py` now, and with no worker configured the
    handler runs where the request stands (#247) -- which is exactly the arrangement this
    test is about, and the one where holding the write lock would still matter.
    """
    from django.test import RequestFactory

    from postulo.jobs import capture_views

    class Source:
        name = "example"
        version = "1.0"

    class Data:
        def model_dump(self, mode=None):
            return {"title": "Research Engineer"}

    monkeypatch.setattr(
        "postulo.plugins.fetching.fetch_page",
        lambda url: type("P", (), {"url": url, "html": "<html>"})(),
    )
    monkeypatch.setattr("postulo.plugins.registry.parse_page", lambda url, html: (Data(), Source()))

    request = RequestFactory().post("/captures/new/", {"url": "https://example.org/job"})
    request.user = user
    capture_views.CaptureCreateView.as_view()(request)


def test_the_archive_reads_its_records_in_one_transaction(db, user, monkeypatch):
    """The view left the transaction; the manifest must not have left consistency with it.

    An archive whose JSON names an application that the rest of the same archive has never
    heard of would be worse than a slow one, so the records are still read together. The
    files are copied outside, and never had that guarantee: those bytes are on disk.
    """
    from postulo.core import export

    outside = depth()
    seen: dict[str, int] = {}
    original = export.build_document

    def spy(owner):
        seen["depth"] = depth()
        return original(owner)

    monkeypatch.setattr(export, "build_document", spy)
    export.write_archive(user)

    assert seen["depth"] > outside, "the manifest was read outside any transaction"


# ------------------------------------------- drawing the same document twice (#220)


class Counting:
    """A renderer that says how many times it was asked to draw."""

    name = "counting"

    def __init__(self) -> None:
        self.drawn: list[str] = []

    def is_available(self) -> bool:
        return True

    def render(self, html: str) -> bytes:
        self.drawn.append(html)
        return b"%PDF-1.7 " + str(len(self.drawn)).encode()


def test_the_same_draft_asked_for_twice_is_drawn_once():
    """A report download is a GET that files nothing, and a browser reloads downloads."""
    from postulo.documents import pdf

    backend = Counting()

    first = pdf.draft_pdf("<html>a report</html>", backend=backend)
    second = pdf.draft_pdf("<html>a report</html>", backend=backend)

    assert first == second
    assert backend.drawn == ["<html>a report</html>"], "drawn twice"


def test_a_different_document_is_a_different_draft():
    """The key is the SHA-256 of the HTML, so a hit means the input was identical."""
    from postulo.documents import pdf

    backend = Counting()

    pdf.draft_pdf("<html>one</html>", backend=backend)
    pdf.draft_pdf("<html>two</html>", backend=backend)

    assert len(backend.drawn) == 2


def test_a_snapshot_is_never_handed_a_draft(db, user):
    """What an employer received is drawn afresh; a copy of something else is not a record."""
    from postulo.documents import pdf

    backend = Counting()
    html = "<html>the same document</html>"
    pdf.draft_pdf(html, backend=backend)

    assert pdf.html_to_pdf(html, backend=backend) == b"%PDF-1.7 2", "it reused the draft"


def test_only_so_many_drafts_are_kept():
    """One worker, bounded: these are somebody's documents sitting in a process."""
    from postulo.documents import pdf

    backend = Counting()
    for index in range(pdf.DRAFT_CACHE_ENTRIES + 1):
        pdf.draft_pdf(f"<html>{index}</html>", backend=backend)

    pdf.draft_pdf("<html>0</html>", backend=backend)

    assert len(backend.drawn) == pdf.DRAFT_CACHE_ENTRIES + 2, "the oldest should have gone"


def test_a_draft_too_large_to_keep_is_not_kept():
    from postulo.documents import pdf

    class Huge(Counting):
        def render(self, html: str) -> bytes:
            self.drawn.append(html)
            return b"x" * (pdf.DRAFT_CACHE_MAX_BYTES + 1)

    backend = Huge()
    pdf.draft_pdf("<html>enormous</html>", backend=backend)
    pdf.draft_pdf("<html>enormous</html>", backend=backend)

    assert len(backend.drawn) == 2
