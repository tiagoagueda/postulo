"""Your details is one record in several parts, navigated rather than traversed (#180).

The sidebar is built from what the page is drawing: a part a feature has switched off has
no block on the page and so no entry, and the count beside a list is the rows it starts
with. The test that matters is the one that checks every entry points at something.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink
from postulo.plugins.phone_numbers import PHONE_NUMBERS

pytestmark = pytest.mark.django_db


def entries(html: str) -> dict[str, str]:
    """Every anchor the sidebar offers, with the count drawn beside it (or "")."""
    found = {}
    for match in re.finditer(r'data-section-link="([^"]+)"(.*?)</a>', html, re.S):
        count = re.search(r'class="badge[^>]*>(\d+)<', match.group(2))
        found[match.group(1)] = count.group(1) if count else ""
    return found


def test_every_entry_points_at_a_part_of_the_page(client, user):
    profile = user.profile
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351912345678", is_primary=True)
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351211111111")
    PostalAddress.objects.create(owner=user, holder=profile, street="Rua 1", is_primary=True)
    WebLink.objects.create(
        owner=user, holder=profile, kind="website", url="https://alex.example", is_primary=True
    )
    MessagingHandle.objects.create(
        owner=user, holder=profile, service="matrix", handle="@alex:example.org", is_primary=True
    )
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    nav = entries(html)

    assert set(nav) == {
        "section-picture",
        "section-cv-photo",
        "section-name",
        "section-personal",
        "section-contact",
        "section-links-social",
        "section-links-repository",
        "section-links-website",
        "section-phones",
        "section-messaging",
        "section-addresses",
        "section-identifiers",
    }
    for anchor in nav:
        assert f'id="{anchor}"' in html, f"{anchor} is offered and not on the page"
    assert nav["section-phones"] == "2"
    assert nav["section-addresses"] == "1"
    assert nav["section-messaging"] == "1"
    assert nav["section-links-website"] == "1"
    assert nav["section-links-social"] == "0", (
        "listed all the same: that is how you learn it exists"
    )
    assert nav["section-picture"] == "", "not a list, so no count"


def test_a_part_a_feature_switched_off_has_no_entry(client, user):
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.create(
        plugin=PHONE_NUMBERS, person=user, state=PluginPolicy.State.FORCED_OFF
    )
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "section-phones" not in entries(html)
    assert 'id="section-phones"' not in html, "one box on the contact block, not a block"


def test_the_page_no_longer_sends_people_to_settings_for_their_addresses(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "username, addresses" not in html
    assert "as a candidate" in html


def test_the_label_over_the_list_is_the_hook_and_keeps_its_text(client, user):
    """The script swaps the label for the page's name; without it the text stays (#869)."""
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    label = re.search(r"<p[^>]*data-section-label[^>]*>(.*?)</p>", html, re.S)
    assert label and label.group(1).strip() == "On this page"
    assert "aria-live" not in label.group(0), "the list's aria-current already says where you are"


# ------------------------------------------------------------ a card not saved (#927)


def unsaved_parts(html: str) -> re.Match:
    """The page's <template> of what `app.js` marks an unsaved card with."""
    found = re.search(
        r'<template data-card-unsaved-parts data-changed-fields="([^"]*)">(.*?)</template>',
        html,
        re.S,
    )
    assert found, "the parts are in the page for the script to copy"
    return found


def test_the_words_an_unsaved_card_is_marked_with_are_the_pages(client, user):
    """Translated with the page, a badge in the amber tone and a dot with hidden words."""
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()

    parts = unsaved_parts(html)
    assert parts.group(1) == "", "nothing is unsaved on a page just loaded"
    assert re.search(
        r'<span class="badge[^"]*" data-tone="amber" data-card-unsaved-badge>Not saved</span>',
        parts.group(2),
    )
    assert '<span data-card-unsaved-dot><span class="sr-only">Not saved</span></span>' in (
        parts.group(2)
    )
    assert "data-unsaved" not in html.replace("data-card-unsaved", ""), (
        "the mark of a field that saves as it goes, which the guard looks for (#656)"
    )


def test_a_refused_save_names_the_fields_it_brought_back_changed(client, user):
    """The page is drawn with what was typed, so only the server can say what differs."""
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alexandra",
            "last_name": "Morgan",
            "form_of_address": "",
            "form_of_address_other": "",
            "pronouns": "",
            "pronouns_other": "",
            "headline": "",
            "location": "",
            "record_language": "",
            "birth_day": "30",
            "birth_month": "2",
            "birth_year": "1990",
        },
    )

    assert response.status_code == 200
    changed = unsaved_parts(response.content.decode()).group(1).split()
    assert "first_name" in changed
    assert "birth_day" in changed
    assert "last_name" not in changed, "typed as it is held, so not a change"
