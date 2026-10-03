"""What a Europass import tells about a telephone number is bounded like the form's (#615).

#142 bounded how often one account may learn that a number is already recorded on the
instance. The import used to skip a number that was held and write one that was not, which
is the same answer, and gave it for nothing.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings

from postulo.core.models import PhoneNumber
from postulo.resume import importing

pytestmark = pytest.mark.django_db

HELD = "+351912345678"
FREE = "+351912345679"


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


def _import(user, number):
    PhoneNumber.objects.filter(owner=user).delete()
    return importing.apply(user, importing.Record(person={"phone": number}))


@override_settings(POSTULO_NUMBER_RATE="2/h")
def test_repeated_imports_of_a_held_number_spend_the_allowance(user, other_user):
    PhoneNumber.objects.create(owner=other_user, holder=other_user.profile, number=HELD)

    first = _import(user, HELD)
    second = _import(user, HELD)
    third = _import(user, HELD)

    assert "already recorded" in first.skipped[0]
    assert "already recorded" in second.skipped[0]
    assert "available again" in third.skipped[0], "the third is past the allowance"
    assert not PhoneNumber.objects.filter(owner=user).exists()


@override_settings(POSTULO_NUMBER_RATE="1/h")
def test_once_spent_a_free_number_is_not_written_either(user, other_user):
    PhoneNumber.objects.create(owner=other_user, holder=other_user.profile, number=HELD)
    _import(user, HELD)

    report = _import(user, FREE)

    assert "available again" in report.skipped[0]
    assert "phone" not in report.profile_filled
    assert not PhoneNumber.objects.filter(owner=user).exists()


@override_settings(POSTULO_NUMBER_RATE="1/h")
def test_a_free_number_is_written_and_costs_nothing(user):
    report = _import(user, FREE)
    again = _import(user, FREE)

    assert "phone" in report.profile_filled and "phone" in again.profile_filled
    assert PhoneNumber.objects.get(owner=user).number == FREE
