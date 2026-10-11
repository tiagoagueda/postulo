"""A contact row reads star, type, boxes, bin; a postal address has both up at its type (#866)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def details(client, user):
    from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress

    profile = user.profile
    PhoneNumber.objects.create(owner=user, holder=profile, number="+351912345678", is_primary=True)
    MessagingHandle.objects.create(
        owner=user, holder=profile, service="matrix", handle="@a:example.org", is_primary=True
    )
    PostalAddress.objects.create(
        owner=user, holder=profile, country="PT", municipality="Lisboa", is_primary=True
    )
    client.force_login(user)
    return client.get(reverse("accounts:profile")).content.decode()


def section(html: str, marker: str) -> str:
    start = html.index(marker)
    return html[start : html.index("</fieldset>", start)]


def first_row(block: str) -> str:
    start = block.index("<li ")
    return block[start : block.index("</li>", start)]


@pytest.mark.parametrize("marker", ["data-phone-numbers", "data-messaging"])
def test_the_star_comes_first_and_the_bin_last(details, marker):
    row = first_row(section(details, marker))

    star, kind, bin_ = row.index("data-row-star"), row.index("<select"), row.index("data-row-bin")
    assert star < kind < bin_
    assert "data-remove-trigger" in row[bin_:]
    assert "primary-choice" in row[star:kind]


def test_the_links_block_does_the_same(details):
    row = first_row(section(details, 'data-web-links="social"'))

    assert row.index("data-row-star") < row.index("<select") < row.index("data-row-bin")


def test_a_postal_address_has_star_and_bin_on_the_type_line(details):
    row = first_row(section(details, "data-postal-addresses"))

    head = re.search(r"data-address-head>(.*?)data-row-actions(.*?)</div>\s*</div>", row, re.S)
    assert head, "the controls sit in the head beside the type"
    assert row.index("data-row-actions") < row.index('data-part="country"')
    assert row.count("data-row-actions") == 1
    assert "primary-choice" in row and "data-remove-trigger" in row
