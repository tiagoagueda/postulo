"""One contract for every kind of contact detail (#146).

Three kinds exist at three different stages, each having invented its own answer: an address
that allauth validates and confirms, a number checked less than syntactically on purpose and
confirmed by nothing, and a postal address that is neither. Nothing could ask them the same
question, and the question is about to matter — a number that becomes a way back into an
account (#144) has to be one somebody proved they hold.

What is being tested here is mostly the *shape of the answers*, because that is what the
issue asks for. Three things in particular.

**Validated-but-not-confirmable has to be expressible**, or the postal channel looks
permanently unfinished rather than finished and honest.

**Two empty carriers are not the same empty.** A channel that will never have one and a
channel that has not been given one yet are different facts, and #144 has to be able to say
which it met.

**A number that fails the check is still kept.** `phones.py` means what it says: refusing to
save an unparseable number would be the worst possible outcome, because a number dictated
badly is still the only number anybody has. This contract reports; refusing is the caller's.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from postulo.core import channels
from postulo.core.channels import (
    CODE,
    LINK,
    REAL,
    SYNTACTIC,
    UNPROVABLE,
    Checked,
    ContactChannel,
    EmailChannel,
    TelephoneChannel,
)

pytestmark = pytest.mark.django_db


class Postal:
    """The kind that can be validated and never proved. Stands in until #147 builds it.

    Written here rather than shipped, because a channel for a model Postulo does not have
    yet would be speculation. What it proves is that the contract has room for the answer.
    """

    name = "postal"
    label = "Postal address"
    depth = SYNTACTIC
    proof = UNPROVABLE

    def check(self, value: str) -> Checked:
        return Checked(bool(value.strip()), SYNTACTIC)

    def carrier(self) -> str:
        return ""


# ---------------------------------------------------------------- the contract itself


def test_the_three_that_exist_are_registered():
    names = [channel.name for channel in channels.all_channels()]

    assert names == ["email", "telephone"]


def test_every_registered_channel_satisfies_the_protocol():
    for channel in channels.all_channels():
        assert isinstance(channel, ContactChannel)
        assert channel.depth in channels.DEPTHS
        assert channel.proof in channels.PROOFS


def test_no_channel_claims_to_check_that_somebody_answered():
    """`real` is what confirming establishes. A channel claiming it would be lying."""
    for channel in channels.all_channels():
        assert channel.depth != REAL


def test_a_channel_can_be_looked_up_by_name():
    assert channels.find("email") is not None
    assert channels.find("carrier-pigeon") is None


def test_a_check_reads_as_a_boolean():
    """So a caller can write `if channel.check(value)` without unpacking it first."""
    assert bool(Checked(True, SYNTACTIC)) is True
    assert not Checked(False, SYNTACTIC, "no")


# ------------------------------------------------------ validated but not confirmable


def test_a_channel_that_can_never_be_proved_is_a_legitimate_answer():
    postal = Postal()

    assert postal.check("12 Somewhere Street").ok, "validated"
    assert not channels.confirmable(postal), "and never provable, which is not a gap"


def test_the_two_kinds_of_empty_carrier_are_told_apart():
    """A channel that will never have one, and one that has not been given one yet."""
    postal, telephone = Postal(), TelephoneChannel()

    assert postal.carrier() == "" and telephone.carrier() == ""
    assert postal.proof == UNPROVABLE, "nothing will ever carry this"
    assert telephone.proof == CODE, "something could; #143 has not built it"


# ----------------------------------------------------------------------- email


def test_an_address_is_checked_for_its_shape():
    channel = EmailChannel()

    assert channel.check("somebody@example.org").ok
    assert not channel.check("somebody-at-example.org").ok
    assert channel.check("nope").message, "and says why"


def test_an_address_is_proved_by_a_link():
    assert EmailChannel().proof == LINK


def test_the_carrier_is_whatever_is_carrying_the_mail():
    """Named rather than assumed to be SMTP: an instance may run a transport plugin."""
    assert EmailChannel().carrier() == "smtp"
    assert channels.confirmable(EmailChannel())


def test_an_instance_with_no_transport_cannot_confirm_an_address(monkeypatch):
    from postulo.notifications import transport

    monkeypatch.setattr(transport, "selected", lambda: None)

    assert EmailChannel().carrier() == ""
    assert not channels.confirmable(EmailChannel())


# ------------------------------------------------------------------- telephone


def test_a_number_in_international_form_passes():
    assert TelephoneChannel().check("+351 912 345 678").ok


def test_a_number_written_the_way_it_is_dialled_at_home_does_not():
    """The whole reason `phones.py` exists: 06 12 34 56 78 is unreachable from anywhere."""
    result = TelephoneChannel().check("06 12 34 56 78")

    assert not result.ok
    assert "country" in result.message


def test_a_country_code_with_no_number_after_it_is_caught():
    """The shape is right and there is nothing to dial: the case a length check is for."""
    result = TelephoneChannel().check("+351")

    assert not result.ok
    assert "digits" in result.message


def test_a_code_no_country_uses_is_caught():
    result = TelephoneChannel().check("+9991234567")

    assert not result.ok
    assert "country" in result.message


def test_spacing_is_not_a_number_and_never_was():
    """`+35 1234 5678` is +351 2345678: the digits decide, which is `phones.py`'s job."""
    assert TelephoneChannel().check("+35 1234 5678").ok


def test_more_than_e164_allows_is_caught():
    assert not TelephoneChannel().check("+351" + "9" * 15).ok


def test_the_check_stops_at_the_shape_and_says_so():
    """The depth matters as much as the answer: #142 has to know how far this went."""
    result = TelephoneChannel().check("+351912345678")

    assert result.depth == SYNTACTIC
    assert TelephoneChannel().depth == SYNTACTIC, "never plausible; the library is refused"


def test_a_number_that_fails_the_check_is_still_savable(user):
    """Reporting is not refusing. A badly dictated number is still the only one anybody has."""
    from postulo.core.models import PhoneNumber

    assert not TelephoneChannel().check("06 12 34 56 78").ok

    kept = PhoneNumber.objects.create(owner=user, holder=user.profile, number="06 12 34 56 78")

    assert kept.pk and kept.number == "06 12 34 56 78"
    assert kept.normalised == "", "outside every comparison, exactly as before"


def test_a_number_cannot_be_confirmed_on_this_instance_yet():
    """Not because numbers are unprovable — because nothing can reach one (#143)."""
    assert not channels.confirmable(TelephoneChannel())


# ------------------------------------------------------------- the message and its limit


def test_a_confirmation_line_names_the_instance_and_the_code():
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Somewhere"})

    line = channels.one_line("824193")

    assert "824193" in line and "Somewhere" in line
    assert line.count("\n") == 0, "one line: there is no interface around it to carry context"


def test_the_line_says_nobody_will_ask_for_it():
    """A stranger's message containing six digits is the shape of every scam there is."""
    assert "ask you for it" in channels.one_line("824193")


@override_settings(POSTULO_CONFIRMATION_RATE="2/h")
def test_resending_is_limited_across_every_channel(user):
    from postulo.core import throttle

    channels.resend(user)
    channels.resend(user)

    with pytest.raises(throttle.TooOften):
        channels.resend(user)


@override_settings(POSTULO_CONFIRMATION_RATE="")
def test_an_empty_limit_switches_it_off(user):
    for _ in range(20):
        channels.resend(user)


@override_settings(POSTULO_CONFIRMATION_RATE="1/h")
def test_one_persons_limit_is_not_another_persons(user, django_user_model):
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="a-long-enough-password-42"
    )
    channels.resend(user)

    channels.resend(other)  # nobody else's allowance was spent


# ------------------------------------------------------------------- the register


def test_a_channel_can_be_added_and_taken_away_again():
    channels.register(Postal())
    try:
        assert channels.find("postal") is not None
    finally:
        channels.unregister("postal")

    assert channels.find("postal") is None


def test_registering_the_same_name_twice_does_not_double_up():
    """`ready()` can run more than once, and a duplicated channel would be asked twice."""
    before = len(channels.all_channels())

    channels.register_the_ones_that_exist()

    assert len(channels.all_channels()) == before
