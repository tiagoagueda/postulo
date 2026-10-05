"""Messaging handles, several per person and per contact, each checked against its service (#682).

Matrix, XMPP, Signal, Telegram, Threema or another service: a handle, on a service chosen in
the row. The row is the telephone row's shape and not its promises: unique per holder and
service, not across the instance; never verified; never a way back in. Switching the feature
off offers none and deletes nothing.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.core import messaging_handles
from postulo.core.models import MessagingHandle
from postulo.plugins.messaging_contacts import MESSAGING_CONTACTS

pytestmark = pytest.mark.django_db

PROFILE_POST = {"first_name": "Alex", "last_name": "Morgan", "headline": "", "location": ""}


@pytest.fixture
def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def add(holder, owner, handle, *, service="matrix", label="", primary=False):
    return MessagingHandle.objects.create(
        owner=owner, holder=holder, service=service, label=label, handle=handle, is_primary=primary
    )


def switch_off(person):
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin=MESSAGING_CONTACTS, person=person, state=PluginPolicy.State.FORCED_OFF
    )


def rows(*entries, primary=None, prefix="messaging"):
    """The POST the block sends, management form and all."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(entries)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    if primary is not None:
        data[f"{prefix}-primary"] = f"{prefix}-{primary}"
    for index, entry in enumerate(entries):
        for name, value in entry.items():
            data[f"{prefix}-{index}-{name}"] = value
    return data


def contact_post(contact, **extra):
    return {
        "name": contact.name,
        "role": "",
        "company": contact.company_id,
        "email": "",
        "notes": "",
        **extra,
    }


# ------------------------------------------------------------------ the invariants


def test_a_holder_may_have_several_handles_one_of_them_primary(contact, user):
    add(contact, user, "@a:example.org")
    add(contact, user, "alex@example.org", service="xmpp")
    add(contact, user, "Alex_M.42", service="signal", primary=True)

    listed = [row.handle for row in contact.messaging_handles.all()]

    assert listed[0] == "Alex_M.42", "the primary first"
    assert len(listed) == 3


def test_only_one_handle_per_holder_can_be_the_primary(contact, user):
    add(contact, user, "@a:example.org", primary=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        add(contact, user, "alex@example.org", service="xmpp", primary=True)


def test_making_one_primary_takes_it_off_the_other(contact, user):
    first = add(contact, user, "@a:example.org", primary=True)
    second = add(contact, user, "@b:example.org")

    messaging_handles.set_primary(second)

    first.refresh_from_db()
    second.refresh_from_db()
    assert not first.is_primary and second.is_primary


def test_a_handle_is_listed_once_per_holder_and_service_in_whatever_case(contact, user):
    add(contact, user, "@Alex:example.org")
    with pytest.raises(IntegrityError), transaction.atomic():
        add(contact, user, "@alex:EXAMPLE.org")
    # Another service is another handle, even when the text is the same.
    add(contact, user, "alexmorgan", service="telegram")
    add(contact, user, "alexmorgan", service="", label="Our chat")


def test_under_other_two_networks_may_share_a_handle_and_one_may_not_be_listed_twice(contact, user):
    add(contact, user, "alex", service="", label="Our IRC")
    add(contact, user, "alex", service="", label="Their IRC")
    with pytest.raises(IntegrityError), transaction.atomic():
        add(contact, user, "ALEX", service="", label="our irc")


def test_the_same_handle_is_freely_held_by_two_holders_and_two_accounts(contact, user, other_user):
    """Not unique across the instance: refusing a second holder would say that somebody
    else here has the same handle, which is what a telephone number's rule discloses and
    this one deliberately does not."""
    from postulo.jobs.models import Company, Contact

    theirs = Contact.objects.create(
        owner=other_user, company=Company.objects.create(owner=other_user, name="B"), name="X"
    )
    add(user.profile, user, "@alex:example.org", primary=True)
    add(contact, user, "@alex:example.org", primary=True)
    add(theirs, other_user, "@alex:example.org", primary=True)
    add(other_user.profile, other_user, "@alex:example.org", primary=True)

    assert MessagingHandle.objects.filter(handle="@alex:example.org").count() == 4


def test_a_handle_is_never_verified_and_never_a_way_back_in():
    names = {field.name for field in MessagingHandle._meta.get_fields()}

    assert not {"verified_at", "is_recovery", "is_verified"} & names


def test_deleting_the_holder_deletes_its_handles(contact, user):
    add(contact, user, "@a:example.org")
    add(user.profile, user, "@b:example.org")

    contact.delete()

    assert [row.handle for row in MessagingHandle.objects.all()] == ["@b:example.org"]


def test_saving_normalises_the_stored_form_and_writes_the_folded_one(contact, user):
    row = add(contact, user, "  @Alex:Example.org ")

    assert row.handle == "@Alex:Example.org", "stored as given, less the edges"
    assert row.comparable == "@alex:example.org"
    row.is_primary = True
    row.save(update_fields=["is_primary"])
    row.refresh_from_db()
    assert row.comparable == "@alex:example.org"


# ----------------------------------------------------------------- the rows on a page


def test_the_block_is_on_your_details_after_the_telephone_numbers(client, user):
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert 'id="section-messaging"' in html
    assert html.index('id="section-phones"') < html.index('id="section-messaging"')
    assert "A telephone number that is also on a messaging app belongs under" in html


def test_the_block_is_on_the_contact_form(client, user, contact):
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()

    assert 'id="section-messaging"' in html


def test_the_profile_page_saves_handles_each_checked_against_its_service(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            **rows(
                {"service": "matrix", "label": "", "handle": "@Alex:Example.org"},
                {"service": "telegram", "label": "", "handle": "@alex_morgan"},
                {"service": "other", "label": "Our IRC", "handle": "alex on #room"},
                primary=1,
            ),
        },
    )

    assert response.status_code == 302, response.content.decode()[:500]
    saved = list(user.profile.messaging_handles.all())
    by_handle = {row.handle: row for row in saved}
    assert set(by_handle) == {"@alex:example.org", "alex_morgan", "alex on #room"}
    assert by_handle["alex_morgan"].is_primary
    assert sum(row.is_primary for row in saved) == 1
    assert all(row.owner == user for row in saved)


def test_a_handle_that_is_not_one_on_its_service_is_refused_beside_the_handle(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {**PROFILE_POST, **rows({"service": "threema", "label": "", "handle": "ABCD123"})},
    )

    assert response.status_code == 200
    assert "does not look like a handle on Threema" in response.content.decode()
    assert not user.profile.messaging_handles.exists()


def test_other_has_to_be_named_on_the_page(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {**PROFILE_POST, **rows({"service": "other", "label": "", "handle": "somebody"})},
    )

    assert response.status_code == 200
    assert "Name the service this handle is on" in response.content.decode()
    assert not user.profile.messaging_handles.exists()


def test_a_handle_listed_twice_in_a_block_is_refused(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            **rows(
                {"service": "matrix", "label": "", "handle": "@a:example.org"},
                {"service": "matrix", "label": "", "handle": "@A:example.org"},
            ),
        },
    )

    assert response.status_code == 200
    assert "This handle is already listed." in response.content.decode()
    assert not user.profile.messaging_handles.exists()


def test_a_page_posted_without_the_block_changes_none_of_the_rows(client, user):
    add(user.profile, user, "@a:example.org", primary=True)
    client.force_login(user)

    response = client.post(reverse("accounts:profile"), PROFILE_POST)

    assert response.status_code == 302
    assert user.profile.messaging_handles.count() == 1


def test_a_stored_row_saved_as_it_was_is_not_checked_again(client, user, monkeypatch):
    """Nothing already stored is refused in retrospect: a service's pattern may change."""
    row = add(user.profile, user, "not a matrix id at all", primary=True)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "not a matrix id at all" in html

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            "messaging-TOTAL_FORMS": "1",
            "messaging-INITIAL_FORMS": "1",
            "messaging-MIN_NUM_FORMS": "0",
            "messaging-MAX_NUM_FORMS": "1000",
            "messaging-0-id": str(row.pk),
            "messaging-0-service": "matrix",
            "messaging-0-label": "",
            "messaging-0-handle": "not a matrix id at all",
            "messaging-primary": "messaging-0",
        },
    )

    assert response.status_code == 302, response.content.decode()[:500]
    row.refresh_from_db()
    assert row.handle == "not a matrix id at all"


def test_a_contact_is_saved_with_its_handles_and_a_new_one_has_its_owner(client, user, contact):
    client.force_login(user)

    response = client.post(
        reverse("jobs:contact_update", args=[contact.pk]),
        contact_post(
            contact,
            **rows({"service": "xmpp", "label": "", "handle": "Cave@Aperture.example/desk"}),
        ),
    )

    assert response.status_code == 302, response.content.decode()[:500]
    [row] = contact.messaging_handles.all()
    assert (row.service, row.handle, row.owner, row.is_primary) == (
        "xmpp",
        "cave@aperture.example",
        user,
        True,
    )


def test_a_contact_can_be_created_with_its_handles_in_one_go(client, user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    response = client.post(
        reverse("jobs:contact_create"),
        {
            "name": "Caroline",
            "role": "",
            "company": company.pk,
            "email": "",
            "notes": "",
            **rows({"service": "signal", "label": "", "handle": "caroline.42"}),
        },
    )

    assert response.status_code == 302, response.content.decode()[:500]
    made = Contact.objects.get(owner=user, name="Caroline")
    assert made.messaging_handles.get().handle == "caroline.42"


def test_the_company_page_lists_a_contacts_handles_as_text_and_never_a_link(client, user, contact):
    add(contact, user, "@cave:aperture.example", primary=True)
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[contact.company_id])).content.decode()

    assert "@cave:aperture.example" in html
    assert "matrix.to" not in html and 'href="matrix:' not in html


# -------------------------------------------------------------- the feature, switched off


def test_the_feature_is_on_by_default():
    assert MESSAGING_CONTACTS == "messaging-contacts"
    from postulo.plugins import registry

    assert registry.find_any(MESSAGING_CONTACTS) is not None


def test_switched_off_the_block_is_not_offered_and_every_row_is_kept(client, user, contact):
    add(user.profile, user, "@a:example.org", primary=True)
    add(contact, user, "@b:example.org", primary=True)
    switch_off(user)
    client.force_login(user)

    profile = client.get(reverse("accounts:profile")).content.decode()
    form = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()
    company = client.get(reverse("jobs:company_detail", args=[contact.company_id])).content.decode()

    assert 'id="section-messaging"' not in profile
    assert 'id="section-messaging"' not in form
    assert "@b:example.org" not in company
    assert MessagingHandle.objects.count() == 2, "off deletes nothing"
    assert messaging_handles.handles_for(user.profile, user) == []
    assert messaging_handles.kept_back(user.profile, user) == 1


def test_a_page_saved_while_the_feature_is_off_leaves_the_rows_alone(client, user):
    add(user.profile, user, "@a:example.org", primary=True)
    switch_off(user)
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {**PROFILE_POST, **rows({"service": "matrix", "label": "", "handle": "@z:example.org"})},
    )

    assert response.status_code == 302
    assert [row.handle for row in user.profile.messaging_handles.all()] == ["@a:example.org"]


def test_switching_it_back_on_brings_every_row_back_untouched(client, user):
    row = add(user.profile, user, "@a:example.org", primary=True)
    from postulo.plugins.models import PluginPolicy

    policy = PluginPolicy.objects.create(
        plugin=MESSAGING_CONTACTS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    policy.delete()
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "@a:example.org" in html
    row.refresh_from_db()
    assert row.is_primary


# ------------------------------------------------------------------ the primary handed on


def test_taking_the_primary_off_hands_it_to_the_next(user):
    first = add(user.profile, user, "@a:example.org", primary=True)
    second = add(user.profile, user, "@b:example.org")

    first.delete()
    handed = messaging_handles.ensure_one_primary(user.profile)

    second.refresh_from_db()
    assert handed == second and second.is_primary


# ----------------------------------------------------------------- taken in without a page


def test_rows_handed_over_by_a_client_are_checked_and_the_first_primary_wins(contact, user):
    checked = messaging_handles.checked_rows(
        [
            {"handle": "@A:example.org", "service": "matrix", "is_primary": True},
            {"handle": "alex.42", "service": "signal"},
            {"handle": "chat", "label": "Elsewhere"},
        ]
    )

    made = messaging_handles.add_handles(contact, user, checked)

    assert [(row.service, row.label, row.handle) for row in made] == [
        ("matrix", "", "@a:example.org"),
        ("signal", "", "alex.42"),
        ("", "Elsewhere", "chat"),
    ]
    assert [row.is_primary for row in made] == [True, False, False]


def test_a_row_that_fails_the_check_names_its_handle_and_nothing_is_written(contact, user):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refused:
        messaging_handles.checked_rows(
            [
                {"handle": "@a:example.org", "service": "matrix"},
                {"handle": "no", "service": "signal"},
            ]
        )

    assert "no: " in refused.value.messages[0]
    assert not contact.messaging_handles.exists()


def test_a_list_with_no_end_is_refused(contact, user):
    from django.core.exceptions import ValidationError

    rows_ = [{"handle": f"h{i}", "label": "x"} for i in range(messaging_handles.MAX_PER_HOLDER + 1)]
    with pytest.raises(ValidationError):
        messaging_handles.checked_rows(rows_)


def test_the_same_handle_is_not_made_twice(contact, user):
    add(contact, user, "@a:example.org", primary=True)

    made = messaging_handles.add_handles(
        contact,
        user,
        messaging_handles.checked_rows([{"handle": "@A:example.org", "service": "matrix"}]),
    )

    assert made == []
    assert contact.messaging_handles.count() == 1


def test_a_file_never_has_a_handle_refused_over_its_service():
    """A file is a claim: what this instance cannot check is kept as *Other*."""
    one = messaging_handles.read_from_a_file
    assert one({"service": "matrix", "handle": "@a:example.org"})["service"] == "matrix"
    assert one({"service": "unknown-net", "handle": "x", "label": ""}) == {
        "service": "",
        "label": "unknown-net",
        "handle": "x",
        "is_primary": False,
    }
    assert one({"service": "matrix", "handle": "not matrix"})["service"] == ""
    assert one({"service": "", "handle": "x", "label": "Mine", "is_primary": True}) == {
        "service": "",
        "label": "Mine",
        "handle": "x",
        "is_primary": True,
    }
    assert one({"handle": ""}) is None and one("a string") is None
