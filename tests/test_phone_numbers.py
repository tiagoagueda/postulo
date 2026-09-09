"""Several telephone numbers per holder, and what switching the feature off does not do.

The interesting assertions here are the negative ones. A plugin that governs a table is new
in Postulo, and the promise the whole plugin system makes -- *switching one off deletes
nothing* -- has to be true of it as literally as it is of a notifier holding somebody's
credentials. So the tests that matter most are the ones that count rows after a switch.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.core import phone_numbers
from postulo.core.models import PhoneNumber
from postulo.plugins.phone_numbers import PHONE_NUMBERS

pytestmark = pytest.mark.django_db


@pytest.fixture
def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def add(holder, owner, value, *, kind="", primary=False):
    return PhoneNumber.objects.create(
        owner=owner, holder=holder, number=value, kind=kind, is_primary=primary
    )


def switch_off(person):
    person.profile.plugins_off = [PHONE_NUMBERS]
    person.profile.save(update_fields=["plugins_off"])


# ------------------------------------------------------------------ the invariant


def test_a_holder_may_have_several_numbers_of_different_kinds(contact, user):
    add(contact, user, "+351912345678", kind=PhoneNumber.Kind.MOBILE, primary=True)
    add(contact, user, "+351211111111", kind=PhoneNumber.Kind.SWITCHBOARD)

    kinds = [row.kind for row in contact.phone_numbers.all()]
    assert kinds == ["mobile", "switchboard"], "the primary first, then the rest"


def test_only_one_of_them_can_be_the_primary(contact, user):
    add(contact, user, "+351912345678", primary=True)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            add(contact, user, "+351211111111", primary=True)


def test_making_one_primary_takes_it_off_the_other(contact, user):
    first = add(contact, user, "+351912345678", primary=True)
    second = add(contact, user, "+351211111111")

    phone_numbers.set_primary(second)

    first.refresh_from_db()
    second.refresh_from_db()
    assert not first.is_primary and second.is_primary


def test_the_number_is_kept_in_the_form_that_can_be_compared(contact, user):
    row = add(contact, user, "+351 912 345 678")
    assert row.normalised == "+351912345678", "written by save, never typed"


def test_a_number_nobody_could_parse_is_kept_and_compared_with_nothing(contact, user):
    """`phones.py` means it: refusing to store an unparseable number is the worst outcome."""
    row = add(contact, user, "ask reception")
    assert row.number == "ask reception"
    assert row.normalised == "", "so it takes part in no comparison at all"


# ------------------------------------------------------- unique across the instance


def test_two_accounts_cannot_both_hold_one_number(contact, user, other_user):
    from postulo.jobs.models import Company, Contact

    add(contact, user, "+351912345678", primary=True)
    theirs = Contact.objects.create(
        owner=other_user,
        company=Company.objects.create(owner=other_user, name="Black Mesa"),
        name="Gordon",
    )

    assert phone_numbers.taken_elsewhere("+351912345678")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            add(theirs, other_user, "+351 912 345 678", primary=True)


def test_the_same_number_written_three_ways_is_one_number(contact, user):
    add(contact, user, "+351912345678", primary=True)
    for spelling in ("+351 912 345 678", "00351912345678", "+351912345678"):
        assert phone_numbers.taken_elsewhere(spelling), spelling


def test_two_unparseable_numbers_do_not_collide(contact, user):
    add(contact, user, "ask reception", primary=True)
    add(contact, user, "ask reception")
    assert contact.phone_numbers.count() == 2, "incomparable is not the same as equal"


def test_the_form_says_what_the_rule_discloses(client, user, contact, other_user):
    """The message names the disclosure rather than hiding it, which is the honest half.

    Instance-wide uniqueness cannot be enforced without telling whoever typed a number
    that somebody else may hold it. A vaguer message would disclose exactly as much and
    leave the person guessing at what had happened.
    """
    from postulo.jobs.models import Company, Contact

    theirs = Contact.objects.create(
        owner=other_user,
        company=Company.objects.create(owner=other_user, name="Black Mesa"),
        name="Gordon",
    )
    add(theirs, other_user, "+351912345678", primary=True)
    switch_off(user)
    client.force_login(user)

    response = client.post(
        reverse("jobs:contact_update", args=[contact.pk]),
        {
            "name": "Cave Johnson",
            "role": "",
            "company": contact.company_id,
            "email": "",
            "linkedin_url": "",
            "notes": "",
            "phone_0": "PT",
            "phone_1": "+351912345678",
        },
    )

    assert response.status_code == 200, "the form came back rather than saving"
    assert "already recorded on this instance" in response.content.decode()
    assert not contact.phone_numbers.exists()


# ---------------------------------------------------- what switching it off does not do


def test_switching_it_off_shows_the_primary_and_keeps_the_rest(contact, user):
    add(contact, user, "+351912345678", primary=True)
    add(contact, user, "+351211111111")
    add(contact, user, "+351933333333")

    assert len(phone_numbers.numbers_for(contact, user)) == 3

    switch_off(user)

    shown = phone_numbers.numbers_for(contact, user)
    assert [row.number for row in shown] == ["+351912345678"], "the primary, and only it"
    assert contact.phone_numbers.count() == 3, "switching a plugin off deletes nothing"
    assert phone_numbers.kept_back(contact, user) == 2, "and the person is told so"


def test_switching_it_back_on_finds_them_unchanged(contact, user):
    add(contact, user, "+351912345678", primary=True)
    add(contact, user, "+351211111111", kind=PhoneNumber.Kind.SWITCHBOARD)

    switch_off(user)
    user.profile.plugins_off = []
    user.profile.save(update_fields=["plugins_off"])

    rows = phone_numbers.numbers_for(contact, user)
    assert [(row.number, row.kind) for row in rows] == [
        ("+351912345678", ""),
        ("+351211111111", "switchboard"),
    ]


def test_the_page_says_the_hidden_ones_are_still_there(client, user, contact):
    """A person who cannot see four numbers and is told nothing assumes they were deleted."""
    add(contact, user, "+351912345678", primary=True)
    add(contact, user, "+351211111111")
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()
    assert "kept for this contact and not shown" in html


def test_saving_the_one_box_leaves_the_hidden_numbers_alone(client, user, contact):
    """The rows this person cannot see are not theirs to lose by saving a form."""
    add(contact, user, "+351912345678", primary=True)
    hidden = add(contact, user, "+351211111111")
    switch_off(user)
    client.force_login(user)

    client.post(
        reverse("jobs:contact_update", args=[contact.pk]),
        {
            "name": "Cave Johnson",
            "role": "Founder",
            "company": contact.company_id,
            "email": "",
            "linkedin_url": "",
            "notes": "",
            "phone_0": "PT",
            "phone_1": "+351999999999",
        },
    )

    hidden.refresh_from_db()
    assert hidden.number == "+351211111111", "untouched"
    assert phone_numbers.primary_for(contact).number == "+351999999999", "the primary moved"


# ------------------------------------------------------------------- everything else


def test_deleting_the_holder_takes_its_numbers_with_it(contact, user):
    add(contact, user, "+351912345678", primary=True)
    add(contact, user, "+351211111111")
    contact.delete()
    assert not PhoneNumber.objects.exists(), "a generic relation is what gives the cascade"


def test_a_document_prints_the_primary_and_not_a_list(user):
    from postulo.documents.rendering import contact_details

    profile = user.profile
    add(profile, user, "+351912345678", primary=True)
    add(profile, user, "+351211111111")

    assert contact_details(user)["phone"] == "+351912345678"


def test_an_export_carries_every_number_even_the_hidden_ones(user, contact):
    """An export is what somebody leaves with, so it carries what is recorded."""
    from postulo.core import export as export_module

    add(contact, user, "+351912345678", primary=True)
    add(contact, user, "+351211111111", kind=PhoneNumber.Kind.SWITCHBOARD)
    switch_off(user)

    document = export_module.build_document(user)
    exported = document["companies"][0]["contacts"][0]["phone_numbers"]
    assert [row["number"] for row in exported] == ["+351912345678", "+351211111111"]
    assert [row["is_primary"] for row in exported] == [True, False]


def test_an_older_archive_still_carries_its_one_number_in(user):
    from postulo.core.importer import _phone_rows

    rows = _phone_rows({"name": "Cave Johnson", "phone": "+351912345678"})
    assert rows == [{"number": "+351912345678", "is_primary": True}]


def test_the_feature_is_a_plugin_an_administrator_can_see(client, django_user_model):
    from postulo.plugins import registry

    names = {getattr(plugin, "name", "") for plugin in registry.plugins("feature")}
    assert PHONE_NUMBERS in names

    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "Several telephone numbers" in html


def test_one_persons_numbers_are_never_another_persons(user, other_user, contact):
    add(contact, user, "+351912345678", primary=True)
    assert not PhoneNumber.objects.for_user(other_user).exists()
    assert PhoneNumber.objects.for_user(user).count() == 1
