"""Telephone numbers, postal addresses and social profiles behave the same for a company, a
contact and *Your details*; any difference is on purpose, written down and pinned here (#934).

The rows are one table each (`core.PhoneNumber`, `PostalAddress`, `WebLink`) held through a
generic relation, edited by one set of partials, built by one set of mixins, written to a
vCard and the API by the same helpers. The last section pins the differences that are
deliberate, so that one cannot appear or vanish unnoticed.
"""

from __future__ import annotations

import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.accounts.views import ProfileView
from postulo.api.models import ApiToken
from postulo.core import phone_numbers, postal, vcard, web_links
from postulo.core.mixins import PhoneNumbersMixin, PostalAddressesMixin
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.jobs import vcards, views
from postulo.jobs.models import Company, Contact

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Acme", website="https://acme.example")


@pytest.fixture
def contact(user, company):
    return Contact.objects.create(owner=user, company=company, name="Alex Morgan")


def number(user, holder, value, *, kind="", primary=False):
    return PhoneNumber.objects.create(
        owner=user, holder=holder, number=value, kind=kind, is_primary=primary
    )


def address(user, holder, street="Rua 1", *, primary=False):
    return PostalAddress.objects.create(
        owner=user,
        holder=holder,
        street=street,
        postcode="1000-100",
        municipality="Lisboa",
        country="PT",
        is_primary=primary,
    )


def social(user, holder, url="https://www.linkedin.com/company/acme"):
    return WebLink.objects.create(
        owner=user, holder=holder, kind=web_links.Kind.SOCIAL, url=url, is_primary=True
    )


def lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


# ------------------------------------------------------------------ one code path


def test_the_profile_builds_its_rows_through_the_same_mixins():
    assert issubclass(ProfileView, PhoneNumbersMixin)
    assert issubclass(ProfileView, PostalAddressesMixin)
    assert "get_numbers" not in vars(ProfileView)
    assert "get_addresses" not in vars(ProfileView)


def test_the_profile_page_draws_the_rows_the_mixins_build(client, user):
    number(user, user.profile, "+351912345678", primary=True)
    address(user, user.profile, primary=True)
    client.force_login(user)
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 200
    assert response.context["numbers"].prefix == "phone_numbers"
    assert response.context["addresses"].prefix == "addresses"
    assert response.context["numbers_kept_back"] == 0


# ------------------------------------------------------------- the same controls


@pytest.mark.parametrize("owner_kind", ["profile", "company", "contact"])
def test_every_owner_gets_the_star_the_bin_and_the_help_mark(
    client, user, company, contact, owner_kind
):
    holder = {"profile": user.profile, "company": company, "contact": contact}[owner_kind]
    number(user, holder, "+351912345678", primary=True)
    address(user, holder, primary=True)
    if owner_kind != "profile":
        social(user, holder)
    client.force_login(user)
    url = {
        "profile": reverse("accounts:profile"),
        "company": reverse("jobs:company_update", args=[company.pk]),
        "contact": reverse("jobs:contact_update", args=[contact.pk]),
    }[owner_kind]
    html = client.get(url).content.decode()
    assert 'class="primary-choice"' in html, owner_kind
    assert 'data-variant="destructive-ghost"' in html, owner_kind
    assert "data-help-mark" in html, owner_kind
    assert "data-remove-row" in html, "the dialogs the bins open"
    assert 'name="phone_numbers-0-DELETE"' not in html, "no Remove box beside a bin"
    assert 'name="addresses-0-DELETE"' not in html


@pytest.mark.parametrize("kind", ["company", "contact"])
def test_a_bin_removes_one_row_at_once_and_hands_the_primary_on(
    client, user, company, contact, kind
):
    holder = company if kind == "company" else contact
    first = number(user, holder, "+351912345678", primary=True)
    second = number(user, holder, "+351912345679")
    client.force_login(user)
    url = reverse(f"jobs:{kind}_remove_row", args=[holder.pk, "number", first.pk])
    response = client.post(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 200 and response.json()["removed"] is True
    assert response.json()["primary"] == second.pk and response.json()["count"] == 1
    second.refresh_from_db()
    assert second.is_primary and not PhoneNumber.objects.filter(pk=first.pk).exists()


@pytest.mark.parametrize("kind", ["company", "contact"])
def test_a_row_of_somebody_elses_holder_is_a_404(client, user, other_user, company, contact, kind):
    holder = company if kind == "company" else contact
    mine = address(user, holder)
    client.force_login(other_user)
    url = reverse(f"jobs:{kind}_remove_row", args=[holder.pk, "address", mine.pk])
    assert client.post(url).status_code == 404
    assert PostalAddress.objects.filter(pk=mine.pk).exists()
    client.force_login(user)
    nonsense = reverse(f"jobs:{kind}_remove_row", args=[holder.pk, "nonsense", mine.pk])
    assert client.post(nonsense).status_code == 404


def test_a_row_is_found_among_its_own_holders_rows_only(client, user, company, contact):
    on_contact = number(user, contact, "+351912345678", primary=True)
    client.force_login(user)
    url = reverse("jobs:company_remove_row", args=[company.pk, "number", on_contact.pk])
    assert client.post(url).status_code == 404
    assert PhoneNumber.objects.filter(pk=on_contact.pk).exists()


def test_without_scripts_the_page_is_drawn_again_at_the_block(client, user, company):
    row = number(user, company, "+351912345678", primary=True)
    client.force_login(user)
    url = reverse("jobs:company_remove_row", args=[company.pk, "number", row.pk])
    assert client.get(url).status_code == 302, "a GET removes nothing"
    assert PhoneNumber.objects.filter(pk=row.pk).exists()
    response = client.post(url)
    assert response.status_code == 302
    assert response["Location"].endswith("#section-phones")
    assert not PhoneNumber.objects.filter(pk=row.pk).exists()


# ---------------------------------------------------------------- autofill


def box_autocomplete(form, name="number") -> str:
    return form[name].field.widget.widgets[1].attrs["autocomplete"]


@pytest.mark.parametrize(
    ("owner_kind", "expected"), [("profile", "tel"), ("company", "off"), ("contact", "off")]
)
def test_a_browser_fills_a_phone_box_on_the_persons_own_rows_alone(
    user, company, contact, owner_kind, expected
):
    holder = {"profile": user.profile, "company": company, "contact": contact}[owner_kind]
    formset = phone_numbers.formset_for(holder, asked_by=user)
    assert box_autocomplete(formset.forms[0]) == expected
    assert f'autocomplete="{expected}"' in str(formset.forms[0]["number"])


@pytest.mark.parametrize(
    ("owner_kind", "expected"), [("profile", "tel"), ("company", "off"), ("contact", "off")]
)
def test_the_single_phone_box_follows_the_same_rule(
    user, company, contact, owner_kind, expected, monkeypatch
):
    from postulo.accounts.forms import ProfileForm
    from postulo.jobs.forms import CompanyForm, ContactForm

    monkeypatch.setattr(phone_numbers, "several_allowed", lambda person: False)
    if owner_kind == "profile":
        form = ProfileForm(instance=user.profile)
    elif owner_kind == "company":
        form = CompanyForm(user=user, instance=company)
    else:
        form = ContactForm(user=user, instance=contact)
    assert box_autocomplete(form, "phone") == expected


def test_postal_autofill_already_follows_the_same_rule(user, company, contact):
    for holder, expected in ((user.profile, True), (company, False), (contact, False)):
        formset = postal.formset_for(holder, person=user)
        assert formset.form_kwargs["own"] is expected


# ------------------------------------------------------------ contact cards


def test_a_contacts_card_lists_its_addresses_as_the_companys_page_does(
    client, user, company, contact
):
    address(user, company, "Company Road 1")
    address(user, contact, "Contact Street 9", primary=True)
    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "Company Road 1" in html
    assert "Contact Street 9" in html and "data-contact-address" in html


def test_another_account_sees_neither_the_contact_nor_its_address(
    client, user, other_user, company, contact
):
    address(user, contact, "Contact Street 9")
    client.force_login(other_user)
    assert client.get(reverse("jobs:company_detail", args=[company.pk])).status_code == 404


# -------------------------------------------------------------------- vCard


def a_full_company(user, company):
    company.email = "jobs@acme.example"
    company.save()
    number(user, company, "+351211234567", kind="switchboard", primary=True)
    number(user, company, "+351211234568", kind="fax")
    address(user, company, "Rua do Exemplo 1", primary=True)
    social(user, company)


def test_a_companys_card_carries_everything_it_holds(user, company):
    a_full_company(user, company)
    found = lines(vcard.card_to_text(vcards.card_for_company(Company.objects.get())))
    assert "EMAIL;TYPE=work:jobs@acme.example" in found
    assert len([line for line in found if line.startswith("TEL")]) == 2
    assert any(line.startswith("ADR") and "Rua do Exemplo 1" in line for line in found)
    assert "URL:https://acme.example" in found
    assert "URL:https://www.linkedin.com/company/acme" in found


def test_a_companys_card_is_read_back_into_a_company_with_its_rows(user, company):
    a_full_company(user, company)
    text = vcard.card_to_text(vcards.card_for_company(Company.objects.get()))
    reader = get_user_model().objects.create_user("reader@example.com", password="x")
    cards = vcard.read(text).cards
    (entry,) = vcards.review(reader, cards)
    assert "TEL" not in entry.not_kept and "ADR" not in entry.not_kept
    assert "EMAIL" not in entry.not_kept and "URL" not in entry.not_kept
    report = vcards.apply(reader, cards, {0})
    assert report.companies == 1
    made = Company.objects.get(owner=reader, name="Acme")
    assert made.website == "https://acme.example"
    assert made.email == "jobs@acme.example"
    assert sorted(n.number for n in made.phone_numbers.all()) == [
        "+351211234567",
        "+351211234568",
    ]
    assert made.phone_numbers.get(is_primary=True).kind == "switchboard"
    (where,) = made.postal_addresses.all()
    assert (where.street, where.municipality, where.country) == ("Rua do Exemplo 1", "Lisboa", "PT")
    (link,) = made.web_links.all()
    assert link.kind == web_links.Kind.SOCIAL and "linkedin.com/company/acme" in link.url


def test_a_second_website_on_a_companys_card_is_the_one_thing_left_out(user):
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:Globex\nURL:https://globex.example\n"
        "URL:https://other.example\nEND:VCARD\n"
    )
    (entry,) = vcards.review(user, vcard.read(text).cards)
    assert "URL" in entry.not_kept
    assert "TEL" not in entry.not_kept and "ADR" not in entry.not_kept


# ---------------------------------------------------------------------- API


def bearer(user):
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def test_a_contact_takes_and_returns_the_three_lists(client, user, company):
    response = post(
        client,
        f"/api/v1/companies/{company.pk}/contacts",
        {
            "name": "Sam",
            "phone_numbers": [
                {"number": "+351912345678", "kind": "mobile", "is_primary": True},
                {"number": "+351912345679", "kind": "work"},
            ],
            "postal_addresses": [
                {"kind": "work", "street": "Rua 1", "municipality": "Lisboa", "country": "pt"}
            ],
            "web_links": [
                {"kind": "social", "url": "https://www.linkedin.com/in/sam", "is_primary": True}
            ],
        },
        **bearer(user),
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert [n["number"] for n in body["phone_numbers"]] == ["+351912345678", "+351912345679"]
    assert body["phone"] == "+351912345678"
    assert body["postal_addresses"][0]["street"] == "Rua 1"
    assert body["postal_addresses"][0]["country"] == "PT"
    assert body["linkedin_url"] == "https://www.linkedin.com/in/sam"
    got = client.get(f"/api/v1/companies/{company.pk}", **bearer(user)).json()
    assert got["contacts"][0]["postal_addresses"][0]["municipality"] == "Lisboa"


def test_the_single_fields_still_work_and_join_the_lists(client, user, company):
    response = post(
        client,
        f"/api/v1/companies/{company.pk}/contacts",
        {
            "name": "Old Client",
            "phone": "+351912345670",
            "linkedin_url": "https://www.linkedin.com/in/old",
        },
        **bearer(user),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["phone"] == "+351912345670" and body["phone_numbers"][0]["is_primary"]
    assert body["linkedin_url"] == "https://www.linkedin.com/in/old"

    both = post(
        client,
        f"/api/v1/companies/{company.pk}/contacts",
        {
            "name": "Both",
            "phone": "+351912345671",
            "phone_numbers": [{"number": "+351912345672", "is_primary": True}],
        },
        **bearer(user),
    )
    numbers = {n["number"]: n["is_primary"] for n in both.json()["phone_numbers"]}
    assert numbers == {"+351912345671": False, "+351912345672": True}


def test_a_number_that_cannot_exist_is_a_422_and_leaves_no_contact(client, user, company):
    response = post(
        client,
        f"/api/v1/companies/{company.pk}/contacts",
        {"name": "Nobody", "phone_numbers": [{"number": "+3519"}]},
        **bearer(user),
    )
    assert response.status_code == 422
    assert not Contact.objects.filter(name="Nobody").exists()


def test_the_single_fields_are_marked_deprecated_in_the_schema(client, user):
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))
    described = client.get("/api/v1/openapi.json", HTTP_AUTHORIZATION=f"Bearer {raw}")
    schema = described.json()["components"]["schemas"]
    for name in ("ContactIn", "ContactOut"):
        properties = schema[name]["properties"]
        assert properties["linkedin_url"].get("deprecated") is True
        assert properties["phone"].get("deprecated") is True
        for listed in ("phone_numbers", "postal_addresses", "web_links"):
            assert listed in properties
    assert schema["ContactIn"]["properties"]["phone_country"].get("deprecated") is True


# ----------------------------------------------- the differences that are on purpose


def test_only_a_persons_own_numbers_are_unique_across_the_instance(user, company, contact):
    assert phone_numbers.identifies(user.profile) is True
    assert phone_numbers.identifies(company) is False
    assert phone_numbers.identifies(contact) is False
    assert number(user, user.profile, "+351912345678").identifies_owner is True
    assert number(user, company, "+351912345678").identifies_owner is False
    assert number(user, contact, "+351912345678").identifies_owner is False


def test_getting_back_in_is_offered_on_the_profile_alone(user, company, contact, monkeypatch):
    monkeypatch.setattr(phone_numbers, "can_be_chosen_here", lambda: True)
    assert phone_numbers.offers_the_recovery_choice(user.profile, user) is True
    assert phone_numbers.offers_the_recovery_choice(company, user) is False
    assert phone_numbers.offers_the_recovery_choice(contact, user) is False


def test_a_company_is_offered_social_profiles_alone_and_the_others_all_three():
    assert web_links.COMPANY_KINDS == (web_links.Kind.SOCIAL,)
    assert {block.kind for block in web_links.blocks()} == {
        web_links.Kind.SOCIAL,
        web_links.Kind.REPOSITORY,
        web_links.Kind.WEBSITE,
    }


def test_a_companys_addresses_are_always_on_the_map_and_the_others_are_asked(
    user, company, contact
):
    assert postal._pinnable(company) is False
    assert postal._pinnable(contact) is True
    assert postal._pinnable(user.profile) is True
    assert views.CompanyUpdateView.addresses_pinnable is False
    assert views.CompanyCreateView.addresses_pinnable is False
    assert PostalAddressesMixin.addresses_pinnable is True


def test_the_profiles_card_prints_one_number_where_a_companys_carries_every_one(user, company):
    number(user, user.profile, "+351912345678", kind="mobile", primary=True)
    number(user, user.profile, "+351912345679", kind="work")
    number(user, company, "+351211234567", primary=True)
    number(user, company, "+351211234568")
    person = lines(vcard.card_to_text(vcards.card_for_person(user)))
    assert len([line for line in person if line.startswith("TEL")]) <= 1, "what a CV prints"
    org = lines(vcard.card_to_text(vcards.card_for_company(Company.objects.get())))
    assert len([line for line in org if line.startswith("TEL")]) == 2
    assert "printed primary" in inspect.getsource(vcards.card_for_company)
