"""Dated entries a plugin places on the calendar, and ends (#887)."""

from __future__ import annotations

import datetime as dt
import json
import zipfile

import pytest
from django.utils import timezone

from postulo.applications import agenda
from postulo.applications.models import Reminder
from postulo.applications.services import add_reminder, retire_reminder, retire_reminders_of
from postulo.core import export, importer
from postulo.notifications.management.commands.send_due_reminders import announce_due_reminders
from postulo.plugins import api
from postulo.plugins.signals import plugin_removed

pytestmark = pytest.mark.django_db


def soon(days=3):
    return timezone.now() + dt.timedelta(days=days)


def past():
    return timezone.now() - dt.timedelta(minutes=5)


def on_the_day_of(made, user):
    day = f"{timezone.localdate(made.due_at):%Y-%m-%d}"
    return agenda.page_for({"view": "day", "on": day}, user).events


def test_the_surface_offers_the_two_calls():
    assert api.add_reminder is add_reminder and api.retire_reminder is retire_reminder


def test_an_entry_is_placed_with_its_source_and_note(user):
    made = add_reminder(
        user,
        summary="Monthly declaration",
        due_at=soon(),
        uid="ft:declaration:2026-11",
        source="postulo-francetravail",
        note="Before the 15th",
    )

    assert (made.owner, made.application, made.source) == (user, None, "postulo-francetravail")
    assert made.note == "Before the 15th" and made.done_at is None


def test_a_second_call_with_the_same_uid_updates_in_place_and_never_unticks(user):
    first = add_reminder(user, summary="Declare", due_at=soon(3), uid="u1", source="p")
    first.complete()
    done_at = Reminder.objects.get(pk=first.pk).done_at

    again = add_reminder(
        user, summary="Declare (moved)", due_at=soon(5), uid="u1", source="p", note="Later"
    )

    assert again.pk == first.pk and Reminder.objects.filter(owner=user).count() == 1
    again.refresh_from_db()
    assert (again.summary, again.note) == ("Declare (moved)", "Later")
    assert again.due_at > first.due_at
    assert again.done_at == done_at, "what the person ticked stays ticked"


def test_moving_an_entry_later_lets_it_be_announced_again(user):
    made = add_reminder(user, summary="Declare", due_at=soon(1), uid="u1", source="p")
    Reminder.objects.filter(pk=made.pk).update(notified_at=timezone.now())

    add_reminder(user, summary="Declare", due_at=soon(6), uid="u1", source="p")

    assert Reminder.objects.get(pk=made.pk).notified_at is None


def test_a_plugin_cannot_overwrite_what_a_person_made_nor_the_reverse(user):
    mine = Reminder.objects.create(owner=user, summary="Mine", due_at=soon(), uid="same")
    with pytest.raises(ValueError):
        add_reminder(user, summary="Theirs", due_at=soon(), uid="same", source="p")
    assert Reminder.objects.get(pk=mine.pk).summary == "Mine"

    add_reminder(user, summary="Placed", due_at=soon(), uid="other", source="p")
    with pytest.raises(ValueError):
        add_reminder(user, summary="Imported", due_at=soon(), uid="other", source="")


def test_it_belongs_to_the_owner_and_to_nobody_elses(user, other_user):
    add_reminder(user, summary="Declare", due_at=soon(), uid="u1", source="p")
    add_reminder(other_user, summary="Declare", due_at=soon(), uid="u1", source="p")

    assert Reminder.objects.filter(uid="u1").count() == 2
    assert retire_reminder(other_user, "u1") is True
    assert Reminder.objects.get(owner=user, uid="u1").retired_at is None


def test_retiring_ends_a_plugins_entry_and_placing_it_again_brings_it_back(user):
    made = add_reminder(user, summary="Declare", due_at=soon(), uid="u1", source="p")

    assert [event.title for event in on_the_day_of(made, user)] == ["Declare"]
    assert retire_reminder(user, "u1") is True
    assert retire_reminder(user, "u1") is False, "already ended"
    assert on_the_day_of(made, user) == []
    assert not Reminder.objects.outstanding().filter(pk=made.pk).exists()

    add_reminder(user, summary="Declare", due_at=soon(), uid="u1", source="p")
    assert Reminder.objects.get(pk=made.pk).retired_at is None


def test_a_persons_own_reminder_cannot_be_retired_by_a_plugin(user):
    Reminder.objects.create(owner=user, summary="Mine", due_at=soon(), uid="mine")

    assert retire_reminder(user, "mine") is False
    assert Reminder.objects.get(uid="mine").retired_at is None


def test_the_calendar_says_which_plugin_placed_an_entry(user):
    made = add_reminder(
        user,
        summary="Declare",
        due_at=soon(),
        uid="u1",
        source="postulo-francetravail",
        note="the 15th",
    )

    [event] = on_the_day_of(made, user)

    assert "postulo-francetravail" in event.detail and "the 15th" in event.detail


def test_removing_a_plugin_retires_what_it_placed_for_everybody(user, other_user):
    add_reminder(user, summary="A", due_at=soon(), uid="a", source="gone")
    add_reminder(other_user, summary="B", due_at=soon(), uid="b", source="gone")
    add_reminder(user, summary="C", due_at=soon(), uid="c", source="staying")

    plugin_removed.send(sender=None, name="gone")

    assert Reminder.objects.filter(source="gone", retired_at__isnull=False).count() == 2
    assert Reminder.objects.get(source="staying").retired_at is None
    assert retire_reminders_of("gone") == 0


def test_an_entry_that_has_fallen_due_is_announced_and_a_retired_one_is_not(user):
    add_reminder(user, summary="Declare", due_at=past(), uid="u1", source="p", note="the 15th")
    add_reminder(user, summary="Ended", due_at=past(), uid="u2", source="p")
    retire_reminder(user, "u2")

    stamped, _delivered = announce_due_reminders()

    assert stamped == 1
    assert Reminder.objects.get(uid="u1").notified_at is not None
    assert Reminder.objects.get(uid="u2").notified_at is None


def test_the_archive_carries_source_note_and_retirement(user, other_user):
    add_reminder(user, summary="Declare", due_at=soon(), uid="u1", source="p", note="the 15th")
    retire_reminder(user, "u1")
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    [written] = document["reminders"]
    assert (written["source"], written["note"]) == ("p", "the 15th") and written["retired_at"]

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    restored = Reminder.objects.get(owner=other_user)
    assert (restored.source, restored.note) == ("p", "the 15th") and restored.retired_at
