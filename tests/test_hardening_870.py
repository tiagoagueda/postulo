"""The section menu of Your details and Your career has the look of vertical tabs (#870)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ["accounts:profile", "resume:overview"])
def test_the_section_menu_is_drawn_as_vertical_tabs_and_stays_anchors(client, user, name):
    client.force_login(user)

    html = client.get(reverse(name)).content.decode()

    assert "<nav" in html and "data-vertical-tabs" in html
    nav = html[html.index("data-vertical-tabs") :].split("</nav>")[0]
    assert "data-section-link" in nav and 'href="#' in nav
    assert 'role="tab' not in nav, "links to sections of one page are not an ARIA tablist"


def test_the_pages_of_an_area_keep_the_plain_list(client, user):
    client.force_login(user)

    html = client.get(reverse("settings:appearance")).content.decode()

    assert "data-vertical-tabs" not in html


def test_the_stylesheet_paints_the_tabs():
    from pathlib import Path

    css = Path("src/postulo/static/css/app.css").read_text(encoding="utf-8")

    assert "nav[data-vertical-tabs] a.nav-link-active" in css
