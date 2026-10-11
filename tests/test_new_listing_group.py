"""The two ways to start a listing are one group, with icons (#841)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client, user):
    company = Company.objects.create(owner=user, name="Aperture")
    JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    client.force_login(user)
    return client


def group_of(html: str) -> str:
    return re.search(r"<div class=\"button-group\"[^>]*data-new-listing>.*?</div>", html, re.S)[0]


def test_capture_and_add_are_one_labelled_group_with_one_variant(signed_in):
    group = group_of(signed_in.get(reverse("listings:list")).content.decode())

    assert 'role="group"' in group and 'aria-label="New listing"' in group
    links = re.findall(r"<a\b[^>]*>.*?</a>", group, re.S)
    assert len(links) == 2
    for link in links:
        assert 'class="btn"' in link and "data-variant" not in link, "both the page's main action"
    assert 'data-icon="clipboard-paste"' in links[0] and "Capture a posting" in links[0]
    assert 'data-icon="plus"' in links[1] and "Add a listing" in links[1]
    assert reverse("jobs:capture_create") in links[0] and reverse("listings:create") in links[1]


def test_the_group_is_painted_by_basecoats_structure():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    assert "components/button-group.css" in css


def test_the_other_capture_buttons_take_the_icon_without_the_group(signed_in):
    dashboard = signed_in.get(reverse("core:home")).content.decode()
    captures = signed_in.get(reverse("jobs:capture_list")).content.decode()

    for html in (dashboard, captures):
        assert 'data-icon="clipboard-paste"' in html
        assert "button-group" not in html
