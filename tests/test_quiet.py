"""Gone quiet: noticing the silence, and what to do about it."""

import datetime as dt

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import ngettext

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
        reverse("settings:appearance"),
        {"theme": "system", "density": "comfortable", "quiet_after_days": "14"},
    )
    assert response.status_code == 302
    assert Profile.objects.get(user=user).quiet_after_days == 14

    response = client.post(
        reverse("settings:appearance"),
        {"theme": "system", "density": "comfortable", "quiet_after_days": "0"},
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

    board = client.get(reverse("applications:list"), {"view": "board"})
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
    from allauth.account.models import EmailAddress

    # The notifier writes only to the person's own verified addresses since #232.
    EmailAddress.objects.get_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    connection = Connection(
        owner=user,
        kind="notifier",
        plugin="email",
        label="Mail me",
        enabled=True,
        config={"to": user.email, **config},
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


def test_a_silence_broken_and_resumed_reaches_a_webhook_twice(user, company):
    """The webhook refuses a key it has seen, so each silence needs a key of its own (#413)."""
    from postulo.notifications.models import WebhookDelivery
    from tests.test_webhooks import webhook_connection

    webhook_connection(user)
    application = sent(user, company, days_ago=60)

    quiet.announce_quiet_applications(at=timezone.now() - dt.timedelta(days=30))
    record_event(
        application,
        kind=EventKind.EMAIL_RECEIVED,
        summary="Reply",
        occurred_at=timezone.now() - dt.timedelta(days=22),
    )
    quiet.announce_quiet_applications()

    assert WebhookDelivery.objects.count() == 2


def test_the_key_of_a_long_list_still_fits_its_column(company):
    from types import SimpleNamespace

    from postulo.notifications.models import WebhookDelivery

    now = timezone.now()
    rows = [
        SimpleNamespace(
            pk=1000 + index,
            posting=SimpleNamespace(title=f"Role {index}", company=company),
            last_activity_at=now - dt.timedelta(days=30),
        )
        for index in range(100)
    ]

    message = quiet._announcement(rows, now)

    assert len(message.key) <= WebhookDelivery._meta.get_field("key").max_length


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


def test_a_silence_of_one_day_is_announced_in_the_singular(user, company):
    """ "1 days" was said at the threshold's smallest value (#391)."""
    sent(user, company, days_ago=2, title="Brand new")
    user.profile.quiet_after_days = 1
    user.profile.save()

    day_later = timezone.now() - dt.timedelta(days=1)

    body = quiet._announcement(list(quiet.quiet_applications(user)), day_later).body

    assert "Brand new at Aperture Science — 1 day" in body
    assert "1 days" not in body


def test_one_more_than_were_named_is_told_with_its_own_count(user, company):
    for index in range(6):
        sent(user, company, days_ago=30, title=f"Role {index}")

    body = quiet._announcement(list(quiet.quiet_applications(user)), timezone.now()).body

    assert body.splitlines()[-1] == "and 1 more"


def test_a_long_list_is_cut_short_in_the_announcement(user, company):
    email_connection(user)
    for index in range(7):
        sent(user, company, days_ago=30 + index, title=f"Role {index}")
    quiet.announce_quiet_applications()
    body = mail.outbox[-1].body
    assert "and 2 more" in body and body.count(" at Aperture Science") == 5


def test_the_quiet_pass_costs_the_same_however_many_applications_one_owner_has(user, company):
    """The owners are asked for once each, not once per application (#400)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    sent(user, company, days_ago=60, title="One")
    sent(user, company, days_ago=55, title="Two")

    def cost():
        quiet.announce_quiet_applications()
        # Everything is stamped now, so what is left is the per-owner question itself.
        with CaptureQueriesContext(connection) as queries:
            quiet.announce_quiet_applications()
        return len(queries)

    two = cost()
    sent(user, company, days_ago=50, title="Three")
    assert cost() == two


# ------------------------------------------------------------- what a pass costs


def someone(name: str, **fields):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email=f"{name}@example.org", password="not-a-real-password", **fields
    )


def a_silence(person, *, days_ago: int = 60, title="Test Engineer", status=Status.APPLIED):
    """One application of ``person``'s, at a company of their own, silent for ``days_ago`` days."""
    employer = Company.objects.create(owner=person, name="Aperture Science")
    return sent(person, employer, days_ago=days_ago, title=title, status=status)


def applications_loaded(run) -> int:
    """How many applications ``run`` read from the database into memory."""
    from django.db.models.signals import post_init

    loaded = []

    def count(sender, instance, **kwargs):
        loaded.append(instance.pk)

    post_init.connect(count, sender=Application)
    try:
        run()
    finally:
        post_init.disconnect(count, sender=Application)
    return len(loaded)


def test_a_pass_with_nothing_new_asks_once_for_the_owners_and_once_for_each(
    user, other_user, django_assert_num_queries
):
    """Each account was asked for twice, once only to read its threshold (#1120)."""
    for person in (user, other_user, someone("third")):
        a_silence(person)
    assert quiet.announce_quiet_applications()[0] == 3

    # The owners with their profiles, then the one question each of them is asked.
    with django_assert_num_queries(1 + 3):
        assert quiet.announce_quiet_applications() == (0, 0)


def test_an_account_with_nothing_that_can_go_quiet_is_not_asked_at_all(
    user, other_user, django_assert_num_queries
):
    """A draft or an application that has ended cost two questions every pass (#1120)."""
    a_silence(user)
    a_silence(other_user, status=Status.REJECTED)
    drafting = someone("drafting")
    Application.objects.create(
        owner=drafting,
        posting=JobPosting.objects.create(
            owner=drafting,
            company=Company.objects.create(owner=drafting, name="Black Mesa"),
            title="Draft",
        ),
        status=Status.DRAFT,
    )
    a_silence(someone("gone", is_active=False))
    assert quiet.announce_quiet_applications()[0] == 1

    with django_assert_num_queries(1 + 1):
        assert quiet.announce_quiet_applications() == (0, 0)
    assert Application.objects.filter(quiet_announced_at__isnull=False).count() == 1, (
        "an account that was closed is still left alone"
    )


def test_an_application_already_announced_is_not_read_again(user, company):
    """Every pass loaded each quiet application to find it had been said (#1120)."""
    email_connection(user)
    sent(user, company, days_ago=60, title="Said last month")
    quiet.announce_quiet_applications(at=timezone.now() - dt.timedelta(days=30))
    assert applications_loaded(quiet.announce_quiet_applications) == 0, "nothing new, nothing read"

    sent(user, company, days_ago=25, title="New silence")
    assert applications_loaded(quiet.announce_quiet_applications) == 1, "only the new one"
    body = mail.outbox[-1].body
    assert "New silence at Aperture Science" in body and "Said last month" not in body


def test_one_read_of_the_owners_still_gives_each_their_threshold_and_language(
    user, other_user, company
):
    """The threshold and the language come from the profile read with the owner (#1120)."""
    email_connection(user)
    sent(user, company, days_ago=10, title="Ten days")
    user.profile.quiet_after_days = 7
    user.profile.language = "fr-FR"
    user.profile.save()
    a_silence(other_user, days_ago=10)
    without_profile = someone("bare")
    Profile.objects.filter(user=without_profile).delete()
    a_silence(without_profile, days_ago=30)

    assert quiet.announce_quiet_applications() == (2, 1), (
        "seven days for one, the default three weeks for the others, profile or not"
    )
    assert set(
        Application.objects.filter(quiet_announced_at__isnull=False).values_list(
            "owner__email", flat=True
        )
    ) == {user.email, without_profile.email}
    with translation.override("fr-FR"):
        expected = ngettext(
            "%(count)s application has gone quiet", "%(count)s applications have gone quiet", 1
        ) % {"count": 1}
    assert expected != "1 application has gone quiet", "the catalogue has it in French"
    assert expected in mail.outbox[-1].subject


# ------------------------------------------------- numbers by status and by channel


def numbers(user, *, usual=21, by_status=None, by_channel=None):
    """Give ``user`` a usual number and the ones by status and by channel (#1099)."""
    profile = Profile.objects.get(user=user)
    profile.quiet_after_days = usual
    profile.quiet_days_by_status = by_status or {}
    profile.quiet_days_by_channel = by_channel or {}
    profile.save()
    user.refresh_from_db()


def through(application, channel):
    Application.objects.filter(pk=application.pk).update(channel=channel)
    application.refresh_from_db()
    return application


def test_a_status_number_comes_before_a_channel_number_before_the_usual_one(user, company):
    """Every application went quiet after one number of days, whatever its stage (#1099)."""
    through(
        sent(user, company, days_ago=12, title="Screening, recruiter", status=Status.SCREENING),
        "recruiter",
    )
    through(sent(user, company, days_ago=12, title="Applied, recruiter"), "recruiter")
    sent(user, company, days_ago=12, title="Applied, no channel")
    through(
        sent(user, company, days_ago=25, title="Offer, job board", status=Status.OFFER),
        "job_board",
    )
    numbers(user, usual=14, by_status={"screening": 10, "offer": 30}, by_channel={"recruiter": 7})

    assert sorted(quiet_titles(user)) == ["Applied, recruiter", "Screening, recruiter"], (
        "screening's 10 and the recruiter's 7 are past; the usual 14 and the offer's 30 are not"
    )
    numbers(user, usual=14, by_status={"screening": 20}, by_channel={"recruiter": 7})
    assert sorted(quiet_titles(user)) == ["Applied, recruiter", "Offer, job board"], (
        "screening's 20 wins over the recruiter's 7; the offer, with none of its own, has the usual"
    )
    numbers(user, usual=14)
    assert quiet_titles(user) == ["Offer, job board"], "with none set, the usual number alone"


def test_the_board_the_table_and_the_api_follow_the_same_rule(client, user, company):
    early = sent(user, company, days_ago=10, title="Screening", status=Status.SCREENING)
    sent(user, company, days_ago=10, title="Applied")
    numbers(user, by_status={"screening": 7})
    client.force_login(user)

    board = client.get(reverse("applications:list"), {"view": "board"})
    cards = {
        a.pk: a.is_quiet for column in board.context["columns"] for a in column["applications"]
    }
    assert cards[early.pk] is True and sum(cards.values()) == 1
    table = client.get(reverse("applications:list"), {"quiet": "1"})
    assert [a.posting.title for a in table.context["applications"]] == ["Screening"]
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    listed = client.get("/api/v1/applications?quiet=true", HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert [item["listing"]["title"] for item in listed.json()["items"]] == ["Screening"]


def test_the_dashboard_widget_follows_the_rule_and_names_no_one_number(client, user, company):
    """The widget said "nothing for 21 days" over a list held to other numbers (#1099)."""
    sent(user, company, days_ago=10, title="Screening", status=Status.SCREENING)
    numbers(user, by_status={"screening": 7})
    client.force_login(user)
    response = client.get(reverse("core:home"))
    assert [a.posting.title for a in response.context["quiet_applications"]] == ["Screening"]
    body = response.content.decode()
    assert "for as long as you allow them" in body
    assert "with nothing for 21 days" not in body


def test_the_pass_with_numbers_of_their_own_still_asks_once_for_each_owner(
    user, other_user, django_assert_num_queries
):
    """The rule is read off the profile that comes with the owner: no query per status (#1099)."""
    for person in (user, other_user, someone("third")):
        a_silence(person, days_ago=10, status=Status.SCREENING)
        numbers(person, by_status={"screening": 7}, by_channel={"recruiter": 3})
    assert quiet.announce_quiet_applications()[0] == 3, "each by screening's own seven days"

    with django_assert_num_queries(1 + 3):
        assert quiet.announce_quiet_applications() == (0, 0)


def test_the_numbers_are_set_beside_the_usual_one(client, user):
    """Blank is the usual number, and nothing is stored for it (#1099)."""
    client.force_login(user)
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="quiet_status_screening"' in page and 'name="quiet_channel_recruiter"' in page
    assert 'name="quiet_status_draft"' not in page

    form = {"theme": "system", "density": "comfortable", "quiet_after_days": "21"}
    response = client.post(
        reverse("settings:appearance"),
        {**form, "quiet_status_screening": "10", "quiet_channel_recruiter": "7"},
    )
    assert response.status_code == 302
    profile = Profile.objects.get(user=user)
    assert profile.quiet_days_by_status == {"screening": 10}
    assert profile.quiet_days_by_channel == {"recruiter": 7}
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="quiet_status_screening"' in page and 'value="10"' in page

    response = client.post(reverse("settings:appearance"), {**form, "quiet_status_screening": "0"})
    assert response.status_code == 200, "a number outside 1 to 365 is refused"
    assert Profile.objects.get(user=user).quiet_days_by_status == {"screening": 10}

    client.post(reverse("settings:appearance"), form)
    profile = Profile.objects.get(user=user)
    assert profile.quiet_days_by_status == {"screening": 10}, (
        "a page drawn before the boxes existed sends none, and leaves them as they were"
    )
    client.post(
        reverse("settings:appearance"),
        {**form, "quiet_status_screening": "", "quiet_channel_recruiter": ""},
    )
    profile = Profile.objects.get(user=user)
    assert profile.quiet_days_by_status == {} and profile.quiet_days_by_channel == {}


def test_saving_the_usual_number_on_its_own_keeps_the_others(client, user):
    numbers(user, by_status={"screening": 10}, by_channel={"recruiter": 7})
    profile = Profile.objects.get(user=user)
    profile.save_as_you_go = True
    profile.save()
    client.force_login(user)
    response = client.post(
        reverse("settings:save_field", args=["appearance", "quiet_after_days"]),
        {"quiet_after_days": "14"},
    )
    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.quiet_after_days == 14
    assert profile.quiet_days_by_status == {"screening": 10}
    assert profile.quiet_days_by_channel == {"recruiter": 7}


def test_the_application_page_says_when_it_counts_as_quiet(client, user, company):
    """Nothing said when an application would go quiet, or by which number (#1099)."""
    screening = sent(user, company, days_ago=3, title="Screening", status=Status.SCREENING)
    applied = through(sent(user, company, days_ago=3, title="Applied"), "recruiter")
    plain = sent(user, company, days_ago=30, title="Plain")
    numbers(user, by_status={"screening": 10}, by_channel={"recruiter": 5})
    client.force_login(user)

    body = client.get(screening.get_absolute_url()).content.decode()
    assert "data-quiet-outlook" in body and "Counts as quiet from" in body
    assert "That is the 10 days you set for Screening." in body
    body = client.get(applied.get_absolute_url()).content.decode()
    assert "That is the 5 days you set for applications through Recruiter." in body
    body = client.get(plain.get_absolute_url()).content.decode()
    assert "Gone quiet on" in body and "That is your usual 21 days." in body

    Reminder.objects.create(
        owner=user,
        application=plain,
        summary="Chase",
        due_at=timezone.now() + dt.timedelta(days=2),
    )
    body = client.get(plain.get_absolute_url()).content.decode()
    assert "Something is planned for it, so it does not count as quiet." in body

    response = client.post(
        reverse("applications:status", args=[screening.pk]),
        {"status": "interviewing"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="status-card",
    )
    swapped = response.content.decode()
    assert "That is your usual 21 days." in swapped, "the swapped card follows the new status"
    assert "Counts as quiet from" in swapped, "and the status change is the newest activity"


def test_the_outlook_agrees_with_the_predicate(user, company):
    application = through(sent(user, company, days_ago=8, title="Edge"), "recruiter")
    numbers(user, by_channel={"recruiter": 7})
    row = Application.objects.with_activity().get(pk=application.pk)
    seen = quiet.outlook(row, user)
    assert (seen.days, seen.source, seen.is_quiet) == (7, "channel", True)
    assert quiet_titles(user) == ["Edge"]
    numbers(user, by_channel={"recruiter": 9})
    seen = quiet.outlook(row, user)
    assert (seen.days, seen.is_quiet) == (9, False) and quiet_titles(user) == []
    change_status(application, Status.REJECTED)
    assert quiet.outlook(application, user) is None, "an ending never goes quiet"


def test_the_figures_are_worked_out_again_when_a_number_changes(user, company):
    sent(user, company, days_ago=10, status=Status.SCREENING)
    before = analytics.fingerprint(user)
    numbers(user, by_status={"screening": 7})
    assert analytics.fingerprint(user) != before
    figures = analytics.build(user)
    assert figures.quiet_now == 1 and figures.quiet_varies


def test_the_numbers_travel_in_the_archive_and_are_checked_on_the_way_back(user, other_user):
    import zipfile

    from postulo.core import export, importer

    numbers(user, by_status={"screening": 10}, by_channel={"recruiter": 7})
    document = export.build_document(user)
    assert document["account"]["profile"]["quiet_days_by_status"] == {"screening": 10}
    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    restored = Profile.objects.get(user=other_user)
    assert restored.quiet_days_by_status == {"screening": 10}
    assert restored.quiet_days_by_channel == {"recruiter": 7}

    profile = Profile.objects.get(user=user)
    value = importer._profile_value(
        profile, "quiet_days_by_status", {"screening": 10, "draft": 3, "offer": 0, "applied": "9"}
    )
    assert value == {"screening": 10}, "a draft cannot go quiet; 0 and text are not days"
    assert importer._profile_value(
        profile, "quiet_days_by_channel", {"recruiter": 7, "pigeon": 2}
    ) == {"recruiter": 7}
    refused = importer._profile_value(profile, "quiet_days_by_channel", ["recruiter"])
    assert refused is importer._REFUSED
