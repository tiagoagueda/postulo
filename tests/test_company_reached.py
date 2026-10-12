"""A company's mailbox, numbers and addresses (#329).

A company's card on a CardDAV server carries what it answers on: a switchboard, a general
mailbox, an office. Postulo now keeps them, on the company's form and page, through the API,
an archive and a merge. A company's number is one a person recorded, so it is unique within
the company and nowhere else; an address is unique per holder.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.urls import reverse

from postulo.core import export, importer
from postulo.core.models import PhoneNumber, PostalAddress
from postulo.jobs import merging
from postulo.jobs.models import Company, Contact

from .test_phone_numbers import number_rows
from .test_postal_addresses import address_rows


def form(**extra) -> dict:
    return {"name": "Aperture", "kind": "employer", "notes": "", **extra}


def number(holder, owner, value, *, primary=False):
    return PhoneNumber.objects.create(owner=owner, holder=holder, number=value, is_primary=primary)


def place(holder, owner, street="Rua do Exemplo 1", **parts):
    fields = {"street": street, "postcode": "1000-001", "municipality": "Lisboa", "country": "PT"}
    fields.update(parts)
    return PostalAddress.objects.create(owner=owner, holder=holder, **fields)


# ------------------------------------------------------------------- the form and the page


def test_a_new_company_is_saved_with_its_mailbox_number_and_office(client, user):
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_create"),
        form(
            email="jobs@aperture.example",
            **number_rows("211234567"),
            **address_rows("Rua do Exemplo 1"),
        ),
    )

    assert response.status_code == 302
    company = Company.objects.get(owner=user, name="Aperture")
    assert company.email == "jobs@aperture.example"
    (row,) = company.phone_numbers.all()
    assert (row.normalised, row.owner, row.identifies_owner) == ("+351211234567", user, False)
    (office,) = company.postal_addresses.all()
    assert (office.municipality, office.owner) == ("Lisboa", user)


def test_a_wrong_address_keeps_the_company_unsaved(client, user):
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_create"),
        form(**number_rows("211234567"), **address_rows("Rua 1", "Rua 1")),
    )

    assert response.status_code == 200
    assert not Company.objects.filter(owner=user).exists(), "with its rows or not at all"


def test_the_edit_page_draws_the_rows(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    place(company, user)
    client.force_login(user)

    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert "data-phone-numbers" in html and "data-postal-addresses" in html
    assert 'name="email"' in html


def test_with_several_numbers_off_the_form_has_one_box(client, user):
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.phone_numbers import PHONE_NUMBERS

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    response = client.post(
        reverse("jobs:company_create"),
        form(phone_0="PT", phone_1="211234567"),
    )

    assert response.status_code == 302
    company = Company.objects.get(owner=user)
    assert company.phone_numbers.get().is_primary


def test_the_page_says_how_to_reach_it(client, user):
    company = Company.objects.create(owner=user, name="Aperture", email="jobs@aperture.example")
    number(company, user, "+351211234567", primary=True)
    place(company, user, kind="work")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert "data-company-reached" in html
    assert "mailto:jobs@aperture.example" in html
    assert "Rua do Exemplo 1" in html and "Lisboa" in html


# ------------------------------------------------------------ what may be recorded twice


def test_two_accounts_record_one_switchboard_on_their_companies(user, other_user):
    mine = Company.objects.create(owner=user, name="IEFP")
    theirs = Company.objects.create(owner=other_user, name="IEFP")

    number(mine, user, "+351211234567", primary=True)
    number(theirs, other_user, "+351 21 123 4567", primary=True)

    assert PhoneNumber.objects.filter(normalised="+351211234567").count() == 2


def test_a_company_and_its_recruiter_share_a_switchboard(user):
    company = Company.objects.create(owner=user, name="Aperture")
    recruiter = Contact.objects.create(owner=user, company=company, name="Caroline")

    number(company, user, "+351211234567", primary=True)
    number(recruiter, user, "+351211234567", primary=True)

    assert company.phone_numbers.count() == recruiter.phone_numbers.count() == 1


def test_only_a_profiles_number_identifies_its_owner(user):
    company = Company.objects.create(owner=user, name="Aperture")

    assert not number(company, user, "+351211234567").identifies_owner
    assert number(user.profile, user, "+351911111111").identifies_owner


# ----------------------------------------------------------------------- a merge


def test_merging_moves_the_numbers_addresses_and_mailbox(user):
    kept = Company.objects.create(owner=user, name="Aperture")
    other = Company.objects.create(owner=user, name="Aperture Science", email="hr@a.example")
    number(kept, user, "+351211234567", primary=True)
    number(other, user, "+351211234567", primary=True)
    number(other, user, "+351219999999")
    place(kept, user)
    place(other, user)
    place(other, user, street="Avenida 2")

    plan = merging.merge_companies(kept, other)

    assert sorted(kept.phone_numbers.values_list("normalised", flat=True)) == [
        "+351211234567",
        "+351219999999",
    ], "the twin is not listed twice"
    assert kept.phone_numbers.filter(is_primary=True).count() == 1
    assert sorted(kept.postal_addresses.values_list("street", flat=True)) == [
        "Avenida 2",
        "Rua do Exemplo 1",
    ]
    kept.refresh_from_db()
    assert kept.email == "hr@a.example"
    assert not Company.objects.filter(pk=other.pk).exists()
    assert any("Telephone numbers" in str(line.label) for line in plan.moves)


def test_merging_contacts_does_not_list_a_shared_number_twice(user):
    company = Company.objects.create(owner=user, name="Aperture")
    kept = Contact.objects.create(owner=user, company=company, name="Caroline")
    other = Contact.objects.create(owner=user, company=company, name="Caroline B.")
    number(kept, user, "+351211234567", primary=True)
    number(other, user, "+351211234567", primary=True)

    merging.merge_contacts(kept, other)

    assert kept.phone_numbers.count() == 1


# ---------------------------------------------------------------------------- the API


@pytest.fixture
def api(client, user):
    from postulo.api.models import ApiToken

    _token, secret = ApiToken.issue(user, "test", scopes=["read", "write"])

    def call(method, path, payload=None):
        return getattr(client, method)(
            f"/api/v1{path}",
            data=json.dumps(payload) if payload is not None else None,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {secret}",
        )

    return call


def test_the_api_adds_and_reads_them(api, user):
    response = api(
        "post",
        "/companies",
        {
            "name": "Aperture",
            "email": "jobs@aperture.example",
            "phone_numbers": [{"number": "211234567", "country": "PT", "is_primary": True}],
            "postal_addresses": [
                {"kind": "work", "street": "Rua 1", "municipality": "Lisboa", "country": "pt"}
            ],
        },
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["email"] == "jobs@aperture.example"
    assert [row["number"] for row in body["phone_numbers"]] == ["+351211234567"]
    assert body["postal_addresses"][0]["country"] == "PT"


def test_the_api_replaces_them_on_a_patch(api, user):
    company = Company.objects.create(owner=user, name="Aperture")
    number(company, user, "+351211234567", primary=True)

    response = api(
        "patch",
        f"/companies/{company.pk}",
        {"phone_numbers": [{"number": "+351219999999"}], "postal_addresses": []},
    )

    assert response.status_code == 200, response.content
    assert [row["number"] for row in response.json()["phone_numbers"]] == ["+351219999999"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not an address"},
        {"phone_numbers": [{"number": "123", "country": "PT"}]},
        {"phone_numbers": [{"number": "+351211234567", "kind": "pager"}]},
        {"postal_addresses": [{"street": "Rua 1", "kind": "castle"}]},
    ],
)
def test_the_api_refuses_what_the_form_would(api, user, payload):
    company = Company.objects.create(owner=user, name="Aperture")

    response = api("patch", f"/companies/{company.pk}", payload)

    assert response.status_code == 422, response.content
    assert not company.phone_numbers.exists() and not company.postal_addresses.exists()


# --------------------------------------------------------------------- the archive


def _round_trip(user, other_user) -> Company:
    data = export.write_archive(user)
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        importer.load(other_user, archive)
    return Company.objects.get(owner=other_user, name="Aperture")


def test_the_archive_carries_them_and_brings_them_back(user, other_user):
    company = Company.objects.create(owner=user, name="Aperture", email="jobs@aperture.example")
    number(company, user, "+351211234567", primary=True)
    place(company, user)

    restored = _round_trip(user, other_user)

    assert restored.email == "jobs@aperture.example"
    assert restored.phone_numbers.get().normalised == "+351211234567"
    assert restored.postal_addresses.get().street == "Rua do Exemplo 1"


def test_the_archive_format_moved():
    assert export.FORMAT_VERSION >= 67
