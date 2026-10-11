"""The archive importer answers whether a number is held within an allowance (#890)."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from postulo.core import importer
from postulo.core.models import PhoneNumber

pytestmark = pytest.mark.django_db

HELD = ("+351912345001", "+351912345002")
FREE = ("+351912345901", "+351912345902")


@pytest.fixture(autouse=True)
def fresh_counters():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def held_by_somebody_else(other_user):
    for number in HELD:
        PhoneNumber.objects.create(
            owner=other_user, holder=other_user.profile, number=number, is_primary=False
        )


def restore(user, *numbers):
    report = importer.ImportReport()
    importer._restore_phone_numbers(
        user.profile, user, [{"number": n} for n in numbers], report, "on the profile"
    )
    return report


def test_a_held_number_is_still_skipped_and_a_free_one_restored(user, held_by_somebody_else):
    report = restore(user, HELD[0], FREE[0])

    assert [row.number for row in user.profile.phone_numbers.all()] == [FREE[0]]
    assert any("ending 01" in line and "already recorded" in line for line in report.skipped)


def test_each_held_answer_is_charged_to_the_asker(user, held_by_somebody_else, settings):
    settings.POSTULO_NUMBER_RATE = "2/h"

    restore(user, *HELD)

    from postulo.core import phone_numbers, throttle

    with pytest.raises(throttle.TooOften):
        phone_numbers.ensure_allowance(user)


def test_once_the_allowance_is_spent_a_free_and_a_held_number_are_treated_alike(
    user, held_by_somebody_else, settings
):
    settings.POSTULO_NUMBER_RATE = "2/h"
    restore(user, *HELD)  # spends it

    free = restore(user, FREE[0])
    held = restore(user, HELD[0])

    assert not user.profile.phone_numbers.exists(), "neither was restored"
    assert free.skipped and free.skipped == held.skipped
    line = free.skipped[0]
    assert "1 telephone number" in line and "lot of numbers" in line
    assert "already recorded" not in line and "ending" not in line


def test_the_archive_is_told_about_the_numbers_not_asked_as_one_line(
    user, held_by_somebody_else, settings
):
    settings.POSTULO_NUMBER_RATE = "1/h"

    report = restore(user, HELD[0], FREE[0], FREE[1])

    assert len(report.skipped) == 2
    assert "ending 01" in report.skipped[0]
    assert "2 telephone numbers" in report.skipped[1]
