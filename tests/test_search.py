"""One search box over everything, grouped by kind, owner-scoped, with the passage marked."""

import datetime as dt

import pytest
from django.core.files.base import ContentFile
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.applications.services import change_status, record_event
from postulo.core import search as searching
from postulo.documents.models import CV, CoverLetter, RenderedDocument, UploadedDocument
from postulo.jobs.models import Company, Contact, Industry, JobPosting
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(user, other_user):
    """A little of everything, with the word "portal" scattered where it should be found."""
    aperture = Company.objects.create(
        owner=user, name="Aperture Science", location="Cambridge", notes="They build portals."
    )
    aperture.industries.set(Industry.named(user, ["Research"]))
    Contact.objects.create(owner=user, company=aperture, name="Cave Johnson", role="CEO")
    listing = JobPosting.objects.create(
        owner=user, company=aperture, title="Portal Researcher", description="Study portals."
    )
    applied = JobPosting.objects.create(owner=user, company=aperture, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=applied, status=Status.DRAFT)
    change_status(application, Status.APPLIED)
    change_status(application, Status.REJECTED)
    record_event(
        application,
        summary="Spoke to Cave",
        body="He mentioned the portal gun project and a start in March.",
        occurred_at=timezone.now() - dt.timedelta(days=3),
    )
    Reminder.objects.create(
        owner=user,
        application=application,
        summary="Ask about the portal team",
        due_at=timezone.now(),
    )
    CoverLetter.objects.create(
        owner=user, name="Aperture letter", body="I have always admired portal research."
    )
    CV.objects.create(
        owner=user, name="Research CV", headline="Physicist", summary="Ten years of portals."
    )
    UploadedDocument.objects.create(
        owner=user,
        title="Portfolio",
        notes="Photos of the portal prototype",
        file=ContentFile(b"%PDF-1.7 x", name="p.pdf"),
    )
    RenderedDocument.objects.create(
        owner=user,
        title="CV for Aperture",
        kind="cv",
        application=application,
        file=ContentFile(b"%PDF-1.7 y", name="s.pdf"),
        source_text="Led the portal calibration team for three years.",
        rendered_at=dt.datetime(2026, 5, 12, tzinfo=dt.UTC),
    )
    Experience.objects.create(
        owner=user,
        organisation="Black Mesa",
        role="Portal technician",
        start_date=dt.date(2020, 1, 1),
        summary="Kept the portals open.",
    )
    # Somebody else's, with the same word everywhere.
    theirs = Company.objects.create(owner=other_user, name="Portal Ltd", notes="portal portal")
    JobPosting.objects.create(owner=other_user, company=theirs, title="Portal Manager")
    return {"application": application, "listing": listing, "company": aperture}


def kinds(groups) -> dict:
    return {group.kind: group for group in groups}


def test_everything_is_searched_and_grouped_and_nothing_of_anyone_elses(user, world):
    groups = kinds(searching.search(user, "portal"))
    assert set(groups) == {
        "applications",
        "listings",
        "companies",
        "contacts",
        "reminders",
        "sent",
        "letters",
        "cvs",
        "uploads",
        "career",
    } - {"contacts"}
    assert [hit.title for hit in groups["listings"].hits] == ["Portal Researcher"]
    assert [hit.title for hit in groups["companies"].hits] == ["Aperture Science"], "not Portal Ltd"
    assert groups["applications"].hits[0].title == "Test Engineer", (
        "a rejected application still counts"
    )
    assert "portal gun" in groups["applications"].hits[0].excerpt
    assert groups["career"].hits[0].title == "Portal technician · Black Mesa"


def test_the_case_of_accented_and_non_latin_letters_is_folded_on_sqlite(user):
    """SQLite's own LIKE folds A-Z only: "école" missed "École" (#505)."""
    for name in ("École Polytechnique", "Škoda", "Αθηναϊκή Εταιρεία"):
        Company.objects.create(owner=user, name=name)

    for query, name in (
        ("école", "École Polytechnique"),
        ("škoda", "Škoda"),
        ("αθηναϊκή", "Αθηναϊκή Εταιρεία"),
        ("ÉCOLE", "École Polytechnique"),
    ):
        groups = kinds(searching.search(user, query))
        assert [hit.title for hit in groups["companies"].hits] == [name], query


def test_the_link_to_all_of_a_group_keeps_the_query_whole(user):
    """`&` and `#` once cut the query off where the address read them as syntax (#506)."""
    from urllib.parse import parse_qs, urlsplit

    Company.objects.create(owner=user, name="R&D #1 Labs")

    groups = kinds(searching.search(user, "R&D #1"))

    assert parse_qs(urlsplit(groups["companies"].more_url).query)["q"] == ["R&D #1"]
    assert "#" not in groups["companies"].more_url


def test_no_group_offers_a_link_that_opens_its_list_unfiltered(user, world):
    for group in searching.search(user, "portal"):
        assert not group.more_url or "q=portal" in group.more_url, group.kind


def test_the_linked_applications_page_holds_as_many_rows_as_the_group_counts(client, user, world):
    """The text was in the timeline only, which the list's own box did not read (#506)."""
    client.force_login(user)
    group = kinds(searching.search(user, "portal gun"))["applications"]
    assert group.total == 1

    response = client.get(group.more_url)

    assert response.context["paginator"].count == group.total


def test_the_linked_companies_and_listings_pages_hold_as_many_rows_as_the_group_counts(
    client, user, world
):
    client.force_login(user)
    groups = kinds(searching.search(user, "portals"))

    for kind in ("companies", "listings"):
        response = client.get(groups[kind].more_url)
        assert response.context["paginator"].count == groups[kind].total, kind


def test_the_text_you_sent_says_where_it_went(user, world):
    groups = kinds(searching.search(user, "calibration"))
    hit = groups["sent"].hits[0]
    assert hit.subtitle == "in the CV you sent to Aperture Science on 12 May 2026"
    assert hit.url == world["application"].get_absolute_url()
    assert "calibration team" in hit.excerpt


def test_excerpts_frame_the_term_and_title_hits_come_first(user, world):
    text = "x" * 300 + " the portal appears here " + "y" * 300
    piece = searching.excerpt(text, "portal")
    assert piece.startswith("…") and piece.endswith("…") and "portal" in piece
    assert len(piece) < 200

    company = world["company"]
    Company.objects.create(owner=user, name="Zzz Portal Works", notes="")
    groups = kinds(searching.search(user, "portal"))
    titles = [hit.title for hit in groups["companies"].hits]
    assert titles == ["Zzz Portal Works", company.name], "the name hit before the notes hit"


def test_short_and_empty_queries_search_nothing(user, world):
    assert searching.search(user, "p") == []
    assert searching.search(user, "   ") == []
    assert searching.clean_query("  two   words  ") == "two words"


def test_the_page_groups_marks_and_links_to_more(client, user, world):
    for index in range(7):
        JobPosting.objects.create(
            owner=user, company=world["company"], title=f"Portal role {index}"
        )
    client.force_login(user)
    response = client.get(reverse("core:search"), {"q": "portal"})
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-group="listings"' in html and 'data-group="sent"' in html
    assert "<mark>Portal</mark> role" in html, "marked in the passage's own capitals"
    assert "All 8 in Listings" in html and reverse("listings:list") in html
    assert 'role="status"' in html and "results for" in html

    nothing = client.get(reverse("core:search"), {"q": "zebra"}).content.decode()
    assert "Nothing matches" in nothing
    short = client.get(reverse("core:search"), {"q": "p"}).content.decode()
    assert "at least two characters" in short
    assert client.get(reverse("core:search")).status_code == 200


def test_the_header_has_the_box_and_the_shortcut_marker(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert 'id="site-search"' in html and "data-search-shortcut" in html
    assert f'action="{reverse("core:search")}"' in html
    assert "<mark>" not in html


def test_search_needs_signing_in(client, db):
    response = client.get(reverse("core:search"), {"q": "portal"})
    assert response.status_code == 302 and "login" in response["Location"]


def test_highlight_escapes_what_it_does_not_mark():
    rendered = Template("{% load postulo %}{{ text|highlight:q }}").render(
        Context({"text": "<b>Portal</b> & portal", "q": "portal"})
    )
    assert rendered == "&lt;b&gt;<mark>Portal</mark>&lt;/b&gt; &amp; <mark>portal</mark>"
    plain = Template("{% load postulo %}{{ text|highlight:q }}").render(
        Context({"text": "<i>x</i>", "q": ""})
    )
    assert plain == "&lt;i&gt;x&lt;/i&gt;"


def test_highlight_and_excerpt_survive_a_dotted_capital_i():
    # "İ".lower() is two code points, which used to shift every later position (#377).
    assert searching.excerpt("İ" * 100 + " needle here", "needle", radius=5).count("needle")
    rendered = Template("{% load postulo %}{{ text|highlight:q }}").render(
        Context({"text": "İstanbul office", "q": "stan"})
    )
    assert rendered == "İ<mark>stan</mark>bul office"
    rendered = Template("{% load postulo %}{{ text|highlight:q }}").render(
        Context({"text": "In İzmir and İstanbul, stand-up", "q": "stand"})
    )
    assert rendered == "In İzmir and İstanbul, <mark>stand</mark>-up"


def test_the_api_returns_the_same_groups(client, user, world):
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    response = client.get("/api/v1/search?q=portal&limit=2", **headers)
    assert response.status_code == 200
    groups = {group["kind"]: group for group in response.json()}
    assert groups["listings"]["hits"][0]["title"] == "Portal Researcher"
    assert groups["listings"]["hits"][0]["web_url"].startswith("http://testserver/")
    assert groups["sent"]["hits"][0]["subtitle"].startswith("in the CV you sent to Aperture")
    assert all(len(group["hits"]) <= 2 for group in groups.values())
    assert client.get("/api/v1/search?q=p", **headers).json() == []
    assert client.get("/api/v1/search", **headers).status_code == 422, "q is required"


def test_a_sent_cover_letter_is_named_by_its_own_sentence(user, world):
    """The sentence is whole per kind, so a catalogue cases the noun itself (#392)."""
    rendered = RenderedDocument.objects.get(application=world["application"])
    rendered.kind = "cover_letter"
    rendered.save()
    hit = kinds(searching.search(user, "calibration"))["sent"].hits[0]
    assert hit.subtitle == "in the cover letter you sent to Aperture Science on 12 May 2026"
