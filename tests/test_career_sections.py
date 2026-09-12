"""The career page lists its sections down the side, with a count beside each (#175).

Seven sections down one page, each a list with no ceiling, and until this nothing on the
page said what was below the fold: the only way to learn it held Languages was to reach
the bottom. The sidebar is the one Settings uses, generalised to take anchors as well as
pages, so the two cannot drift apart.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse

from postulo.resume.models import Experience
from postulo.resume.registry import OVERVIEW_ORDER, SECTIONS

pytestmark = pytest.mark.django_db


def the_page(client, user) -> str:
    client.force_login(user)
    return client.get(reverse("resume:overview")).content.decode()


def sidebar_of(html: str) -> str:
    start = html.index('aria-label="Career sections"')
    return html[start : html.index("</nav>", start)]


def test_every_section_is_listed_in_order_with_its_count(client, user):
    Experience.objects.create(
        owner=user, organisation="Aperture", role="Engineer", start_date=dt.date(2020, 1, 1)
    )
    Experience.objects.create(
        owner=user, organisation="Black Mesa", role="Researcher", start_date=dt.date(2022, 1, 1)
    )

    nav = sidebar_of(the_page(client, user))

    hrefs = re.findall(r'href="(#section-[a-z-]+)"', nav)
    assert hrefs == [f"#section-{slug}" for slug in OVERVIEW_ORDER], "one anchor per section"
    counts = re.findall(
        r'data-section-link="section-([a-z-]+)".*?<span class="ms-auto[^"]*">(\d+)</span>',
        nav,
        re.S,
    )
    assert dict(counts)["experience"] == "2"
    assert dict(counts)["language"] == "0", "an empty section is listed, and says so"
    assert len(counts) == len(OVERVIEW_ORDER)


def test_the_anchors_land_on_the_sections(client, user):
    html = the_page(client, user)
    for slug in OVERVIEW_ORDER:
        assert f'<section id="section-{slug}">' in html, slug
        assert str(SECTIONS[slug].plural) in html


def test_nothing_is_current_until_a_browser_says_so(client, user):
    """`aria-current` on an anchor means the section on the screen, which only the browser
    knows; the markup claims nothing, and app.js sets `location` as the page is read."""
    nav = sidebar_of(the_page(client, user))
    assert "aria-current" not in nav
    assert "nav-link-active" not in nav


def test_the_settings_sidebar_still_marks_its_page(client, user):
    """The same template serves Settings, where the current entry is a page."""
    client.force_login(user)
    html = client.get(reverse("settings:appearance")).content.decode()
    start = html.index('aria-label="Settings sections"')
    nav = html[start : html.index("</nav>", start)]
    assert nav.count('aria-current="page"') == 1
    assert "nav-link-active" in nav
