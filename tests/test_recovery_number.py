"""The account's primary number as a way back in — and why it is not the primary (#144).

The intrinsic link the request asked for was already there: a `PhoneNumber` has an `owner`
and a `holder`, and the account's own numbers are the ones whose holder is their profile.
What was missing was everything that makes such a number trustworthy, which #142 and #143
built. This joins them up, and makes four decisions while doing it.

**A separate flag, not `is_primary`.** The primary is what a document prints — a recruiter
dials it. Tying the way back into an account to the number on a CV means somebody who changes
what a recruiter dials silently changes how they prove they are themselves.

**Only a confirmed number of your own.** A recruiter's switchboard is a number this account
recorded, never one it is; a number nobody answered on is a claim rather than a channel.

**The plugin cannot take it away.** Recovery is instance policy and a feature plugin is a
per-person preference, so an administrator switching *several telephone numbers* off for
somebody must not quietly remove their way back in.

**Nobody is nominated by default**, and on this release nobody can be: nothing confirms a
number yet, so the control does not appear.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from postulo.core import phone_numbers
from postulo.core.models import PhoneNumber
from postulo.plugins import base, registry
from postulo.plugins.base import FieldSpec
from postulo.plugins.base import TestResult as PluginTestResult

pytestmark = pytest.mark.django_db


class Gateway:
    """Somebody else's text transport, so a number can be confirmable in a test."""

    name = "gateway"
    version = "1.0.0"
    kind = "transport"
    label = "Gateway"
    description = "Sends text messages."
    medium = base.TEXT

    def config_fields(self) -> list[FieldSpec]:
        return []

    def test(self, config: dict) -> PluginTestResult:
        return PluginTestResult(True, "fine")

    def deliver(self, messages: list, config: dict) -> int:
        return len(messages)


@contextlib.contextmanager
def a_gateway():
    registry.register_builtin("transport", Gateway)
    registry.plugins("transport", refresh=True)
    try:
        yield
    finally:
        registry.unregister_builtin("transport", Gateway)
        registry.plugins("transport", refresh=True)


def confirmed(user, digits="+351912345678", **extra) -> PhoneNumber:
    row = PhoneNumber.objects.create(owner=user, holder=user.profile, number=digits, **extra)
    row.record_verified()
    return row


# ------------------------------------------------------- kept apart from the primary


def test_the_primary_is_not_automatically_the_way_back_in(user):
    """Changing what a recruiter dials must not change how somebody proves who they are."""
    row = confirmed(user)
    phone_numbers.set_primary(row)

    row.refresh_from_db()
    assert row.is_primary and not row.is_recovery
    assert phone_numbers.recovery_number(user) is None


def test_they_can_be_two_different_numbers(user):
    published = confirmed(user, "+351912345670")
    private = confirmed(user, "+351912345671")
    phone_numbers.set_primary(published)
    phone_numbers.set_recovery(private, owner=user)

    assert phone_numbers.recovery_number(user).pk == private.pk
    assert phone_numbers.primary_for(user.profile).pk == published.pk


def test_changing_the_primary_does_not_touch_the_way_back_in(user):
    published = confirmed(user, "+351912345670")
    private = confirmed(user, "+351912345671")
    phone_numbers.set_recovery(private, owner=user)

    phone_numbers.set_primary(published)

    assert phone_numbers.recovery_number(user).pk == private.pk


def test_only_one_per_account(user):
    first = confirmed(user, "+351912345670")
    second = confirmed(user, "+351912345671")
    phone_numbers.set_recovery(first, owner=user)

    phone_numbers.set_recovery(second, owner=user)

    assert PhoneNumber.objects.filter(owner=user, is_recovery=True).count() == 1
    assert phone_numbers.recovery_number(user).pk == second.pk


# ------------------------------------------------------------ what it refuses


def test_a_contacts_number_is_refused(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Somewhere")
    contact = Contact.objects.create(owner=user, company=company, name="A recruiter")
    theirs = PhoneNumber.objects.create(owner=user, holder=contact, number="+351912345679")
    theirs.record_verified()
    theirs.is_recovery = True

    with pytest.raises(ValidationError) as raised:
        theirs.clean()

    assert "your own numbers" in str(raised.value)


def test_an_unconfirmed_number_is_refused(user):
    row = PhoneNumber.objects.create(owner=user, holder=user.profile, number="+351912345678")
    row.is_recovery = True

    with pytest.raises(ValidationError) as raised:
        row.clean()

    assert "confirmed" in str(raised.value)


def test_a_lapsed_confirmation_stops_it_counting(user):
    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)
    PhoneNumber.objects.filter(pk=row.pk).update(
        verified_at=timezone.now() - PhoneNumber.VERIFICATION_LASTS - timedelta(days=1)
    )

    assert phone_numbers.recovery_number(user) is None


def test_editing_the_digits_takes_the_route_away(user):
    """A route to a number nobody has answered on is not a route."""
    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)

    row.number = "+351912345679"
    row.save()

    row.refresh_from_db()
    assert not row.is_recovery and row.verified_at is None


def test_a_narrow_save_still_takes_it_away(user):
    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)

    row.number = "+351912345679"
    row.save(update_fields=["number"])

    row.refresh_from_db()
    assert not row.is_recovery


# --------------------------------------------- the plugin cannot take it away


def forced_off_for(person) -> None:
    """An administrator switching *several telephone numbers* off for one account."""
    from postulo.core.features import PHONE_NUMBERS
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=person, state=PluginPolicy.State.FORCED_OFF
    )


def test_switching_the_feature_off_leaves_the_route_alone(user):
    """Recovery is instance policy; the plugin is a per-person preference."""
    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)
    forced_off_for(user)

    assert not phone_numbers.several_allowed(user), "the feature really is off"
    assert phone_numbers.recovery_number(user).pk == row.pk


def test_switching_it_off_still_hides_the_other_numbers(user):
    """What the plugin governs is unchanged: what is shown and used, never who can get in."""
    primary = confirmed(user, "+351912345670")
    phone_numbers.set_primary(primary)
    confirmed(user, "+351912345671")
    forced_off_for(user)

    assert len(phone_numbers.numbers_for(user.profile, user)) == 1


# ------------------------------------------------------------ the route itself


def test_a_nominated_number_covers_that_account(user):
    from postulo.notifications import transport

    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)

    with a_gateway():
        assert phone_numbers.accounts_without_a_recovery_number() == 0
        assert "text" in transport.recovery_routes()


def test_a_confirmed_number_nobody_nominated_covers_nothing(user):
    from postulo.notifications import transport

    confirmed(user)

    with a_gateway():
        assert phone_numbers.accounts_without_a_recovery_number() == 1
        assert "text" not in transport.recovery_routes()


def test_the_stranded_count_grows_a_clause_rather_than_an_assumption(user):
    """An account with a route is not an account that needs email — but only with a gateway."""
    from postulo.notifications import transport

    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)

    assert transport.accounts_needing_email() == 1, "no gateway, so the number reaches nobody"

    with a_gateway():
        assert transport.accounts_needing_email() == 0


def test_somebody_with_no_number_is_unaffected(user, django_user_model):
    from postulo.notifications import transport

    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)
    django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )

    with a_gateway():
        assert transport.accounts_needing_email() == 1
        assert transport.refuse_switching_off("smtp"), "and the lock stays shut for them"


# ------------------------------------------------------------------- the page


def test_the_choice_is_not_offered_without_a_gateway(client, user):
    """A control nobody can use, beside a promise nobody can keep, is worse than none."""
    confirmed(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-recovery-choice" not in html
    assert not phone_numbers.can_be_chosen_here()


def test_it_appears_the_day_a_gateway_is_installed(client, user):
    confirmed(user)
    client.force_login(user)

    with a_gateway():
        html = client.get(reverse("accounts:profile")).content.decode()

        assert "data-recovery-choice" in html
        assert phone_numbers.can_be_chosen_here()


# --------------------------------------------------------------- the archive


def test_an_import_never_lands_a_way_back_in(user):
    """An archive is a claim, and this one would nominate a route nobody here confirmed."""
    from postulo.core.importer import _restore_phone_numbers

    _restore_phone_numbers(
        user.profile,
        user,
        [
            {
                "number": "+351912345678",
                "is_primary": True,
                "verified_at": "2020-01-01T00:00:00Z",
                "is_recovery": True,
            }
        ],
    )

    landed = PhoneNumber.objects.get(number="+351912345678")
    assert not landed.is_recovery and landed.verified_at is None


def test_the_export_carries_the_persons_own_record_of_it(user):
    from postulo.core.export import build_document

    row = confirmed(user)
    phone_numbers.set_recovery(row, owner=user)

    numbers = build_document(user)["account"]["profile"]["phone_numbers"]

    assert numbers[0]["is_recovery"] is True
