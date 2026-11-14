"""Register lookup's keyed registers: where a key is kept and what it gates (#1065).

The United Kingdom's answer below is built from Companies House's ``companyProfile`` field
list (read 2026-11-14), not fetched with a key.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from django.urls import reverse

from postulo.core.models import SiteSettings
from postulo.jobs import register_lookup
from postulo.plugins.register_lookup import adapters

from .test_register_lookup import acme, answer, registers, switched_on  # noqa: F401

UNITED_KINGDOM = {
    "company_name": "ACME WIDGETS LIMITED",
    "company_number": "00445790",
    "company_status": "liquidation",
    "date_of_cessation": None,
    "registered_office_address": {
        "premises": "1",
        "address_line_1": "High Street",
        "locality": "London",
        "postal_code": "EC1A 1BB",
    },
    "sic_codes": ["62012"],
}
PATH = "/company/00445790"


def save_key(value="my-key"):
    row = SiteSettings.get()
    row.register_keys = {adapters.UNITED_KINGDOM.key: value}
    row.save()


def test_without_a_key_nothing_is_asked(registers, db):  # noqa: F811
    _, seen = registers

    assert not register_lookup.available(adapters.UNITED_KINGDOM)
    with pytest.raises(register_lookup.LookupRefused, match="needs a key"):
        register_lookup.lookup(adapters.UNITED_KINGDOM, "GB 00445790")
    assert seen == []


def test_the_key_goes_as_the_basic_user_name_and_not_in_the_address(registers, db):  # noqa: F811
    answers, seen = registers
    answer(answers, PATH, UNITED_KINGDOM)
    save_key("secret-key")

    record = register_lookup.lookup(adapters.UNITED_KINGDOM, "GB 445790")

    (request,) = seen
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"secret-key:").decode()
    assert "secret-key" not in str(request.url)
    assert record.name == "ACME WIDGETS LIMITED"
    assert record.address.street == "1 High Street"
    assert record.state == "administration"
    assert record.division == "62"


def test_a_key_the_register_refuses_is_said(registers, db):  # noqa: F811
    answers, _ = registers
    answer(answers, PATH, {}, status=401)
    save_key()

    with pytest.raises(register_lookup.LookupRefused, match="did not accept the key"):
        register_lookup.lookup(adapters.UNITED_KINGDOM, "GB 00445790")


def test_a_company_the_register_does_not_have_is_no_entry(registers, db):  # noqa: F811
    save_key()
    with pytest.raises(register_lookup.LookupRefused, match="no entry"):
        register_lookup.lookup(adapters.UNITED_KINGDOM, "GB 00445790")


def test_no_look_up_is_drawn_until_a_key_is_saved(client, user, switched_on, db):  # noqa: F811
    company = acme(user, value="GB 00445790", name="Acme")
    client.force_login(user)
    url = reverse("jobs:company_detail", args=[company.pk])

    assert "Companies House" not in client.get(url).content.decode()
    save_key()
    assert "Companies House" in client.get(url).content.decode()


def test_the_key_is_stored_encrypted_and_never_drawn_back(admin_client, db):
    url = reverse("server:register_lookup")
    key = f"key__{adapters.UNITED_KINGDOM.key}"

    admin_client.post(url, {key: "plain-secret"})
    row = SiteSettings.get()
    page = admin_client.get(url).content.decode()

    assert "plain-secret" not in row.register_keys_encrypted
    assert row.register_keys == {adapters.UNITED_KINGDOM.key: "plain-secret"}
    assert "plain-secret" not in page and 'data-key-saved="yes"' in page

    admin_client.post(url, {key: ""})
    assert SiteSettings.get().register_keys == {adapters.UNITED_KINGDOM.key: "plain-secret"}

    admin_client.post(url, {key: "", f"remove__{adapters.UNITED_KINGDOM.key}": "on"})
    assert SiteSettings.get().register_keys == {}


def test_the_settings_page_is_for_administrators(client, user):
    client.force_login(user)
    assert client.get(reverse("server:register_lookup")).status_code in (302, 403, 404)


def test_an_adapter_that_needs_a_key_must_say_where_it_goes():
    import dataclasses

    broken = dataclasses.replace(adapters.UNITED_KINGDOM, key_basic=False)
    both = dataclasses.replace(adapters.UNITED_KINGDOM, key_header="apikey")
    header = dataclasses.replace(adapters.UNITED_KINGDOM, key_basic=False, key_header="apikey")

    assert not register_lookup._usable(broken)
    assert not register_lookup._usable(both)
    assert register_lookup._usable(header)
    assert not register_lookup._usable(dataclasses.replace(adapters.FRANCE, key_basic=True))


def test_the_header_style_puts_the_key_in_its_header(registers, db):  # noqa: F811
    answers, seen = registers
    answer(answers, PATH, UNITED_KINGDOM)
    import dataclasses

    adapter = dataclasses.replace(adapters.UNITED_KINGDOM, key_basic=False, key_header="apikey")
    save_key("k1")
    row = SiteSettings.get()
    row.register_keys = {adapter.key: "k1"}
    row.save()

    register_lookup.lookup(adapter, "GB 00445790")

    assert seen[0].headers["apikey"] == "k1" and "Authorization" not in seen[0].headers
    assert isinstance(seen[0], httpx.Request)
