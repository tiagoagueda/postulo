"""Reminders on the plugin surface, with one service each to make, change, move, tick off and
untick one, and every caller moved onto them (#331)."""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
import zoneinfo
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications import agenda, services, views
from postulo.applications.models import Application, Reminder, Status
from postulo.applications.services import (
    REMINDER_HOUR,
    add_reminder,
    change_reminder,
    complete_reminder,
    create_reminder,
    postpone_reminder,
    reopen_reminder,
)
from postulo.core import export, importer
from postulo.jobs.models import Company, JobPosting
from postulo.plugins import api

pytestmark = pytest.mark.django_db

SRC = Path(__file__).resolve().parent.parent / "src" / "postulo"


def soon(days=3):
    return timezone.now() + dt.timedelta(days=days)


def application_of(owner, title="Test Engineer"):
    company = Company.objects.create(owner=owner, name=f"Aperture {title}")
    posting = JobPosting.objects.create(owner=owner, company=company, title=title)
    return Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)


def stored(reminder):
    return Reminder.objects.get(pk=reminder.pk)


# ---------------------------------------------------------------------------- the surface


def test_the_record_and_the_five_calls_are_the_cores_own():
    from postulo.applications import models

    assert api.Reminder is models.Reminder
    for name in (
        "create_reminder",
        "change_reminder",
        "postpone_reminder",
        "complete_reminder",
        "reopen_reminder",
    ):
        assert name in api.__all__, name
        assert getattr(api, name) is getattr(services, name), name
    assert "Reminder" in api.__all__
    assert "change_status" not in api.__all__, "a plugin proposes a status through `suggest`"


# ---------------------------------------------------------------------------- create


def test_create_records_the_actor_and_cuts_the_summary(user):
    made = create_reminder(user, summary="x" * 300, due=soon(), actor="API token phone")

    made = stored(made)
    assert len(made.summary) == 250
    assert (made.owner, made.actor, made.source) == (user, "API token phone", "")


def test_create_refuses_another_persons_application(user, other_user):
    theirs = application_of(other_user)

    with pytest.raises(ValueError, match="someone else"):
        create_reminder(user, summary="Chase", due=soon(), application=theirs)
    assert not Reminder.objects.exists()


def test_create_refuses_a_reminder_that_says_nothing(user):
    with pytest.raises(ValueError):
        create_reminder(user, summary="   ", due=soon())


def test_a_date_lands_at_nine_in_the_owners_zone_outside_any_request(user):
    user.profile.time_zone = "Pacific/Auckland"
    user.profile.save()
    day = dt.date(2030, 3, 4)

    # The active zone is not the owner's, as in the scheduler or a worker.
    with timezone.override(zoneinfo.ZoneInfo("America/New_York")):
        made = create_reminder(user, summary="Declare", due=day)

    local = stored(made).due_at.astimezone(zoneinfo.ZoneInfo("Pacific/Auckland"))
    assert (local.date(), local.hour, local.minute) == (day, REMINDER_HOUR, 0)


def test_a_moment_is_kept_as_it_is(user):
    when = soon(2).replace(microsecond=0)

    assert stored(create_reminder(user, summary="Ring", due=when)).due_at == when


# ---------------------------------------------------------------------------- change


def test_change_rewords_and_records_the_actor(user):
    made = create_reminder(user, summary="Chase", due=soon())

    change_reminder(made, summary="Ring them", actor="postulo-dav")

    assert (stored(made).summary, stored(made).actor) == ("Ring them", "postulo-dav")


def test_change_refuses_another_persons_application_and_cuts(user, other_user):
    made = create_reminder(user, summary="Chase", due=soon())

    with pytest.raises(ValueError, match="someone else"):
        change_reminder(made, application=application_of(other_user))
    change_reminder(made, summary="y" * 400)

    assert stored(made).application_id is None and len(stored(made).summary) == 250


def test_change_moves_a_reminder_onto_and_off_an_application(user):
    mine = application_of(user)
    made = create_reminder(user, summary="Chase", due=soon())

    change_reminder(made, application=mine)
    assert stored(made).application_id == mine.pk
    change_reminder(made, application=None)
    assert stored(made).application_id is None


def test_a_change_that_changes_nothing_writes_nothing(user):
    made = create_reminder(user, summary="Chase", due=soon(), actor="API token phone")

    change_reminder(made, summary="Chase", actor="")

    assert stored(made).actor == "API token phone", "nobody touched it"


# ---------------------------------------------------------------------------- postpone


def test_a_move_clears_the_stamp_and_records_the_actor(user):
    made = create_reminder(user, summary="Chase", due=soon(1))
    Reminder.objects.filter(pk=made.pk).update(notified_at=timezone.now())

    postpone_reminder(stored(made), soon(4), actor="postulo-dav")

    assert stored(made).notified_at is None and stored(made).actor == "postulo-dav"


def test_a_move_to_a_date_lands_at_nine_in_the_owners_zone(user):
    user.profile.time_zone = "Europe/Lisbon"
    user.profile.save()
    made = create_reminder(user, summary="Chase", due=soon(1))

    with timezone.override(zoneinfo.ZoneInfo("Asia/Tokyo")):
        postpone_reminder(made, dt.date(2030, 7, 1))

    local = stored(made).due_at.astimezone(zoneinfo.ZoneInfo("Europe/Lisbon"))
    assert (local.date(), local.hour) == (dt.date(2030, 7, 1), REMINDER_HOUR)


def test_a_done_reminder_is_not_moved(user):
    made = create_reminder(user, summary="Chase", due=soon(1))
    complete_reminder(made)
    due = stored(made).due_at

    postpone_reminder(stored(made), soon(9))

    assert stored(made).due_at == due


# ---------------------------------------------------------------------------- complete


def test_complete_records_when_and_who(user):
    made = create_reminder(user, summary="Chase", due=soon())
    yesterday = timezone.now() - dt.timedelta(days=1)

    complete_reminder(made, at=yesterday, actor="postulo-dav")

    assert (stored(made).done_at, stored(made).actor) == (yesterday, "postulo-dav")


def test_complete_refuses_a_moment_in_the_future(user):
    made = create_reminder(user, summary="Chase", due=soon())

    with pytest.raises(ValueError, match="future"):
        complete_reminder(made, at=soon(1))
    assert stored(made).done_at is None


def test_complete_keeps_the_first_tick(user):
    made = create_reminder(user, summary="Chase", due=soon())
    complete_reminder(made, actor="API token phone")
    first = stored(made).done_at

    complete_reminder(stored(made), actor="")

    assert (stored(made).done_at, stored(made).actor) == (first, "API token phone")


# ---------------------------------------------------------------------------- reopen


def test_reopening_unticks_and_does_not_announce_again(user):
    made = create_reminder(user, summary="Chase", due=timezone.now() - dt.timedelta(hours=1))
    announced = timezone.now()
    Reminder.objects.filter(pk=made.pk).update(notified_at=announced)
    complete_reminder(stored(made))

    reopen_reminder(stored(made), actor="postulo-dav")

    made = stored(made)
    assert made.done_at is None and made.actor == "postulo-dav"
    assert made.notified_at == announced, "a tick undone by mistake does not ring twice"
    assert made in Reminder.objects.outstanding()


def test_reopening_one_not_done_changes_nothing(user):
    made = create_reminder(user, summary="Chase", due=soon(), actor="API token phone")

    reopen_reminder(made, actor="")

    assert stored(made).actor == "API token phone"


# ------------------------------------------------------------- add_reminder is one path


def test_add_reminder_makes_a_new_entry_through_create_reminder(user, monkeypatch):
    calls = []
    real = services.create_reminder

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "create_reminder", spy)

    made = add_reminder(
        user, summary="Declare", due_at=soon(), uid="u1", source="p", done=True, actor="p"
    )

    assert [call["uid"] for call in calls] == ["u1"]
    assert stored(made).is_done and stored(made).actor == "p"


# ----------------------------------------------------------------------- the pages


def test_the_form_makes_a_reminder_through_the_service(client, user, monkeypatch):
    calls = []
    real = views.create_reminder
    monkeypatch.setattr(views, "create_reminder", lambda *a, **k: calls.append(k) or real(*a, **k))
    client.force_login(user)

    response = client.post(
        reverse("applications:reminder_create"),
        {"summary": "Chase", "due_at": "2030-01-02T10:00"},
    )

    assert response.status_code == 302
    assert [call["summary"] for call in calls] == ["Chase"]
    assert Reminder.objects.get(owner=user).actor == ""


def test_snooze_makes_a_reminder_through_the_service(client, user, monkeypatch):
    calls = []
    real = views.create_reminder
    monkeypatch.setattr(views, "create_reminder", lambda *a, **k: calls.append(k) or real(*a, **k))
    application = application_of(user)
    client.force_login(user)

    client.post(reverse("applications:quiet_action", args=[application.pk]), {"action": "snooze"})

    assert len(calls) == 1 and Reminder.objects.filter(application=application).count() == 1


def test_not_done_on_the_page_unticks_a_reminder(client, user):
    made = create_reminder(user, summary="Chase", due=soon())
    complete_reminder(made)
    client.force_login(user)

    response = client.post(reverse("applications:reminder_reopen", args=[made.pk]))

    assert response.status_code == 302 and not stored(made).is_done


def test_not_done_is_offered_beside_a_done_reminder_on_the_calendar(client, user):
    made = create_reminder(user, summary="Chase", due=soon())
    complete_reminder(made)
    client.force_login(user)
    day = f"{timezone.localdate(stored(made).due_at):%Y-%m-%d}"

    page = client.get(reverse("applications:calendar"), {"view": "day", "on": day})

    assert reverse("applications:reminder_reopen", args=[made.pk]) in page.content.decode()


def test_nobody_unticks_another_persons_reminder(client, user, other_user):
    theirs = create_reminder(other_user, summary="Theirs", due=soon())
    complete_reminder(theirs)
    client.force_login(user)

    response = client.post(reverse("applications:reminder_reopen", args=[theirs.pk]))

    assert response.status_code == 404 and stored(theirs).is_done


def test_the_edit_form_does_not_quietly_drop_a_new_time_for_a_done_reminder(client, user):
    made = create_reminder(user, summary="Chase", due=soon())
    complete_reminder(made)
    client.force_login(user)

    response = client.post(
        reverse("applications:reminder_update", args=[made.pk]),
        {"summary": "Chase", "due_at": "2031-05-05T10:00"},
    )

    assert response.status_code == 200, "the form is drawn again, saying why"
    assert stored(made).due_at == made.due_at


def test_the_edit_form_rewords_and_moves_through_the_services(client, user):
    made = create_reminder(user, summary="Chase", due=soon(1), actor="API token phone")
    Reminder.objects.filter(pk=made.pk).update(notified_at=timezone.now())
    client.force_login(user)

    client.post(
        reverse("applications:reminder_update", args=[made.pk]),
        {"summary": "Ring them", "due_at": "2031-05-05T10:00"},
    )

    made = stored(made)
    assert made.summary == "Ring them" and made.notified_at is None
    assert made.actor == "", "the person changed it last"


def test_who_changed_it_is_said_beside_it(user):
    application = application_of(user)
    made = create_reminder(
        user, summary="Chase", due=soon(), application=application, actor="API token phone"
    )

    assert "via API token phone" in agenda.reminder_event(stored(made)).detail


def test_who_changed_it_is_said_on_the_applications_page(client, user):
    application = application_of(user)
    create_reminder(
        user, summary="Chase", due=soon(), application=application, actor="API token phone"
    )
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert re.search(r"via\s+API token phone", page)


# ------------------------------------------------------------------------------ the API


def bearer(user):
    _token, raw = ApiToken.issue(owner=user, name="phone", scopes=["read", "write"])
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def test_the_api_makes_completes_and_reopens_through_the_services(client, user):
    headers = bearer(user)

    made = post(
        client, "/api/v1/reminders", {"summary": "Chase", "due_at": soon().isoformat()}, **headers
    )
    assert made.status_code == 201 and made.json()["actor"] == "API token phone"
    pk = made.json()["id"]

    done = post(client, f"/api/v1/reminders/{pk}/complete", {}, **headers)
    assert done.json()["done_at"] and Reminder.objects.get(pk=pk).is_done

    reopened = post(client, f"/api/v1/reminders/{pk}/reopen", {}, **headers)
    assert reopened.status_code == 200 and reopened.json()["done_at"] is None
    assert not Reminder.objects.get(pk=pk).is_done


def test_the_api_change_records_the_token(client, user):
    made = create_reminder(user, summary="Chase", due=soon())

    client.patch(
        f"/api/v1/reminders/{made.pk}",
        data=json.dumps({"summary": "Ring"}),
        content_type="application/json",
        **bearer(user),
    )

    assert (stored(made).summary, stored(made).actor) == ("Ring", "API token phone")


def test_reopening_through_the_api_needs_write_and_the_owner(client, user, other_user):
    theirs = create_reminder(other_user, summary="Theirs", due=soon())
    complete_reminder(theirs)
    _token, raw = ApiToken.issue(owner=user, name="reader", scopes=["read"])

    assert (
        post(
            client,
            f"/api/v1/reminders/{theirs.pk}/reopen",
            {},
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        ).status_code
        == 403
    )
    assert post(
        client, f"/api/v1/reminders/{theirs.pk}/reopen", {}, **bearer(user)
    ).status_code == (404)
    assert stored(theirs).is_done


# ----------------------------------------------------------------------- the archive


def test_the_actor_travels_in_the_archive(user, other_user):
    create_reminder(user, summary="Chase", due=soon(), actor="API token phone")

    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    assert document["reminders"][0]["actor"] == "API token phone"
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 79

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    assert Reminder.objects.get(owner=other_user).actor == "API token phone"


# --------------------------------------------------------------------------- the guard


def test_nothing_but_the_services_makes_or_ticks_a_reminder():
    """No view, API route or other service makes, ticks or unticks a reminder by itself; the
    importer and the demo seed, which restore or invent rows, are the exceptions (#331)."""
    allowed = {
        SRC / "applications" / "services.py",
        SRC / "core" / "importer.py",
        SRC / "core" / "management" / "commands" / "seed_demo.py",
    }
    offending = []
    for path in SRC.rglob("*.py"):
        if "migrations" in path.parts or path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (r"Reminder\.objects\.create\(", r"\.complete\(\)", r"\.done_at = "):
            if re.search(pattern, text) and path != SRC / "applications" / "models.py":
                offending.append(f"{path.relative_to(SRC)}: {pattern}")
    assert not offending, offending
