"""Companies, departments, contacts and the person's card on the plugin surface (#328).

The behaviour behind the names: who may write, what is refused, what a switch changes and
what a sync can rely on. Every call is made through `postulo.plugins.api`, as a plugin would.
"""

from __future__ import annotations

import json

import pytest

from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.jobs.models import Company, Contact, Department, RecordChange, Share
from postulo.plugins import api
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db


def switch_off(person, plugin) -> None:
    PluginPolicy.objects.update_or_create(
        plugin=plugin, person=person, defaults={"state": PluginPolicy.State.FORCED_OFF}
    )


def share(company, grantee, scope, owner) -> None:
    Share.objects.create(company=company, grantee_user=grantee, scope=scope, granted_by=owner)


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


# ------------------------------------------------------------------------ the company


def test_a_plugin_updates_a_company_and_the_trail_says_who(user, company):
    result = api.update_company(
        company,
        actor=user,
        via="dav",
        name="Aperture Science",
        website="https://aperture.example",
        careers_url="https://aperture.example/jobs",
        notes="Cake",
        kind=api.CompanyKind.EMPLOYMENT_SERVICE,
    )

    company.refresh_from_db()
    assert result.pk == company.pk
    assert (company.name, company.website, company.kind) == (
        "Aperture Science",
        "https://aperture.example",
        "employment_service",
    )
    assert company.note_for(user) == "Cake"
    line = RecordChange.objects.get(object_id=company.pk)
    assert line.via == "dav" and line.actor == user
    assert "name" in line.fields and "notes" not in line.fields


def test_a_name_another_company_has_is_refused_and_never_merged(user, company):
    Company.objects.create(owner=user, name="Black Mesa")

    with pytest.raises(api.WriteRefused):
        api.update_company(company, actor=user, name="black  MESA")

    company.refresh_from_db()
    assert company.name == "Aperture"
    assert Company.objects.filter(owner=user).count() == 2


def test_another_owners_record_is_refused(user, other_user, company):
    stranger = Company.objects.create(owner=other_user, name="Stranger")

    with pytest.raises(api.WriteRefused):
        api.update_company(stranger, actor=user, website="https://x.example")
    with pytest.raises(api.WriteRefused):
        api.create_contact(stranger, actor=user, name="Eve")
    with pytest.raises(api.WriteRefused):
        api.get_or_create_department(stranger, "Legal", actor=user)
    with pytest.raises(api.WriteRefused):
        api.save_web_links(stranger, [], actor=user)

    stranger.refresh_from_db()
    assert stranger.website == ""
    assert not Contact.objects.filter(owner=other_user).exists()


def test_a_write_share_may_change_the_company_and_a_read_share_may_not(user, other_user, company):
    share(company, other_user, Share.Scope.READ, user)
    with pytest.raises(api.WriteRefused):
        api.update_company(company, actor=other_user, website="https://x.example")

    Share.objects.all().delete()
    share(company, other_user, Share.Scope.WRITE, user)
    api.update_company(company, actor=other_user, website="https://x.example")
    contact = api.create_contact(company, actor=other_user, name="Chell", notes="Mine")

    company.refresh_from_db()
    assert company.website == "https://x.example"
    assert contact.owner == other_user, "a contact added at a shared company stays theirs (#955)"
    assert contact.note_for(other_user) == "Mine"


def test_a_parent_is_set_checked_and_refused_while_structure_is_off(user, company):
    from postulo.plugins.employer_structure import EMPLOYER_STRUCTURE

    group = Company.objects.create(owner=user, name="Cave Johnson Holdings")

    api.update_company(company, actor=user, parent=group)
    assert Company.objects.get(pk=company.pk).parent == group
    with pytest.raises(api.WriteRefused):
        api.update_company(group, actor=user, parent=company)

    switch_off(user, EMPLOYER_STRUCTURE)
    with pytest.raises(api.WriteRefused):
        api.update_company(company, actor=user, parent=None)
    assert Company.objects.get(pk=company.pk).parent == group, "hidden and kept"
    # Reads work either way, and the rest of the company can still be changed.
    api.update_company(company, actor=user, website="https://aperture.example")
    assert Company.objects.for_user(user).filter(pk=company.pk).exists()


# ---------------------------------------------------------------- departments, contacts


def test_a_department_is_found_by_name_or_made(user, company):
    made = api.get_or_create_department(company, "Legal", actor=user)
    again = api.get_or_create_department(company, "  legal ", actor=user)

    assert again.pk == made.pk and made.owner == user


def test_structure_off_refuses_a_new_department_and_a_contact_in_one_but_reads(user, company):
    from postulo.plugins.employer_structure import EMPLOYER_STRUCTURE

    team = Department.objects.create(owner=user, company=company, name="Legal")
    switch_off(user, EMPLOYER_STRUCTURE)

    assert api.get_or_create_department(company, "Legal", actor=user).pk == team.pk
    with pytest.raises(api.WriteRefused):
        api.get_or_create_department(company, "Finance", actor=user)
    with pytest.raises(api.WriteRefused):
        api.create_contact(company, actor=user, name="Wheatley", department=team)
    plain = api.create_contact(company, actor=user, name="Wheatley")
    with pytest.raises(api.WriteRefused):
        api.update_contact(plain, actor=user, department=team)

    assert not Department.objects.filter(name="Finance").exists()
    assert Contact.objects.get(pk=plain.pk).department is None


def test_a_department_of_another_company_is_refused(user, company):
    other = Company.objects.create(owner=user, name="Black Mesa")
    elsewhere = api.get_or_create_department(other, "Research", actor=user)

    with pytest.raises(api.WriteRefused):
        api.create_contact(company, actor=user, name="Gordon", department=elsewhere)
    mine = api.create_contact(company, actor=user, name="Gordon")
    with pytest.raises(api.WriteRefused):
        api.update_contact(mine, actor=user, department=elsewhere)

    assert not Contact.objects.filter(department=elsewhere).exists()


def test_a_contact_is_created_and_updated_with_a_trail(user, company):
    team = api.get_or_create_department(company, "Legal", actor=user)

    contact = api.create_contact(
        company, actor=user, via="dav", name="Glados", role="Host", department=team
    )
    api.update_contact(contact, actor=user, via="dav", role="Overseer", email="g@aperture.example")

    contact.refresh_from_db()
    assert (contact.role, contact.email, contact.department) == (
        "Overseer",
        "g@aperture.example",
        team,
    )
    actions = list(RecordChange.objects.filter(object_id=contact.pk).values_list("action", "via"))
    assert ("created", "dav") in actions and ("edited", "dav") in actions


def test_a_contact_needs_a_name_and_a_real_address(user, company):
    with pytest.raises(api.WriteRefused):
        api.create_contact(company, actor=user, name="  ")
    with pytest.raises(api.WriteRefused):
        api.create_contact(company, actor=user, name="Eve", email="not an address")
    assert not Contact.objects.exists()


# ----------------------------------------------------------------------------- the rows


def test_numbers_are_made_these_keeping_the_row_that_stays(user, company):
    contact = api.create_contact(company, actor=user, name="Glados")
    api.save_phone_numbers(
        contact,
        [api.PhoneRow("+351912345678", "mobile"), api.PhoneRow("+351912345679", "work")],
        actor=user,
    )
    kept = contact.phone_numbers.get(number="+351912345678")

    saved = api.save_phone_numbers(
        contact,
        [
            api.PhoneRow("+351 912 345 678", "work", is_primary=True),
            {"number": "+351912345680", "kind": "home"},
        ],
        actor=user,
    )

    assert not saved.refused
    assert contact.phone_numbers.get(pk=kept.pk).kind == "work", "the row that stays is kept"
    assert contact.phone_numbers.count() == 2
    assert not contact.phone_numbers.filter(number="+351912345679").exists()
    assert contact.phone_numbers.get(is_primary=True).pk == kept.pk
    assert len(saved.rows) == 2 and saved.rows[0].is_primary


def test_a_number_the_rules_refuse_comes_back_with_the_reason(user, company):
    contact = api.create_contact(company, actor=user, name="Glados")

    saved = api.save_phone_numbers(
        contact,
        [
            api.PhoneRow("+351912345678"),
            api.PhoneRow("+351 912 345 678"),
            api.PhoneRow("12"),
            api.PhoneRow("+351912345679", kind="nonsense"),
        ],
        actor=user,
    )

    assert [row.number for row in saved.rows] == ["+351912345678"]
    assert len(saved.refused) == 3
    assert all(isinstance(refusal.reason, str) and refusal.reason for refusal in saved.refused)
    assert saved.refused[0].row.number == "+351 912 345 678"


def test_nothing_on_the_surface_writes_a_user_or_a_profile(user):
    """The person's own details are read only: a profile is refused as a holder, and the
    account and profile are as they were after every call (#328)."""
    before = (user.email, user.first_name, user.profile.headline, user.profile.updated_at)

    for call in (
        lambda: api.save_phone_numbers(user.profile, [api.PhoneRow("+351912345678")], actor=user),
        lambda: api.save_postal_addresses(
            user.profile, [api.AddressRow(municipality="Lisboa")], actor=user
        ),
        lambda: api.save_web_links(
            user.profile, [api.LinkRow("https://a.example", "website")], actor=user
        ),
        lambda: api.update_company(user.profile, actor=user, name="x"),
        lambda: api.update_contact(user, actor=user, name="x"),
    ):
        with pytest.raises(api.WriteRefused):
            call()

    user.refresh_from_db()
    user.profile.refresh_from_db()
    assert before == (user.email, user.first_name, user.profile.headline, user.profile.updated_at)
    assert not PhoneNumber.objects.exists() and not PostalAddress.objects.exists()
    assert not WebLink.objects.exists()


def test_with_several_numbers_off_only_the_primary_is_written(user, company):
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    contact = api.create_contact(company, actor=user, name="Glados")
    api.save_phone_numbers(
        contact,
        [api.PhoneRow("+351912345678", is_primary=True), api.PhoneRow("+351912345679")],
        actor=user,
    )
    switch_off(user, PHONE_NUMBERS)

    saved = api.save_phone_numbers(
        contact,
        [api.PhoneRow("+351912345680"), api.PhoneRow("+351912345681")],
        actor=user,
    )

    assert saved.held_back == 1
    assert [row.number for row in saved.rows] == ["+351912345680"]
    assert contact.phone_numbers.filter(number="+351912345679").exists(), "hidden row untouched"
    assert contact.phone_numbers.count() == 2
    assert [row.number for row in api.numbers_for(contact, user)] == ["+351912345680"]


def test_addresses_are_made_these_with_one_primary(user, company):
    api.save_postal_addresses(
        company,
        [
            api.AddressRow(municipality="Lisboa", country="pt", is_primary=True),
            api.AddressRow(municipality="Porto", country="PT"),
        ],
        actor=user,
    )
    porto = company.postal_addresses.get(municipality="Porto")

    saved = api.save_postal_addresses(
        company,
        [
            api.AddressRow(municipality="Porto", country="PT", kind="work", is_primary=True),
            api.AddressRow(),
            api.AddressRow(municipality="Porto", country="PT"),
        ],
        actor=user,
    )

    assert company.postal_addresses.count() == 1
    kept = company.postal_addresses.get()
    assert kept.pk == porto.pk and kept.is_primary and kept.kind == "work"
    assert len(saved.refused) == 2


def test_links_are_made_these_with_a_primary_per_kind(user, company):
    api.save_web_links(
        company,
        [
            api.LinkRow("https://www.linkedin.com/company/aperture", "social"),
            api.LinkRow("https://mastodon.example/@aperture", "social", is_primary=True),
        ],
        actor=user,
    )

    saved = api.save_web_links(
        company,
        [
            api.LinkRow("https://mastodon.example/@aperture", "social"),
            api.LinkRow("javascript:alert(1)", "social"),
            api.LinkRow("https://aperture.example", "website"),
            api.LinkRow("https://mastodon.example/@aperture", "social"),
        ],
        actor=user,
    )

    assert [link.url for link in company.web_links.all()] == ["https://mastodon.example/@aperture"]
    assert company.web_links.get().is_primary
    reasons = {refusal.row.url: refusal.reason for refusal in saved.refused}
    assert "javascript:alert(1)" in reasons and "https://aperture.example" in reasons
    assert len(saved.refused) == 3


def test_with_a_link_kind_off_only_its_primary_is_written(user):
    from postulo.plugins.social_profiles import SOCIAL_PROFILES

    contact = Contact.objects.create(owner=user, name="Glados")
    WebLink.objects.create(
        owner=user, holder=contact, kind="social", url="https://a.example/hidden", is_primary=False
    )
    WebLink.objects.create(
        owner=user, holder=contact, kind="social", url="https://a.example/main", is_primary=True
    )
    switch_off(user, SOCIAL_PROFILES)

    saved = api.save_web_links(
        contact,
        [
            api.LinkRow("https://a.example/new", "social"),
            api.LinkRow("https://a.example/x", "social"),
        ],
        actor=user,
    )

    urls = set(contact.web_links.values_list("url", flat=True))
    assert urls == {"https://a.example/hidden", "https://a.example/new"}, "main edited in place"
    assert contact.web_links.get(is_primary=True).url == "https://a.example/new"
    assert saved.held_back == 1


def test_rows_of_a_read_share_are_refused(user, other_user, company):
    share(company, other_user, Share.Scope.READ, user)
    with pytest.raises(api.WriteRefused):
        api.save_postal_addresses(
            company, [api.AddressRow(municipality="Lisboa")], actor=other_user
        )
    assert not company.postal_addresses.exists()


# ---------------------------------------------------------------------- the person's card


def test_the_persons_card_is_read_only_and_carries_the_sign_in_address(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    profile = user.profile
    profile.headline = "Engineer"
    profile.save()
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351912345678", is_primary=True)
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351912345679")
    PostalAddress.objects.create(
        owner=user, holder=profile, municipality="Lisboa", country="PT", is_primary=True
    )
    WebLink.objects.create(
        owner=user, holder=profile, kind="website", url="https://alex.example", is_primary=True
    )

    card = api.person_card(user)

    assert (card.given_name, card.family_name) == ("Alex", "Morgan")
    assert card.email == user.email == "applicant@example.org"
    assert card.headline == "Engineer" and card.location == "Lisboa, Portugal"
    assert card.picture == ""
    assert [row.number for row in card.numbers] == ["+351912345678", "+351912345679"]
    assert card.addresses[0].municipality == "Lisboa"
    assert card.links[0].url == "https://alex.example"
    with pytest.raises(AttributeError):
        card.email = "other@example.org"


def test_the_persons_card_follows_the_switch_for_several_numbers(user):
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    PhoneNumber.objects.create(
        owner=user, holder=user.profile, number="+351912345678", is_primary=True
    )
    PhoneNumber.objects.create(owner=user, holder=user.profile, number="+351912345679")
    switch_off(user, PHONE_NUMBERS)

    assert [row.number for row in api.person_card(user).numbers] == ["+351912345678"]


# --------------------------------------------------------------------------------- the API


def bearer(user, *scopes):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes)
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def add_contact(client, company, headers, **payload):
    return client.post(
        f"/api/v1/companies/{company.pk}/contacts",
        data=json.dumps({"name": "Caroline", **payload}),
        content_type="application/json",
        **headers,
    )


def test_the_api_adds_a_contact_through_the_service_and_writes_its_trail(client, user, company):
    response = add_contact(client, company, bearer(user, "read", "write"), role="Assistant")

    assert response.status_code == 201
    contact = Contact.objects.get(owner=user, name="Caroline")
    assert contact.company == company and contact.role == "Assistant"
    assert RecordChange.objects.filter(object_id=contact.pk, action="created", via="Agent").exists()


def test_the_api_needs_write_access_to_the_company_to_add_a_contact(
    client, user, other_user, company
):
    headers = bearer(other_user, "read", "write")
    assert add_contact(client, company, headers).status_code == 404, "a stranger's company"

    share(company, other_user, Share.Scope.READ, user)
    assert add_contact(client, company, headers).status_code == 404, "read-only is not enough"
    assert not Contact.objects.exists()

    Share.objects.all().delete()
    share(company, other_user, Share.Scope.WRITE, user)
    assert add_contact(client, company, headers).status_code == 201
    assert Contact.objects.get().owner == other_user
