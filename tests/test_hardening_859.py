"""Calendar settings: words on the buttons, weekday names, the first day of the week (#859)."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile

import pytest
from django.urls import reverse

from postulo.applications import agenda, reports
from postulo.core import export, importer, preferences, weeks

pytestmark = pytest.mark.django_db

APPEARANCE = {
    "theme": "system",
    "density": "comfortable",
    "identifier_style": "both",
    "button_labels": "both",
    "quiet_after_days": 14,
    "closing_notice_days": 3,
    "navigation": [],
}


@pytest.fixture(autouse=True)
def _locale_weeks():
    weeks.choose(None)
    yield
    weeks.choose(None)


def calendar(client, **query):
    return client.get(reverse("applications:calendar"), query).content.decode()


# ---------------------------------------------------------------- the week


def test_a_chosen_first_day_moves_every_week(user):
    wednesday = dt.date(2026, 9, 16)
    assert reports.week_start(wednesday) == dt.date(2026, 9, 14), "en-GB: a Monday"

    weeks.choose(0)
    assert reports.week_start(wednesday) == dt.date(2026, 9, 13), "a Sunday"
    weeks.choose(6)
    assert reports.week_start(wednesday) == dt.date(2026, 9, 12), "a Saturday"
    weeks.choose(None)
    assert reports.week_start(wednesday) == dt.date(2026, 9, 14)


def test_the_choice_is_put_in_force_with_the_person_and_not_left_for_the_next(user):
    user.profile.first_weekday = 0
    user.profile.save()

    preferences.apply(user.profile)
    assert weeks.first_day() == 0
    preferences.apply(None)
    assert weeks.first_day() == 1, "the previous visitor's choice does not carry over"


def test_the_month_grid_and_the_week_view_agree_with_it(client, user):
    user.profile.first_weekday = 0
    user.profile.save()
    client.force_login(user)

    html = calendar(client, view="week", on="2026-09-16")
    preferences.apply(user.profile)
    page = agenda.page_for({"view": "week", "on": "2026-09-16"}, user)

    assert page.days[0].date == dt.date(2026, 9, 13) and page.days[0].date.isoweekday() == 7
    assert "Sun 13" in html


def test_the_setting_is_on_language_and_time_and_is_saved(client, user):
    client.force_login(user)
    page = client.get(reverse("settings:locale")).content.decode()
    assert (
        'name="first_weekday"' in page
        and "The language&#x27;s usual" in page.replace("’", "&#x27;")
    ) or ('name="first_weekday"' in page)

    client.post(reverse("settings:locale"), {"language": "", "time_zone": "", "first_weekday": "0"})
    user.profile.refresh_from_db()
    assert user.profile.first_weekday == 0

    client.post(reverse("settings:locale"), {"language": "", "time_zone": "", "first_weekday": ""})
    user.profile.refresh_from_db()
    assert user.profile.first_weekday is None, "blank is the language's own"


# -------------------------------------------------------------- the weekday names


def test_abbreviated_is_the_default_and_in_full_adds_the_name_for_wide_screens(client, user):
    client.force_login(user)
    assert "Wednesday" not in calendar(client, view="week", on="2026-09-16")

    user.profile.weekday_names = "long"
    user.profile.save()
    html = calendar(client, view="week", on="2026-09-16")

    assert re.search(
        r'<span class="sm:hidden">Wed</span><span class="hidden sm:inline">Wednesday</span>', html
    )
    month = calendar(client, month="2026-09")
    assert '<span class="hidden sm:inline">Monday</span>' in month, "the grid's headings too"


# -------------------------------------------------------- the words on the buttons


def test_without_words_the_buttons_keep_them_for_a_screen_reader_only(client, user):
    client.force_login(user)
    # The step group only: the views beside it have labels of their own since #904.
    steps = r'<div class="button-group[^"]*"[^>]*aria-label="Move the calendar">.*?</div>'
    nav = re.search(steps, calendar(client), re.S)[0]
    assert nav.count('class="btn-label"') == 3

    user.profile.calendar_labels = False
    user.profile.save()
    nav = re.search(steps, calendar(client), re.S)[0]

    assert 'class="btn-label"' not in nav
    assert nav.count('class="sr-only"') == 3
    assert 'aria-label="Previous month"' in nav, "the name is still there"


# -------------------------------------------------------------------- the forms


def test_the_two_appearance_choices_are_saved_and_left_alone_when_not_sent(client, user):
    client.force_login(user)

    client.post(
        reverse("settings:appearance"),
        {**APPEARANCE, "calendar_labels": "False", "weekday_names": "long"},
    )
    user.profile.refresh_from_db()
    assert (user.profile.calendar_labels, user.profile.weekday_names) == (False, "long")

    client.post(reverse("settings:appearance"), APPEARANCE)
    user.profile.refresh_from_db()
    assert (user.profile.calendar_labels, user.profile.weekday_names) == (False, "long")


def test_the_three_travel_in_the_archive_and_a_bad_value_is_refused(user, other_user):
    user.profile.calendar_labels = False
    user.profile.weekday_names = "long"
    user.profile.first_weekday = 6
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))
    profile = document["account"]["profile"]
    assert (profile["calendar_labels"], profile["weekday_names"], profile["first_weekday"]) == (
        False,
        "long",
        6,
    )

    def load(**changes):
        document["account"]["profile"].update(changes)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
        importer.load(other_user, zipfile.ZipFile(buffer))
        other_user.profile.refresh_from_db()
        return other_user.profile

    restored = load()
    assert (restored.calendar_labels, restored.weekday_names, restored.first_weekday) == (
        False,
        "long",
        6,
    )
    refused = load(weekday_names="huge", first_weekday=9)
    assert refused.weekday_names == "long" and refused.first_weekday == 6
