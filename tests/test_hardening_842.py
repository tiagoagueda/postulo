"""The Listings state strip is drawn like Basecoat's tabs and stays a strip of links (#842)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, JobPosting
from tests.test_components import render

pytestmark = pytest.mark.django_db

ITEMS = [
    {"value": "undecided", "label": "To decide", "count": 3, "active": True},
    {"value": "shortlisted", "label": "Shortlisted", "count": 0, "active": False},
    {"value": "closed", "label": "Closed", "count": None, "active": False},
]


def strip(html: str) -> str:
    return re.search(r'<nav class="tab-nav[^"]*".*?</nav>', html, re.S)[0]


def test_the_component_draws_links_in_a_nav_with_the_open_one_current():
    html = render(
        '{% cotton tab-nav :items="items" label="Filters" param="state" default="undecided" / %}',
        items=ITEMS,
        request=type("R", (), {"path": "/listings/"})(),
    )

    nav = strip(html)
    assert 'aria-label="Filters"' in nav
    assert nav.count("<a ") == 3 and nav.count('aria-current="page"') == 1
    assert 'href="/listings/"' in nav, "the usual tab links to the bare page"
    assert 'href="?state=shortlisted"' in nav
    assert 'class="tab-nav-count">3<' in nav and 'class="tab-nav-count">0<' in nav
    assert nav.count("tab-nav-count") == 2, "no count where there is none"


def test_it_is_not_a_tablist():
    html = render(
        '{% cotton tab-nav :items="items" label="Filters" / %}',
        items=ITEMS,
        request=type("R", (), {"path": "/x/"})(),
    )

    assert "role=" not in strip(html), "links that load a page are not tabs"


def test_the_listings_page_draws_its_strip_with_it(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="Tester")
    client.force_login(user)

    html = client.get(reverse("listings:list")).content.decode()

    nav = strip(html)
    assert 'aria-label="Listing filters"' in nav
    assert "To decide" in nav and "Everything" in nav and 'aria-current="page"' in nav
    assert "nav-link" not in nav


def test_the_old_strip_classes_are_gone_from_the_listings_template():
    from pathlib import Path

    source = Path("src/postulo/templates/jobs/listing_list.html").read_text(encoding="utf-8")

    assert "nav-link-active" not in source and "<c-tab-nav" in source
