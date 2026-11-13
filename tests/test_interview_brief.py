"""An interview's own page, and the same page as a PDF to prepare from (#1091).

> Before an interview a person opens four or five pages to remember what they applied for.

What is held here: that each interview has a page that gathers when and where, who attends
(with the reader's own note on them), the listing's text, the company in brief, the documents
as they were sent, the timeline so far and the earlier interviews with how they went, and the
preparation notes; that the interview list, the application page and the calendar link to it;
that nobody opens another person's; that the same page is a PDF through a theme of its own
kind, downloaded and never filed, remembered on a POST only; and that a theme is handed plain
values and no model.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications import interview_brief
from postulo.applications.models import (
    Application,
    ApplicationEvent,
    EventKind,
    InterviewKind,
    InterviewOutcome,
    Status,
)
from postulo.applications.services import schedule_interview, settle_interview
from postulo.documents import themes
from postulo.documents.models import RenderedDocument
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db

PAGE = "applications:interview_detail"
FILE = "applications:interview_pdf"


@pytest.fixture(autouse=True)
def _no_plugin_themes():
    yield
    themes.forget()


@pytest.fixture
def drafts(monkeypatch):
    """What the renderer was handed, and a PDF that is not one, so these run without it."""
    seen: list[str] = []

    def draft(html, *args, **kwargs):
        seen.append(html)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", draft)
    return seen


@pytest.fixture
def application(user):
    company = Company.objects.create(
        owner=user, name="Aperture Science", website="https://aperture.example.org"
    )
    posting = JobPosting.objects.create(
        owner=user,
        company=company,
        title="Test Engineer",
        description="You will test portals.\nBring a helmet.",
    )
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


@pytest.fixture
def interview(user, application):
    boss = Contact.objects.create(
        owner=user,
        company=application.posting.company,
        name="Cave Johnson",
        role="CEO",
        email="cave@aperture.test",
    )
    boss.write_note(user, "Likes lemons. Ask about the roadmap.")
    made = schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=timezone.now() + dt.timedelta(days=3),
        location="https://meet.example.org/abc",
        contacts=[boss],
        notes="Prepare the story about the migration.",
        remind=False,
    )
    return made


@pytest.fixture
def sent(user, application):
    return RenderedDocument.objects.create(
        owner=user,
        title="CV for Aperture",
        kind="cv",
        application=application,
        file=ContentFile(b"%PDF-1.7 sent", name="sent.pdf"),
        checksum="x",
    )


def read(client, interview) -> str:
    return client.get(reverse(PAGE, args=[interview.pk])).content.decode()


# ------------------------------------------------------------------------- the page


def test_the_page_gathers_what_to_prepare_from(client, user, interview, sent):
    """Where it used to take the listing, the company, the contacts, the documents and the
    timeline, each on a page of its own, this one page holds them (#1091)."""
    client.force_login(user)

    page = read(client, interview)

    assert "Prepare the story about the migration." in page, "the preparation notes"
    assert "Cave Johnson" in page and "cave@aperture.test" in page, "who attends"
    assert "Likes lemons. Ask about the roadmap." in page, "the reader's own note on them"
    assert "Bring a helmet." in page, "the listing's text"
    assert "Aperture Science" in page and "https://aperture.example.org" in page
    assert "CV for Aperture" in page, "what was sent"
    assert reverse("documents:rendered_download", args=[sent.pk]) in page
    assert "Join the call" in page and "Video call" in page
    assert "Video call scheduled for" in page, "the timeline so far"


def test_a_page_with_nothing_says_so(client, user, application):
    interview = schedule_interview(
        application, kind=InterviewKind.PHONE, starts_at=timezone.now() + dt.timedelta(days=2)
    )
    application.posting.description = ""
    application.posting.save()
    client.force_login(user)

    page = read(client, interview)

    assert "Nobody is named for this interview." in page
    assert "Nothing recorded as sent for this application." in page
    assert "No text recorded for this listing." in page
    assert "Nothing written yet." in page


def test_earlier_interviews_of_the_application_say_how_they_went(client, user, application):
    first = schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() + dt.timedelta(days=1),
        remind=False,
    )
    settle_interview(first, InterviewOutcome.DONE, note="They liked the portal demo.")
    later = schedule_interview(
        application,
        kind=InterviewKind.ONSITE,
        starts_at=timezone.now() + dt.timedelta(days=5),
        remind=False,
    )
    client.force_login(user)

    page = read(client, later)
    first_page = read(client, first)

    assert "Earlier interviews for this application" in page
    assert "Phone screen" in page and "Held" in page
    assert "They liked the portal demo." in page
    assert "Earlier interviews for this application" not in first_page, "none came before it"


def test_the_timeline_of_a_past_interview_stops_where_it_began(client, user, application):
    began = timezone.now() - dt.timedelta(days=5)
    held = schedule_interview(application, kind=InterviewKind.VIDEO, starts_at=began)
    ApplicationEvent.objects.create(
        application=application,
        kind=EventKind.NOTE,
        summary="Before the call",
        occurred_at=began - dt.timedelta(days=1),
    )
    ApplicationEvent.objects.create(
        application=application,
        kind=EventKind.NOTE,
        summary="Afterwards, a week on",
        occurred_at=began + dt.timedelta(days=1),
    )
    client.force_login(user)

    page = read(client, held)

    assert "Before the call" in page
    assert "Afterwards, a week on" not in page


def test_another_persons_interview_is_not_found(client, user, other_user, interview, drafts):
    client.force_login(other_user)

    assert client.get(reverse(PAGE, args=[interview.pk])).status_code == 404
    assert client.get(reverse(FILE, args=[interview.pk])).status_code == 404
    assert client.post(reverse(FILE, args=[interview.pk])).status_code == 404
    assert drafts == []


def test_a_person_who_is_signed_out_is_sent_to_sign_in(client, interview):
    assert client.get(reverse(PAGE, args=[interview.pk])).status_code == 302


def test_a_contact_nobody_may_open_is_a_name_and_no_more(client, user, other_user, application):
    """A contact somebody else keeps privately, put on this interview, is not read for its
    details or its note: the reader sees what they are called and nothing else (#896)."""
    theirs = Contact.objects.create(owner=other_user, name="Glados", email="glados@secret.test")
    theirs.write_note(other_user, "Not for you.")
    meeting = schedule_interview(
        application,
        kind=InterviewKind.VIDEO,
        starts_at=timezone.now() + dt.timedelta(days=2),
        remind=False,
    )
    # The services refuse it, so this is what is left when a share is withdrawn.
    meeting.contacts.add(theirs)
    client.force_login(user)

    page = read(client, meeting)

    assert "Glados" in page
    assert "glados@secret.test" not in page and "Not for you." not in page


def test_the_list_the_application_and_the_calendar_link_to_it(client, user, interview):
    client.force_login(user)
    url = reverse(PAGE, args=[interview.pk])

    assert interview.get_absolute_url() == url, "what the calendar and the API link to"
    assert f'href="{url}"' in client.get(reverse("applications:interview_list")).content.decode()
    assert f'href="{url}"' in client.get(interview.application.get_absolute_url()).content.decode()
    month = timezone.localtime(interview.starts_at).strftime("%Y-%m")
    calendar = client.get(reverse("applications:calendar"), {"view": "agenda"}).content.decode()
    assert url in calendar, month


# --------------------------------------------------------------------------- the PDF


def printed(client, drafts, interview, **form) -> str:
    response = client.post(reverse(FILE, args=[interview.pk]), {"language": "en-GB", **form})
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    return drafts[-1]


def test_the_pdf_holds_what_the_page_holds(client, user, interview, sent, drafts):
    client.force_login(user)

    html = printed(client, drafts, interview)

    for said in (
        "Prepare the story about the migration.",
        "Cave Johnson",
        "Likes lemons. Ask about the roadmap.",
        "Bring a helmet.",
        "Aperture Science",
        "CV for Aperture",
        "Video call scheduled for",
    ):
        assert said in html, said
    assert "<button" not in html and "<a " not in html, "nothing that is a control"


def test_the_pdf_is_named_for_the_meeting_and_never_filed(client, user, interview, drafts):
    client.force_login(user)
    before = RenderedDocument.objects.count()

    response = client.post(reverse(FILE, args=[interview.pk]), {"language": "en-GB"})

    assert (
        f"postulo-interview-{timezone.localtime(interview.starts_at):%Y-%m-%d}"
        in (response["Content-Disposition"])
    )
    assert RenderedDocument.objects.count() == before


def test_a_theme_is_remembered_on_a_post_only(client, user, interview, drafts):
    client.force_login(user)

    client.get(reverse(FILE, args=[interview.pk]), {"theme": "classic"})
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {}

    printed(client, drafts, interview, theme="classic")
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"interview": "classic"}
    assert 'value="classic" selected' in read(client, interview)


def test_the_language_and_the_direction_are_the_documents(client, user, interview, drafts):
    client.force_login(user)

    html = printed(client, drafts, interview, language="ar")

    assert 'lang="ar"' in html and 'dir="rtl"' in html


def test_a_paper_the_theme_does_not_print_on_is_said_and_nothing_is_drawn(
    client, user, interview, drafts
):
    themes.register(
        themes.Theme(
            name="upright",
            label="Upright",
            templates={themes.Kind.INTERVIEW: "documents/themes/plain/interview.html"},
            papers=("a4-portrait",),
        )
    )
    client.force_login(user)

    response = client.post(
        reverse(FILE, args=[interview.pk]), {"theme": "upright", "paper": "a4-landscape"}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse(PAGE, args=[interview.pk])
    assert drafts == []


# ------------------------------------------------------------------------- the theme


def test_it_is_a_kind_of_its_own_set_by_both_shipped_themes():
    assert themes.Kind.INTERVIEW == "interview"
    assert {theme.name for theme in themes.for_kind(themes.Kind.INTERVIEW)} == {
        "plain",
        "classic",
    }


def test_a_theme_is_handed_plain_values_and_no_model(user, interview, sent):
    """A theme from a plugin draws what the page draws, from strings, numbers, booleans,
    dates and lists and mappings of them: nothing it is handed reaches the database."""
    context = interview_brief.as_context(user, interview)

    def plain(value) -> bool:
        if isinstance(value, dict):
            return all(isinstance(key, str) and plain(item) for key, item in value.items())
        if isinstance(value, list | tuple):
            return all(plain(item) for item in value)
        return value is None or isinstance(value, str | int | float | bool | dt.date | dt.datetime)

    assert plain(context), context


def test_a_plugin_theme_that_sets_only_lists_is_not_offered(client, user, interview):
    themes.register(
        themes.Theme(name="roster", label="Roster", templates={themes.Kind.LIST: "x.html"})
    )
    client.force_login(user)

    assert 'value="roster"' not in read(client, interview)
    assert interview_brief.theme_for(user, "roster") == "plain"


def test_a_plugin_theme_for_interviews_is_offered_and_draws(
    client, user, interview, drafts, tmp_path, settings
):
    template = tmp_path / "prep.html"
    template.write_text(
        "<html><body>PREP|{{ title }}|{{ company.name }}|{{ contacts|length }}</body></html>",
        encoding="utf-8",
    )
    settings.TEMPLATES[0]["DIRS"] = [*settings.TEMPLATES[0]["DIRS"], str(tmp_path)]
    from django.template import engines

    for engine in engines.all():
        engine.engine.dirs = [*engine.engine.dirs, str(tmp_path)]
    themes.register(
        themes.Theme(
            name="prep",
            label="Prep",
            templates={themes.Kind.INTERVIEW: "prep.html"},
            provider="Prep Press",
            papers=("any",),
        )
    )
    client.force_login(user)

    assert "Prep, from Prep Press" in read(client, interview)
    html = printed(client, drafts, interview, theme="prep")

    assert "PREP|Video call · Test Engineer at Aperture Science|Aperture Science|1" in html
