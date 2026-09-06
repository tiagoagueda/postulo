"""Letters of four kinds, and links: portfolios, profiles and videos.

The three things the issue asked for are three different problems. A motivation letter is
a **kind of text**, so letters gain a kind and each kind starts from its own shape. A
portfolio is mostly an **address**, so links are a career item that goes on the CV and can
be sent with an application. A video CV is, for almost everyone, an address too — an
unlisted upload somewhere — so it is a link of a video kind, and hosting video here is
deliberately not part of this.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.core import importer
from postulo.core.export import write_archive
from postulo.documents.models import (
    CV,
    LETTER_STARTERS,
    CoverLetter,
    CVItem,
    DocumentKind,
    LetterKind,
    Theme,
)
from postulo.documents.rendering import build_sections, snapshot_letter
from postulo.jobs.models import Company, JobPosting
from postulo.resume import links as link_checks
from postulo.resume.models import Link, LinkKind, LinkStatus

pytestmark = pytest.mark.django_db


class FakeBackend:
    name = "fake"

    def is_available(self):
        return True

    def render(self, html):
        return b"%PDF-1.7 fake"


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def a_link(user, **overrides):
    values = {
        "title": "Portfolio",
        "url": "https://alex.example/work",
        "kind": LinkKind.PORTFOLIO,
        "description": "Six projects.",
    }
    values.update(overrides)
    return Link.objects.create(owner=user, **values)


@pytest.fixture
def answering(monkeypatch):
    """A web that answers however the test says, without leaving the machine."""
    state = {"status": 200, "head_status": None, "raise": None, "calls": []}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def _answer(self, method, url):
            state["calls"].append((method, url))
            if state["raise"]:
                raise state["raise"]
            code = (
                state["head_status"]
                if method == "HEAD" and state["head_status"]
                else state["status"]
            )
            return httpx.Response(code, request=httpx.Request(method, url))

        def head(self, url, **kwargs):
            return self._answer("HEAD", url)

        def get(self, url, **kwargs):
            return self._answer("GET", url)

    monkeypatch.setattr(link_checks.httpx, "Client", FakeClient)
    monkeypatch.setattr(link_checks, "validate_public_url", lambda url: url)
    return state


def without_dns(url: str) -> str:
    """``validate_public_url`` with the name lookup replaced, and nothing else.

    The real one resolves a hostname and refuses unless every address it answers with is
    publicly routable. A test cannot resolve ``portfolio.example.org``, so the lookup is
    the one part stood in for: a literal private or loopback address is refused exactly
    as the real function would refuse it, and a name is taken to be public. What is being
    tested is *which requests are checked*, not the check itself, which has its own tests.
    """
    import ipaddress
    from urllib.parse import urlparse

    from postulo.plugins.fetching import UnsafeURL

    host = urlparse(url).hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not address.is_global:
        raise UnsafeURL("That address is on a private or local network.")
    return url


def with_transport(monkeypatch, transport):
    """Make every client the link checker builds speak to ``transport``.

    The guard being tested is an event hook on the real client, so the client itself has
    to be the real one; only the wire underneath it, and the name lookup, are replaced.
    """
    import httpx

    from postulo.plugins import http as plugin_http

    real = httpx.Client

    def build(*args, **kwargs):
        return real(*args, **{**kwargs, "transport": transport})

    monkeypatch.setattr(plugin_http.httpx, "Client", build)
    monkeypatch.setattr(plugin_http, "validate_public_url", without_dns)


# ------------------------------------------------------------------ letters


def test_a_letter_has_a_kind_and_starts_from_that_kinds_shape(client, user):
    client.force_login(user)
    html = client.get(reverse("documents:letter_create") + "?kind=motivation").content.decode()
    assert "Why this work" in html or "Why {{ company }}" in html
    assert 'value="motivation" selected' in html or "motivation" in html

    client.post(
        reverse("documents:letter_create"),
        {
            "name": "For the institute",
            "kind": LetterKind.MOTIVATION,
            "subject": "",
            "body": str(LETTER_STARTERS[LetterKind.MOTIVATION]),
            "theme": Theme.CLASSIC,
        },
    )
    letter = CoverLetter.objects.get(owner=user)
    assert letter.kind == LetterKind.MOTIVATION
    assert letter.document_kind == DocumentKind.MOTIVATION_LETTER


def test_every_kind_has_its_own_starter_and_a_default_theme():
    from postulo.documents.models import LETTER_THEMES

    for kind in LetterKind:
        assert str(LETTER_STARTERS[kind]).strip(), kind
        assert LETTER_THEMES[kind] in {value for value, _label in Theme.choices}
    # A motivation letter is sectioned prose; a cover letter is addressed and short.
    assert "Dear " in str(LETTER_STARTERS[LetterKind.COVER])
    assert "Why this work" in str(LETTER_STARTERS[LetterKind.MOTIVATION])
    assert "Dear " not in str(LETTER_STARTERS[LetterKind.MOTIVATION])


def test_an_existing_letter_is_a_cover_letter_and_is_left_alone(user):
    letter = CoverLetter.objects.create(owner=user, name="Old one", body="Dear …")
    assert letter.kind == LetterKind.COVER
    assert letter.document_kind == DocumentKind.COVER_LETTER


def test_a_render_is_filed_under_the_kind_of_letter_it_came_from(user, application):
    motivation = CoverLetter.objects.create(
        owner=user, name="Story", kind=LetterKind.MOTIVATION, body="Why this work…"
    )
    document = snapshot_letter(motivation, application=application, backend=FakeBackend())
    assert document.kind == DocumentKind.MOTIVATION_LETTER
    assert "motivation letter" in document.title

    cover = CoverLetter.objects.create(owner=user, name="Short", body="Dear …")
    assert snapshot_letter(cover, backend=FakeBackend()).kind == DocumentKind.COVER_LETTER


def test_the_letters_page_filters_by_kind(client, user):
    CoverLetter.objects.create(owner=user, name="A cover", body="x")
    CoverLetter.objects.create(owner=user, name="A story", kind=LetterKind.MOTIVATION, body="y")
    client.force_login(user)

    html = client.get(reverse("documents:letter_list")).content.decode()
    assert "A cover" in html and "A story" in html and "Letters" in html

    html = client.get(reverse("documents:letter_list") + "?kind=motivation").content.decode()
    assert "A story" in html and "A cover" not in html

    html = client.get(reverse("documents:letter_list") + "?kind=nonsense").content.decode()
    assert "A cover" in html and "A story" in html, "a filter nobody offers is ignored"


# -------------------------------------------------------------------- links


def test_a_link_is_a_career_item_and_can_go_on_a_cv(user):
    link = a_link(user, kind=LinkKind.VIDEO, title="Two minutes about me")
    assert link.host == "alex.example"
    assert str(link) == "Two minutes about me"

    from django.contrib.contenttypes.models import ContentType

    cv = CV.objects.create(owner=user, name="Backend EN")
    CVItem.objects.create(
        owner=user, cv=cv, content_type=ContentType.objects.get_for_model(Link), object_id=link.pk
    )
    sections = build_sections(cv)
    assert [section.kind for section in sections] == ["link"]
    assert sections[0].label == "Links"
    assert sections[0].items[0].item == link


def test_links_appear_on_the_career_record_with_their_own_section(client, user):
    a_link(user)
    client.force_login(user)
    html = client.get(reverse("resume:overview")).content.decode()
    assert "Links" in html and "https://alex.example/work" in html
    assert reverse("resume:link_check_all") in html


def test_a_link_can_be_sent_with_an_application(client, user, application):
    link = a_link(user)
    client.force_login(user)
    html = client.get(reverse("documents:send", args=[application.pk])).content.decode()
    assert "Links you pointed them at" in html and "Portfolio" in html

    response = client.post(
        reverse("documents:send", args=[application.pk]), {"links": [link.pk]}, follow=True
    )
    assert "Recorded what you sent" in response.content.decode()
    assert list(application.sent_links.all()) == [link]
    event = application.events.latest("pk")
    assert "https://alex.example/work" in event.body

    html = client.get(
        reverse("documents:application_documents", args=[application.pk])
    ).content.decode()
    assert "Links you pointed them at" in html and "https://alex.example/work" in html


def test_sending_still_insists_on_something(client, user, application):
    client.force_login(user)
    response = client.post(reverse("documents:send", args=[application.pk]), {})
    assert "Choose at least one document" in response.content.decode()


def test_links_are_private_to_their_owner(client, user, other_user):
    link = a_link(user)
    client.force_login(other_user)
    assert "alex.example" not in client.get(reverse("resume:overview")).content.decode()
    assert client.post(reverse("resume:link_check", args=[link.pk])).status_code == 404


# ------------------------------------------------------------- checking them


def test_checking_a_link_records_what_it_found(user, answering):
    link = a_link(user)
    link_checks.check(link)
    link.refresh_from_db()
    assert link.check_status == LinkStatus.OK and not link.is_broken
    assert "200" in link.check_detail and link.checked_at is not None
    assert answering["calls"] == [("HEAD", "https://alex.example/work")]

    answering["status"] = 404
    link_checks.check(link)
    link.refresh_from_db()
    assert link.is_broken and "404" in link.check_detail


def test_a_head_that_is_refused_is_asked_again_properly(user, answering):
    link = a_link(user)
    answering["head_status"] = 405
    link_checks.check(link)
    link.refresh_from_db()
    assert not link.is_broken
    assert [method for method, _url in answering["calls"]] == ["HEAD", "GET"]


def test_a_link_that_cannot_be_reached_at_all_says_so(user, answering):
    link = a_link(user)
    answering["raise"] = httpx.ConnectError("no route to host")
    link_checks.check(link)
    link.refresh_from_db()
    assert link.is_broken and "ConnectError" in link.check_detail


def test_a_private_address_is_never_visited(user, monkeypatch):
    from postulo.plugins.fetching import UnsafeURL

    def refuse(url):
        raise UnsafeURL("that address is not public.")

    monkeypatch.setattr(link_checks, "validate_public_url", refuse)
    link = a_link(user, url="http://192.168.1.20/portfolio")
    link_checks.check(link)
    link.refresh_from_db()
    assert link.is_broken and "not public" in link.check_detail


def test_a_redirect_towards_a_private_address_is_not_followed(user, monkeypatch):
    """The address a person saved is public; where it sends us afterwards may not be.

    This is the whole reason link checking does not use a bare ``httpx.Client``. A client
    told to follow redirects makes each hop itself, so a public host answering
    ``302 Location: http://127.0.0.1:9000/`` would be fetched and its answer written onto
    the link — a scan of whatever network the instance sits on, with the results shown.
    """
    import httpx

    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        if request.url.host == "portfolio.example.org":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:9000/admin/"})
        return httpx.Response(200)

    monkeypatch.setattr(link_checks, "validate_public_url", without_dns)
    with_transport(monkeypatch, httpx.MockTransport(handler))

    link = a_link(user, url="https://portfolio.example.org/")
    link_checks.check(link)
    link.refresh_from_db()

    assert reached == ["https://portfolio.example.org/"], "the second hop was never made"
    assert link.is_broken
    assert "private or local address" in link.check_detail


def test_a_redirect_to_another_public_address_is_followed(user, monkeypatch):
    """The guard refuses a destination, not redirection itself."""
    import httpx

    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        if request.url.host == "old.example.org":
            return httpx.Response(301, headers={"Location": "https://new.example.org/cv"})
        return httpx.Response(200)

    monkeypatch.setattr(link_checks, "validate_public_url", without_dns)
    with_transport(monkeypatch, httpx.MockTransport(handler))

    link = a_link(user, url="https://old.example.org/cv")
    link_checks.check(link)
    link.refresh_from_db()

    assert len(reached) == 2, "a public redirect is followed as it always was"
    assert not link.is_broken


def test_the_check_is_public_only_even_where_connections_may_be_private(
    user, monkeypatch, settings
):
    """POSTULO_CONNECTIONS_ALLOW_PRIVATE is about connections, not about a portfolio.

    An operator running Paperless on the same network has said so about *connections*. A
    portfolio address is a public thing by definition — a recruiter clicks it from the
    open internet — so a private one is broken whatever that setting says.
    """
    import httpx

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        if request.url.host == "portfolio.example.org":
            return httpx.Response(302, headers={"Location": "http://192.168.1.20/"})
        return httpx.Response(200)

    monkeypatch.setattr(link_checks, "validate_public_url", without_dns)
    with_transport(monkeypatch, httpx.MockTransport(handler))

    link = a_link(user, url="https://portfolio.example.org/")
    link_checks.check(link)
    link.refresh_from_db()

    assert reached == ["https://portfolio.example.org/"]
    assert link.is_broken


def test_checking_happens_only_when_a_person_asks(client, user, answering):
    a_link(user)
    client.force_login(user)
    client.get(reverse("resume:overview"))
    assert answering["calls"] == [], "opening the page fetches nothing"

    response = client.post(reverse("resume:link_check_all"), follow=True)
    assert "still answer" in response.content.decode()
    assert len(answering["calls"]) == 1


def test_checking_all_reports_the_ones_that_did_not_answer(client, user, answering):
    a_link(user)
    a_link(user, title="Old site", url="https://alex.example/old")
    answering["status"] = 410
    client.force_login(user)
    response = client.post(reverse("resume:link_check_all"), follow=True)
    assert "0 answered, 2 did not" in response.content.decode()
    assert Link.objects.for_user(user).filter(check_status=LinkStatus.BROKEN).count() == 2


def test_checking_one_says_which(client, user, answering):
    link = a_link(user)
    client.force_login(user)
    answering["status"] = 500
    response = client.post(reverse("resume:link_check", args=[link.pk]), follow=True)
    assert "Portfolio did not answer" in response.content.decode()


# ------------------------------------------------------------ the round trip


def test_links_and_letter_kinds_travel_in_the_export(user, other_user, application):
    link = a_link(user, kind=LinkKind.VIDEO, title="Two minutes")
    link.checked_at = timezone.now() - dt.timedelta(days=1)
    link.check_status = LinkStatus.OK
    link.check_detail = "Answered 200."
    link.save()
    application.sent_links.add(link)
    CoverLetter.objects.create(
        owner=user, name="Story", kind=LetterKind.MOTIVATION, body="Why this work…"
    )

    import json
    import zipfile

    archive = write_archive(user)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("postulo.json"))
    exported = manifest["resume"]["links"]
    assert exported[0]["title"] == "Two minutes" and exported[0]["kind"] == "video"
    assert exported[0]["check_status"] == "ok"
    assert manifest["documents"]["cover_letters"][0]["kind"] in {"cover", "motivation"}
    posting = manifest["companies"][0]["postings"][0]
    assert posting["applications"][0]["sent_link_ids"] == [link.pk]

    archive.seek(0)
    with zipfile.ZipFile(archive) as bundle:
        importer.load(other_user, bundle)
    restored = Link.objects.for_user(other_user).get()
    assert restored.title == "Two minutes" and restored.kind == "video"
    assert restored.check_status == "ok" and restored.checked_at is not None
    assert restored.pk != link.pk
    theirs = Application.objects.for_user(other_user).get()
    assert list(theirs.sent_links.all()) == [restored]
    assert CoverLetter.objects.for_user(other_user).filter(kind=LetterKind.MOTIVATION).exists()
