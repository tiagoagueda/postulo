"""The header: an account menu on the right, and nothing else there but the theme switch."""

import re

import pytest
from django.template import Context, Template
from django.urls import reverse

from postulo.core.templatetags.postulo import AVATAR_COLOURS, initials_for

pytestmark = pytest.mark.django_db


def header_of(response) -> str:
    html = response.content.decode()
    return html[html.index("<header") : html.index("</header>")]


def test_the_right_side_holds_the_account_menu_and_the_theme_switch(client, user):
    client.force_login(user)
    header = header_of(client.get(reverse("core:home")))
    assert "details" in header and "data-menu" in header
    assert user.display_name in header
    assert "Account menu, applicant" in header
    assert "Your details" in header
    assert "Export everything" in header
    assert "Sign out" in header
    assert "data-theme-switch" in header
    # Capture and Record have left the header.
    assert reverse("jobs:capture_create") not in header
    assert reverse("applications:create") not in header


def test_sign_out_stays_a_post(client, user):
    client.force_login(user)
    header = header_of(client.get(reverse("core:home")))
    pattern = r'<form method="post" action="([^"]+)">\s*<input[^>]+csrfmiddlewaretoken'
    form = re.search(pattern, header)
    assert form and form.group(1) == reverse("account_logout")


def test_the_dashboard_took_over_the_two_actions(client, user):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    body = html[html.index("</header>") :]
    assert reverse("jobs:capture_create") in body
    assert reverse("applications:create") in body
    assert "Capture a posting" in body
    assert "Record an application" in body


def test_signed_out_visitors_see_neither_menu_nor_switch(client):
    header = header_of(client.get(reverse("core:home")))
    assert "data-menu" not in header
    assert "data-theme-switch" not in header
    assert "Sign in" in header


class FakeUser:
    def __init__(self, first_name="", last_name="", display_name=""):
        self.first_name = first_name
        self.last_name = last_name
        self.display_name = display_name


def test_initials_prefer_the_two_names():
    assert initials_for(FakeUser("Alex", "Morgan", "Alex Morgan")) == "AM"
    assert initials_for(FakeUser("Alex", "", "Alex")) == "AL"
    assert initials_for(FakeUser("", "", "alex.morgan")) == "AM"
    assert initials_for(FakeUser("", "", "applicant")) == "AP"
    assert initials_for(FakeUser("", "", "")) == "?"


def test_avatar_is_a_decorative_tile_with_a_stable_colour():
    render = lambda u: Template("{% load postulo %}{% avatar u %}").render(Context({"u": u}))  # noqa: E731
    first = render(FakeUser("Alex", "Morgan", "Alex Morgan"))
    again = render(FakeUser("Alex", "Morgan", "Alex Morgan"))
    assert first == again
    assert ">AM</span>" in first
    assert 'aria-hidden="true"' in first
    assert any(colour in first for colour in AVATAR_COLOURS)
    assert "<script" not in render(FakeUser("<script>", "x", "<script> x"))
