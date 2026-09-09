"""A verified state on a telephone number, and the rule that only a verified one counts (#142).

Numbers have been storable since #90, unique across the instance, first-come-first-served and
unverified — which was fine while nothing depended on them. The moment one becomes a way back
into an account, every number typed before that day is a claim nobody checked, and somebody who
typed a stranger's number a month ago is pre-positioned.

Four things follow, and each has tests here.

**No existing row may be promoted.** The migration adds a column and nothing else. Verification
starts empty and is earned, whatever churn that causes.

**Only the account's own numbers are candidates.** `PhoneNumber` has a generic holder, so a
recruiter's switchboard is a number this account *owns* and never a number this account *is*.

**A proof goes stale.** People give up numbers and carriers reissue them; an address is forever
in a way a number is not.

**An archive cannot carry a claim of verification in.** It is a claim another instance made,
and this one never checked it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from postulo.core import phone_numbers
from postulo.core.models import PhoneNumber

pytestmark = pytest.mark.django_db

LASTS = PhoneNumber.VERIFICATION_LASTS


def number_for(holder, owner, digits="+351912345678", **extra) -> PhoneNumber:
    return PhoneNumber.objects.create(owner=owner, holder=holder, number=digits, **extra)


# ------------------------------------------------------------- earned, never granted


def test_a_new_number_is_not_verified(user):
    row = number_for(user.profile, user)

    assert row.verified_at is None
    assert row.is_verified is False


def test_the_migration_promotes_nothing():
    """The absence of a data migration is the point, so it is asserted rather than assumed."""
    import importlib

    module = importlib.import_module("postulo.core.migrations.0012_phone_number_verified")
    kinds = [type(operation).__name__ for operation in module.Migration.operations]

    assert kinds == ["AddField"], "anything else here would grant somebody a way in"


def test_verifying_is_something_that_happens_to_a_number(user):
    row = number_for(user.profile, user)

    row.record_verified()

    row.refresh_from_db()
    assert row.is_verified and row.verified_at is not None


def test_a_form_cannot_reach_the_field():
    """It is not on any form, because nothing a person types may assert it."""
    from postulo.core.phone_numbers import PhoneNumberForm

    assert "verified_at" not in PhoneNumberForm().fields


# -------------------------------------------------------------------- going stale


def test_a_proof_lasts_a_while(user):
    row = number_for(user.profile, user)
    PhoneNumber.objects.filter(pk=row.pk).update(
        verified_at=timezone.now() - LASTS + timedelta(days=1)
    )

    assert PhoneNumber.objects.get(pk=row.pk).is_verified


def test_a_proof_older_than_that_stops_counting(user):
    """A number a carrier reissued is somebody else's handset answering an old proof."""
    row = number_for(user.profile, user)
    PhoneNumber.objects.filter(pk=row.pk).update(
        verified_at=timezone.now() - LASTS - timedelta(days=1)
    )

    stale = PhoneNumber.objects.get(pk=row.pk)
    assert not stale.is_verified
    assert stale.verification_has_lapsed, "and lapsed is a different state from never"


def test_never_verified_is_not_lapsed(user):
    assert not number_for(user.profile, user).verification_has_lapsed


# ------------------------------------------------- changing the digits is a new claim


def test_editing_the_number_forgets_the_proof(user):
    row = number_for(user.profile, user)
    row.record_verified()

    row.number = "+351912345679"
    row.save()

    row.refresh_from_db()
    assert row.verified_at is None, "a proof of the old number says nothing about this one"


def test_saving_without_changing_it_keeps_the_proof(user):
    row = number_for(user.profile, user)
    row.record_verified()

    row.kind = PhoneNumber.Kind.MOBILE
    row.save()

    row.refresh_from_db()
    assert row.is_verified


def test_spacing_it_differently_is_the_same_number(user):
    """The digits decide, which is `phones.py`'s rule and this follows it."""
    row = number_for(user.profile, user)
    row.record_verified()

    row.number = "+351 912 345 678"
    row.save()

    row.refresh_from_db()
    assert row.is_verified


def test_an_update_fields_save_still_forgets_it(user):
    """The narrow save is the one that would otherwise slip past."""
    row = number_for(user.profile, user)
    row.record_verified()

    row.number = "+351912345679"
    row.save(update_fields=["number"])

    row.refresh_from_db()
    assert row.verified_at is None


# ---------------------------------------------------------- only your own numbers


def test_only_numbers_on_your_own_profile_are_yours(user, django_user_model):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Somewhere")
    contact = Contact.objects.create(owner=user, company=company, name="A recruiter")
    number_for(user.profile, user, "+351912345678")
    number_for(contact, user, "+351912345679")

    mine = list(phone_numbers.mine(user))

    assert [row.number for row in mine] == ["+351912345678"]


def test_a_recruiters_switchboard_is_never_a_way_into_your_account(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Somewhere")
    contact = Contact.objects.create(owner=user, company=company, name="A recruiter")
    theirs = number_for(contact, user, "+351912345679")
    theirs.record_verified()

    assert phone_numbers.recovery_candidates(user) == []
    assert not phone_numbers.can_get_back_in(user)


def test_a_verified_number_of_your_own_is_a_candidate(user):
    row = number_for(user.profile, user)
    row.record_verified()

    assert [found.pk for found in phone_numbers.recovery_candidates(user)] == [row.pk]


def test_no_instance_has_one_today(user):
    """Nothing can send to a number yet (#143), so nothing is ever verified. Correct, not TODO."""
    number_for(user.profile, user)

    assert not phone_numbers.can_get_back_in(user)


def test_a_lapsed_proof_is_not_a_candidate(user):
    row = number_for(user.profile, user)
    PhoneNumber.objects.filter(pk=row.pk).update(
        verified_at=timezone.now() - LASTS - timedelta(days=1)
    )

    assert phone_numbers.recovery_candidates(user) == []


# ------------------------------------------------------------- the enumeration bound


@override_settings(POSTULO_NUMBER_RATE="2/h")
def test_the_honest_sentence_is_given_while_there_is_allowance(user):
    first = phone_numbers.collision_message(user)

    assert "already recorded" in first


@override_settings(POSTULO_NUMBER_RATE="2/h")
def test_asking_it_repeatedly_closes_the_door(user):
    """Twenty honest answers is a person; five hundred is a list of registered numbers."""
    phone_numbers.collision_message(user)
    phone_numbers.collision_message(user)

    third = phone_numbers.collision_message(user)

    assert "available again" in third
    assert "belong to somebody else" not in third, "it stops handing out the informative half"


@override_settings(POSTULO_NUMBER_RATE="1/h")
def test_one_persons_sweep_does_not_spend_anybody_elses(user, django_user_model):
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )
    phone_numbers.collision_message(user)

    assert "already recorded" in phone_numbers.collision_message(other)


@override_settings(POSTULO_NUMBER_RATE="")
def test_an_operator_can_switch_the_limit_off(user):
    for _ in range(50):
        assert "already recorded" in phone_numbers.collision_message(user)


@override_settings(POSTULO_NUMBER_RATE="1/h")
def test_only_the_informative_answer_is_charged_for(user):
    """Somebody recording their own numbers must never meet this.

    The allowance is spent only where the answer says something, so a number nobody else
    holds costs nothing however many are saved.
    """
    from postulo.core.phone_numbers import save_only_number, taken_elsewhere

    for digits in ("+351912345670", "+351912345671", "+351912345672"):
        assert not taken_elsewhere(digits)
        save_only_number(user.profile, user, digits)

    assert "belong to somebody else" in phone_numbers.collision_message(user), (
        "the allowance was never touched"
    )


# --------------------------------------------------------------- the archive


def test_the_export_carries_the_verification_date(user):
    from postulo.core.export import build_document

    row = number_for(user.profile, user)
    row.record_verified()

    document = build_document(user)

    numbers = document["account"]["profile"]["phone_numbers"]
    assert numbers[0]["verified_at"], "the person's own record of it travels with them"


def test_an_import_lands_numbers_unverified(user, django_user_model):
    """An archive is a claim, and a claim of verification is one this instance never checked."""
    from postulo.core.importer import _restore_phone_numbers

    _restore_phone_numbers(
        user.profile,
        user,
        [{"number": "+351912345678", "is_primary": True, "verified_at": "2020-01-01T00:00:00Z"}],
    )

    landed = PhoneNumber.objects.get(number="+351912345678")
    assert landed.verified_at is None
    assert not landed.is_verified


def test_the_format_version_moved(user):
    from postulo.core import export

    assert export.FORMAT_VERSION == 7
