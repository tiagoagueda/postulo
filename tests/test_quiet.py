"""Gone quiet: noticing the silence, and what to do about it."""

import datetime as dt

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.models import Profile
from postulo.api.models import ApiToken
from postulo.applications import analytics, quiet
from postulo.applications.models import (
    Application,
    EventKind,
    InterviewKind,
    Reminder,
    Status,
)
from postulo.applications.services import change_status, record_event, schedule_interview
from postulo.jobs.models import Company, JobPosting
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


def sent(user, company, *, days_ago: int, title="Test Engineer", status=Status.APPLIED, source=""):
    """An application applied to ``days_ago`` days ago with nothing since."""
    posting = JobPosting.objects.create(owner=user, company=company, title=title, source=source)
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    when = timezone.now() - dt.timedelta(days=days_ago)
    change_status(application, Status.APPLIED, occurred_at=when)
    if status != Status.APPLIED:
        change_status(application, status, occurred_at=when)
    application.refresh_from_db()
    return application


def quiet_titles(user) -> list[str]:
    return [a.posting.title for a in quiet.quiet_applications(user)]


# ---------------------------------------------------------------- the predicate


def test_quiet_is_open_sent_silent_and_unplanned(user, company):
    old = sent(user, company, days_ago=30, title="Old")
    sent(user, company, days_ago=5, title="Fresh")
    draft = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(owner=user, company=company, title="Draft"),
        status=Status.DRAFT,
    )
    draft.created_at = timezone.now() - dt.timedelta(days=60)
    draft.save(update_fields=["created_at"])
    sent(user, company, days_ago=40, title="Settled", status=Status.REJECTED)

    assert quiet_titles(user) == ["Old"]
    assert old.pk in set(Application.objects.for_user(user).quiet(21).values_list("pk", flat=True))


def test_anything_planned_means_waiting_not_quiet(user, company):
    reminded = sent(user, company, days_ago=30, title="Reminded")
    Reminder.objects.create(
        owner=user,
        application=reminded,
        summary="Chase",
        due_at=timezone.now() + dt.timedelta(days=2),
    )
    booked = sent(user, company, days_ago=30, title="Booked")
    schedule_interview(
        booked, kind=InterviewKind.VIDEO, starts_at=timezone.now() + dt.timedelta(days=3)
    )
    overdue = sent(user, company, days_ago=30, title="Overdue reminder")
    Reminder.objects.create(
        owner=user,
        application=overdue,
        summary="Missed",
        due_at=timezone.now() - dt.timedelta(days=2),
    )
    done = sent(user, company, days_ago=30, title="Done reminder")
    Reminder.objects.create(
        owner=user,
        application=done,
        summary="Done",
        due_at=timezone.now() + dt.timedelta(days=2),
        done_at=timezone.now(),
    )

    assert quiet_titles(user) == ["Overdue reminder", "Done reminder"], (
        "a reminder ahead or an interview in the diary is a plan; an overdue or done one is not"
    )


def test_activity_of_any_kind_resets_the_silence(user, company):
    application = sent(user, company, days_ago=30)
    assert quiet_titles(user) == ["Test Engineer"]
    record_event(application, summary="They wrote back", kind=EventKind.EMAIL_RECEIVED)
    assert quiet_titles(user) == []
    application.refresh_from_db()
    row = Application.objects.for_user(user).with_activity().get(pk=application.pk)
    assert row.days_since_activity == 0


def test_the_threshold_is_the_persons_own(user, company):
    sent(user, company, days_ago=10)
    assert quiet.threshold_for(user) == 21
    assert quiet_titles(user) == []

    profile = Profile.objects.get(user=user)
    profile.quiet_after_days = 7
    profile.save()
    user.refresh_from_db()
    assert quiet.threshold_for(user) == 7
    assert quiet_titles(user) == ["Test Engineer"]


def test_an_application_with_no_events_still_has_a_last_activity(user, company):
    posting = JobPosting.objects.create(owner=user, company=company, title="Bare")
    bare = Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)
    bare.applied_at = timezone.now() - dt.timedelta(days=40)
    bare.save(update_fields=["applied_at"])
    row = Application.objects.for_user(user).with_activity().get(pk=bare.pk)
    assert row.last_activity_at == bare.applied_at
    assert quiet_titles(user) == ["Bare"]


# ------------------------------------------------------------------- the settings


def test_the_threshold_is_set_under_appearance(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert "Consider an application quiet after" in page and 'value="21"' in page

    response = client.post(
        reverse("settings:appearance"), {"theme": "system", "quiet_after_days": "14"}
    )
    assert response.status_code == 302
    assert Profile.objects.get(user=user).quiet_after_days == 14

    response = client.post(
        reverse("settings:appearance"), {"theme": "system", "quiet_after_days": "0"}
    )
    assert response.status_code == 200
    assert Profile.objects.get(user=user).quiet_after_days == 14


# ------------------------------------------------------------------ the dashboard


def test_the_dashboard_lists_them_longest_silence_first_with_three_actions(client, user, company):
    sent(user, company, days_ago=25, title="Newer silence")
    sent(user, company, days_ago=45, title="Older silence")
    client.force_login(user)

    response = client.get(reverse("core:home"))
    rows = list(response.context["quiet_applications"])
    assert [a.posting.title for a in rows] == ["Older silence", "Newer silence"]
    assert response.context["quiet_count"] == 2
    body = response.content.decode()
    assert "data-gone-quiet" in body and "quiet for 45 days" in body
    assert body.count('value="follow_up"') == 2
    assert body.count('value="snooze"') == 2
    assert body.count('value="ghosted"') == 2


def test_followed_up_records_the_follow_up_and_ends_the_silence(client, user, company):
    application = sent(user, company, days_ago=30)
    client.force_login(user)
    response = client.post(
        reverse("applications:quiet_action", args=[application.pk]),
        {"action": "follow_up", "next": reverse("core:home")},
    )
    assert response.status_code == 302 and response["Location"] == reverse("core:home")
    event = application.events.first()
    assert event.kind == EventKind.FOLLOW_UP and event.summary == "Followed up"
    assert quiet_titles(user) == []


def test_snooze_sets_a_reminder_two_weeks_out(client, user, company):
    application = sent(user, company, days_ago=30)
    client.force_login(user)
    client.post(reverse("applications:quiet_action", args=[application.pk]), {"action": "snooze"})
    reminder = Reminder.objects.get(application=application)
    assert "Aperture Science" in reminder.summary
    assert reminder.due_at - timezone.now() > dt.timedelta(days=13)
    assert quiet_titles(user) == [], "a plan means waiting"


def test_ghosted_goes_through_the_status_action(client, user, company):
    application = sent(user, company, days_ago=30)
    client.force_login(user)
    client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": "ghosted", "next": reverse("core:home")},
    )
    application.refresh_from_db()
    assert application.status == Status.GHOSTED
    assert application.events.filter(to_status=Status.GHOSTED).exists()
    assert quiet_titles(user) == []


def test_an_unknown_action_and_someone_elses_application_do_nothing(
    client, user, other_user, company
):
    application = sent(user, company, days_ago=30)
    client.force_login(user)
    client.post(reverse("applications:quiet_action", args=[application.pk]), {"action": "shout"})
    assert application.events.filter(kind=EventKind.FOLLOW_UP).count() == 0

    client.force_login(other_user)
    response = client.post(
        reverse("applications:quiet_action", args=[application.pk]), {"action": "follow_up"}
    )
    assert response.status_code == 404


# ----------------------------------------------------------- board, table, API


def test_the_board_badge_and_the_table_filter_use_the_same_predicate(client, user, company):
    quiet_one = sent(user, company, days_ago=30, title="Quiet one")
    sent(user, company, days_ago=3, title="Fresh one")
    client.force_login(user)

    board = client.get(reverse("applications:board"))
    cards = {
        a.pk: a.is_quiet for column in board.context["columns"] for a in column["applications"]
    }
    assert cards[quiet_one.pk] is True and sum(cards.values()) == 1
    assert board.content.decode().count("data-quiet") == 1

    table = client.get(reverse("applications:list"), {"quiet": "1"})
    assert [a.posting.title for a in table.context["applications"]] == ["Quiet one"]
    assert 'name="quiet" value="1" checked' in table.content.decode()
    assert table.context["table"].filters_active


def test_the_api_can_ask_for_the_quiet_ones(client, user, company):
    sent(user, company, days_ago=30, title="Quiet one")
    sent(user, company, days_ago=3, title="Fresh one")
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}
    listed = client.get("/api/v1/applications?quiet=true", **headers).json()
    assert [item["listing"]["title"] for item in listed["items"]] == ["Quiet one"]
    everything = client.get("/api/v1/applications", **headers).json()
    assert everything["count"] == 2


# -------------------------------------------------------------------- insights


def test_insights_count_the_quiet_ones_per_source_and_company(user, company):
    sent(user, company, days_ago=30, title="Quiet A", source="LinkedIn")
    sent(user, company, days_ago=40, title="Quiet B", source="LinkedIn")
    sent(user, company, days_ago=3, title="Fresh", source="LinkedIn")
    elsewhere = Company.objects.create(owner=user, name="Black Mesa")
    sent(user, elsewhere, days_ago=30, title="Quiet C", source="Referral")

    insights = analytics.build(user)
    assert insights.quiet_now == 3 and insights.quiet_after_days == 21
    by_source = {row.name: row for row in insights.sources}
    assert by_source["LinkedIn"].quiet == 2 and round(by_source["LinkedIn"].quiet_rate) == 67
    assert by_source["Referral"].quiet == 1
    assert insights.quiet_by_company == [("Aperture Science", 2), ("Black Mesa", 1)]


def test_the_sources_widget_shows_the_quiet_column(client, user, company):
    sent(user, company, days_ago=30, source="LinkedIn")
    profile = user.profile
    profile.dashboard_widgets = ["sources"]
    profile.save(update_fields=["dashboard_widgets"])
    client.force_login(user)
    body = client.get(reverse("core:home")).content.decode()
    assert "Quiet" in body and "data-quiet-by-company" in body


# ----------------------------------------------------------------- announcing


def email_connection(user, **config):
    connection = Connection(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=True,
        config={"to": "me@example.org", **config},
    )
    connection.save()
    return connection


def test_a_silence_is_announced_once_and_again_after_it_is_broken(user, company, settings):
    settings.POSTULO_PUBLIC_URL = "https://postulo.example.org"
    email_connection(user)
    quiet_one = sent(user, company, days_ago=60, title="Quiet one")
    sent(user, company, days_ago=55, title="Quiet two")
    sent(user, company, days_ago=3, title="Fresh")

    # The scheduler ran a month ago, when both had been silent for three weeks or more.
    a_month_ago = timezone.now() - dt.timedelta(days=30)
    stamped, delivered = quiet.announce_quiet_applications(at=a_month_ago)
    assert (stamped, delivered) == (2, 1), "one message per person, naming both"
    message = mail.outbox[-1]
    assert "2 applications have gone quiet" in message.subject
    assert "Quiet one at Aperture Science — 30 days" in message.body
    assert "Quiet two at Aperture Science" in message.body
    assert "https://postulo.example.org/applications/?quiet=1" in message.body

    assert quiet.announce_quiet_applications(at=a_month_ago) == (0, 0), "said once"
    assert len(mail.outbox) == 1

    # They wrote back three weeks ago, then fell silent again: that is a new silence.
    record_event(
        quiet_one,
        kind=EventKind.EMAIL_RECEIVED,
        summary="Reply",
        occurred_at=timezone.now() - dt.timedelta(days=22),
    )
    stamped, delivered = quiet.announce_quiet_applications()
    assert (stamped, delivered) == (1, 1)
    assert "1 application has gone quiet" in mail.outbox[-1].subject


def test_the_switch_on_the_connection_is_respected_and_the_stamp_still_set(user, company):
    email_connection(user, event_went_quiet=False)
    sent(user, company, days_ago=30)
    assert quiet.announce_quiet_applications() == (1, 0)
    assert len(mail.outbox) == 0
    application = Application.objects.get(owner=user)
    assert application.quiet_announced_at is not None


def test_the_scheduler_command_reports_quiet_applications(user, company, capsys):
    sent(user, company, days_ago=30)
    call_command("send_due_reminders")
    out = capsys.readouterr().out
    assert "1 applications gone quiet" in out
    call_command("send_due_reminders")
    assert "Nothing due." in capsys.readouterr().out


def test_a_long_list_is_cut_short_in_the_announcement(user, company):
    email_connection(user)
    for index in range(7):
        sent(user, company, days_ago=30 + index, title=f"Role {index}")
    quiet.announce_quiet_applications()
    body = mail.outbox[-1].body
    assert "and 2 more" in body and body.count(" at Aperture Science") == 5
