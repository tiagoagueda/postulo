"""Suggestions: what a plugin thinks happened, and the review that decides whether it did."""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import suggestions
from postulo.applications.models import (
    Application,
    ApplicationEvent,
    EventKind,
    Status,
    Suggestion,
    SuggestionStatus,
)
from postulo.applications.services import change_status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=5))
    application.refresh_from_db()
    return application


def a_suggestion(user, **overrides):
    values = {
        "source": "imap",
        "external_id": "<msg-1@example.org>",
        "kind": EventKind.EMAIL_RECEIVED,
        "summary": "Thank you for applying",
        "body": "We have received your application.",
        "context": {"From": "jobs@blackmesa.test", "Subject": "Your application"},
    }
    values.update(overrides)
    suggestion, created = suggestions.suggest(user, **values)
    return suggestion, created


# ------------------------------------------------------------------- filing


def test_a_suggestion_is_filed_once_per_source_and_identifier(user, application):
    first, created = a_suggestion(user, application=application)
    assert created and first.is_pending and first.is_matched
    assert first.owner == user and first.source == "imap"
    assert first.context["From"] == "jobs@blackmesa.test"

    again, created = a_suggestion(user, application=application, summary="Different words")
    assert not created and again.pk == first.pk
    assert again.summary == "Thank you for applying", "the first reading stands"
    assert Suggestion.objects.count() == 1

    other, created = a_suggestion(user, external_id="<msg-2@example.org>")
    assert created and other.pk != first.pk and not other.is_matched


def test_the_same_identifier_from_another_source_or_person_is_its_own(user, other_user):
    first, _ = a_suggestion(user)
    same_id_other_source, created = a_suggestion(user, source="dav")
    assert created and same_id_other_source.pk != first.pk
    theirs, created = a_suggestion(other_user)
    assert created and theirs.owner == other_user
    assert Suggestion.objects.count() == 3


def test_without_an_identifier_every_call_files_one(user):
    a_suggestion(user, external_id="")
    a_suggestion(user, external_id="")
    assert Suggestion.objects.count() == 2, "a source with nothing to key on gets no idempotence"


# ---------------------------------------------------------------- accepting


def test_accepting_writes_the_timeline_and_names_the_plugin(user, application):
    suggestion, _ = a_suggestion(
        user, application=application, occurred_at=timezone.now() - dt.timedelta(days=1)
    )
    suggestions.accept(suggestion)

    event = ApplicationEvent.objects.get(application=application, kind=EventKind.EMAIL_RECEIVED)
    assert event.summary == "Thank you for applying"
    assert event.body == "We have received your application."
    assert event.actor == "imap", "the record says which plugin put it there"
    assert event.occurred_at == suggestion.occurred_at

    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.ACCEPTED
    assert suggestion.event == event and suggestion.reviewed_at is not None

    # Accepting twice writes nothing further.
    suggestions.accept(suggestion)
    assert ApplicationEvent.objects.filter(kind=EventKind.EMAIL_RECEIVED).count() == 1


def test_a_suggestion_that_proposes_a_status_moves_the_application_through_the_log(
    user, application
):
    suggestion, _ = a_suggestion(
        user,
        application=application,
        kind=EventKind.REJECTION,
        summary="We are moving forward with other candidates",
        suggested_status=Status.REJECTED,
    )
    suggestions.accept(suggestion)

    application.refresh_from_db()
    assert application.status == Status.REJECTED
    event = ApplicationEvent.objects.filter(kind=EventKind.STATUS_CHANGE).latest("pk")
    assert event.to_status == Status.REJECTED and event.actor == "imap"
    assert "other candidates" in event.body or "other candidates" in event.summary
    suggestion.refresh_from_db()
    assert suggestion.event == event


def test_an_unmatched_suggestion_needs_an_application(user, application):
    suggestion, _ = a_suggestion(user)
    with pytest.raises(ValueError, match="needs an application"):
        suggestions.accept(suggestion)
    suggestions.accept(suggestion, application=application)
    suggestion.refresh_from_db()
    assert suggestion.application == application and suggestion.status == SuggestionStatus.ACCEPTED


def test_a_suggestion_cannot_be_accepted_onto_someone_elses_application(user, other_user):
    company = Company.objects.create(owner=other_user, name="Aperture")
    posting = JobPosting.objects.create(owner=other_user, company=company, title="Tester")
    theirs = Application.objects.create(owner=other_user, posting=posting, status=Status.APPLIED)
    suggestion, _ = a_suggestion(user)
    with pytest.raises(ValueError, match="belongs to someone else"):
        suggestions.accept(suggestion, application=theirs)


def test_declining_writes_nothing_and_is_remembered(user, application):
    suggestion, _ = a_suggestion(user, application=application)
    suggestions.decline(suggestion)
    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.DECLINED and suggestion.event is None
    assert not ApplicationEvent.objects.filter(kind=EventKind.EMAIL_RECEIVED).exists()

    again, created = a_suggestion(user, application=application)
    assert not created and again.status == SuggestionStatus.DECLINED, "never suggested twice"
    suggestions.accept(again)
    assert again.status == SuggestionStatus.DECLINED, "a declined one stays declined"


# ------------------------------------------------------------------- the page


def test_the_page_shows_what_is_waiting_and_accepts_it(client, user, application):
    suggestion, _ = a_suggestion(user, application=application)
    declined, _ = a_suggestion(user, external_id="<msg-9@example.org>", summary="Old news")
    suggestions.decline(declined)
    client.force_login(user)

    html = client.get(reverse("applications:suggestion_list")).content.decode()
    assert "Thank you for applying" in html and "jobs@blackmesa.test" in html
    assert "Old news" not in html, "answered ones are out of the way"
    assert (
        "Old news"
        in client.get(reverse("applications:suggestion_list") + "?show=all").content.decode()
    )

    response = client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "accept"]), follow=True
    )
    assert "Added to the timeline" in response.content.decode()
    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.ACCEPTED

    response = client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "accept"]), follow=True
    )
    assert "already been answered" in response.content.decode()


def test_an_unmatched_one_is_accepted_onto_the_application_chosen(client, user, application):
    suggestion, _ = a_suggestion(user)
    client.force_login(user)
    html = client.get(reverse("applications:suggestion_list")).content.decode()
    assert "Not matched to an application" in html
    assert f'<option value="{application.pk}">' in html

    response = client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "accept"]), follow=True
    )
    assert "Choose which application" in response.content.decode()

    client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "accept"]),
        {"application": application.pk},
    )
    suggestion.refresh_from_db()
    assert suggestion.application == application and suggestion.status == SuggestionStatus.ACCEPTED


def test_declining_from_the_page(client, user, application):
    suggestion, _ = a_suggestion(user, application=application)
    client.force_login(user)
    response = client.post(
        reverse("applications:suggestion_action", args=[suggestion.pk, "decline"]), follow=True
    )
    assert "will not be suggested again" in response.content.decode()
    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.DECLINED


def test_suggestions_are_private_to_their_owner(client, user, other_user, application):
    suggestion, _ = a_suggestion(user, application=application)
    client.force_login(other_user)
    assert (
        "Thank you for applying"
        not in client.get(reverse("applications:suggestion_list")).content.decode()
    )
    for action in ("accept", "decline"):
        url = reverse("applications:suggestion_action", args=[suggestion.pk, action])
        assert client.post(url).status_code == 404
    suggestion.refresh_from_db()
    assert suggestion.is_pending


def test_the_dashboard_says_when_something_is_waiting(client, user, application):
    client.force_login(user)
    assert "may have happened" not in client.get(reverse("core:home")).content.decode()
    a_suggestion(user, application=application)
    html = client.get(reverse("core:home")).content.decode()
    assert "may have happened" in html and reverse("applications:suggestion_list") in html
