"""Postulo installed as an app of its own (#895).

The manifest a browser installs from, the page head an installed window and Safari read, the
worker registered for anybody signed in, the offline page it keeps, and the account menu's
*Install* entry. What the worker may keep is a boundary and is held in
`tests/security/test_offline_page.py`; the browser suite installs nothing, but checks the
manifest as a browser reads it and the offline page as a browser is shown it.
"""

from __future__ import annotations

import json
import re

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def manifest(client) -> dict:
    return json.loads(client.get(reverse("core:manifest")).content)


# ------------------------------------------------------------------ the manifest


def test_the_manifest_has_what_an_install_needs():
    payload = manifest(Client())

    assert payload["id"] == "/" and payload["scope"] == "/"
    assert payload["display"] == "standalone"
    assert payload["start_url"] == reverse("applications:board")
    assert payload["lang"] == "en-GB" and payload["dir"] == "ltr"
    assert payload["theme_color"] and payload["background_color"]


def test_the_manifest_keeps_the_maskable_icon_apart_from_the_plain_one():
    """One image for both purposes is the one a launcher cuts into."""
    icons = manifest(Client())["icons"]

    plain = [icon for icon in icons if "purpose" not in icon]
    maskable = [icon for icon in icons if icon.get("purpose") == "maskable"]
    assert {icon["sizes"] for icon in plain} == {"192x192", "512x512"}
    assert [icon["src"] for icon in maskable] == ["/static/brand/icon-maskable-512.png"]
    assert not any("any" in icon.get("purpose", "") for icon in icons)


def test_the_shortcuts_lead_to_the_board_the_listings_and_the_calendar():
    shortcuts = manifest(Client())["shortcuts"]

    assert [shortcut["url"] for shortcut in shortcuts] == [
        reverse("applications:board"),
        reverse("listings:list"),
        reverse("applications:calendar"),
    ]
    assert [shortcut["name"] for shortcut in shortcuts] == ["Board", "Listings", "Calendar"]


def test_the_manifest_speaks_the_accounts_language(client, user):
    user.profile.language = "fr-FR"
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("core:manifest"))

    payload = json.loads(response.content)
    assert payload["lang"] == "fr-FR"
    assert response["Content-Language"] == "fr-FR"


def test_the_manifest_is_fetched_with_credentials(client, user):
    """Otherwise the browser asks without the session, and gets the default language."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    link = re.search(r'<link rel="manifest"[^>]*>', html)[0]
    assert 'crossorigin="use-credentials"' in link


# ------------------------------------------------------------------- the page head


def test_the_title_bar_follows_the_system_theme_by_default(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert (
        '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">' in html
    )
    assert (
        '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#101419">' in html
    )


def test_the_title_bar_follows_a_chosen_theme(client, user):
    user.profile.theme = "dark"
    user.profile.save()
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert re.findall(r'<meta name="theme-color"[^>]*>', html) == [
        '<meta name="theme-color" content="#101419">'
    ]


def test_safari_is_told_the_instances_name(client, user):
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Alex's search"})
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    assert '<meta name="apple-mobile-web-app-title" content="Alex&#x27;s search">' in html


# ------------------------------------------------------------------- the worker


def test_the_worker_is_offered_to_anybody_signed_in_and_nobody_else(client, user):
    signed_out = Client().get(reverse("account_login")).content.decode()
    client.force_login(user)
    signed_in = client.get(reverse("core:home")).content.decode()

    assert f'data-service-worker="{reverse("notifications:worker")}"' in signed_in
    assert "data-service-worker" not in signed_out, "signing out unregisters it (#753)"


# -------------------------------------------------------------- the offline page


def test_the_offline_page_needs_no_sign_in_and_is_in_the_accounts_language(client, user):
    assert Client().get(reverse("core:offline")).status_code == 200

    user.profile.language = "fr-FR"
    user.profile.save()
    client.force_login(user)
    response = client.get(reverse("core:offline"))

    assert response.status_code == 200
    assert response["Content-Language"] == "fr-FR", "the worker compares it with the tab's"
    assert '<html lang="fr-FR"' in response.content.decode()


def test_the_offline_page_brings_its_own_rules_under_its_own_nonce():
    """When it is shown nothing else can be fetched, so nothing is linked."""
    response = Client().get(reverse("core:offline"))
    html = response.content.decode()

    nonce = re.search(r'<style nonce="([^"]+)">', html)[1]
    assert f"'nonce-{nonce}'" in response["Content-Security-Policy"]
    assert "--ground" in html, "the 500 page's stylesheet, inlined"
    assert '<link rel="stylesheet"' not in html and "<script" not in html


def test_the_offline_page_names_the_instance_and_offers_another_try():
    from postulo.core.models import SiteSettings

    SiteSettings.objects.update_or_create(pk=1, defaults={"instance_name": "Job hunt"})
    html = Client().get(reverse("core:offline")).content.decode()

    assert re.search(r"<h1>\s*Job hunt cannot reach its server\s*</h1>", html)
    assert '<a href="">Try again</a>' in html, "the address that failed, loaded again"


def test_the_offline_page_is_not_marked_never_to_keep(client, user):
    """The worker keeps it on purpose; the private-page rule is for pages about somebody."""
    client.force_login(user)

    assert client.get(reverse("core:offline"))["Cache-Control"] == "no-cache"


# --------------------------------------------------------------- the account menu


def test_the_account_menu_holds_both_install_entries_hidden(client, user):
    """A script shows the one this browser can use, and neither once it is installed."""
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()

    button = re.search(r"<button[^>]*data-install-app[^>]*>", html)[0]
    link = re.search(r"<a[^>]*data-install-help[^>]*>", html)[0]
    assert "hidden" in button and "hidden" in link
    assert f'href="{reverse("core:help_topic", args=["install"])}"' in link
