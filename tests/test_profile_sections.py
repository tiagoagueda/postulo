"""Your details is one record in several parts, navigated rather than traversed (#180).

The sidebar is built from what the page is drawing: a part a feature has switched off has
no block on the page and so no entry, and the count beside a list is the rows it starts
with. The test that matters is the one that checks every entry points at something.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.plugins.phone_numbers import PHONE_NUMBERS

pytestmark = pytest.mark.django_db


def entries(html: str) -> dict[str, str]:
    """Every anchor the sidebar offers, with the count drawn beside it (or "")."""
    found = {}
    for match in re.finditer(r'data-section-link="([^"]+)"(.*?)</a>', html, re.S):
        count = re.search(r"rounded-full[^>]*>(\d+)<", match.group(2))
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
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    nav = entries(html)

    assert set(nav) == {
        "section-picture",
        "section-name",
        "section-contact",
        "section-links-social",
        "section-links-repository",
        "section-links-website",
        "section-phones",
        "section-addresses",
        "section-identifiers",
    }
    for anchor in nav:
        assert f'id="{anchor}"' in html, f"{anchor} is offered and not on the page"
    assert nav["section-phones"] == "2"
    assert nav["section-addresses"] == "1"
    assert nav["section-links-website"] == "1"
    assert nav["section-links-social"] == "0", (
        "listed all the same: that is how you learn it exists"
    )
    assert nav["section-picture"] == "", "not a list, so no count"


def test_a_part_a_feature_switched_off_has_no_entry(client, user):
    user.profile.plugins_off = [PHONE_NUMBERS]
    user.profile.save(update_fields=["plugins_off"])
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "section-phones" not in entries(html)
    assert 'id="section-phones"' not in html, "one box on the contact block, not a block"


def test_the_page_no_longer_sends_people_to_settings_for_their_addresses(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "username, addresses" not in html
    assert "as a candidate" in html
