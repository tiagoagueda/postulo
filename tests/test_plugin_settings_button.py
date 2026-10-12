"""A Settings button under the switch of every plugin that has settings (#885).

Hidden when there are none; shown, and a link, to someone who may change them; shown and
`aria-disabled` -- with the reason as its tooltip, since a `disabled` button shows none on
focus -- to someone who may not.
"""

from __future__ import annotations

import re

import pytest
from django.urls import NoReverseMatch, reverse

from postulo.plugins import base, policy, registry

pytestmark = pytest.mark.django_db


def row_of(body: str, name: str) -> str:
    start = body.index(f'data-policy="{name}"')
    return body[start : body.index("</li>", start)]


@pytest.fixture
def page(client, admin_user):
    client.force_login(admin_user)

    def get():
        return client.get(reverse("server:plugins")).content.decode()

    return get


def test_a_plugin_with_settings_has_a_button_that_links_to_them(page):
    row = row_of(page(), "gdpr")
    assert f'href="{reverse("server:data_protection")}"' in row
    assert 'data-settings="gdpr"' in row
    assert "Settings" in row


def test_a_plugin_without_settings_has_none(page):
    assert "data-settings" not in row_of(page(), "websites")


def test_a_notifier_and_a_transport_say_where_theirs_are(page):
    body = page()
    assert f'href="{reverse("server:email")}"' in row_of(body, "email")
    assert f'href="{reverse("server:email")}"' in row_of(body, "smtp")


def test_the_identifier_registry_keeps_its_dialog(page):
    row = row_of(page(), "identifiers")
    assert 'data-opens-dialog="settings-identifiers"' in row


def test_someone_who_may_not_change_them_sees_the_button_disabled_with_the_reason(
    page, monkeypatch
):
    monkeypatch.setattr(policy, "may_change_settings", lambda user, name: False)
    row = row_of(page(), "gdpr")
    assert 'aria-disabled="true"' in row
    assert 'title="You are not authorised to change settings."' in row
    assert "href=" not in re.sub(r"data-opens-dialog", "", row.split("data-settings")[1])


def test_every_settings_address_a_plugin_names_exists():
    for classes in registry.builtins().values():
        for plugin_class in classes:
            manifest = getattr(plugin_class, "manifest", None)
            if isinstance(manifest, base.Manifest) and manifest.settings:
                try:
                    reverse(manifest.settings)
                except NoReverseMatch:
                    pytest.fail(f"{manifest.name} names {manifest.settings}, which is no address")


def test_the_old_class_attribute_is_still_read(page, monkeypatch):
    plugin = registry.find_any("websites")
    monkeypatch.setattr(type(plugin), "settings_url_name", "server:email", raising=False)
    assert 'data-settings="websites"' in row_of(page(), "websites")


def test_only_an_administrator_may_change_settings():
    class Person:
        is_staff = True
        is_active = True

    class Stranger:
        is_staff = False

    assert policy.may_change_settings(Person(), "gdpr")
    assert not policy.may_change_settings(Stranger(), "gdpr")
    assert not policy.may_change_settings(None, "gdpr")
