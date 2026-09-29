"""The header: a + menu and an avatar-only account menu on the right. The name used to
be in the account trigger and is a row of that menu since #282, and the theme switch
is a row of that menu since #197."""

import re

import pytest
from django.template import Context, Template
from django.urls import reverse

from postulo.core.templatetags.postulo import AVATAR_COLOURS, initials_for

pytestmark = pytest.mark.django_db


def header_of(response) -> str:
    html = response.content.decode()
    return html[html.index("<header") : html.index("</header>")]


def test_the_right_side_holds_the_plus_menu_and_the_account_menu(client, user):
    client.force_login(user)
    header = header_of(client.get(reverse("core:home")))
    # Both are `<c-dropdown-menu>`: a trigger and a popover each (#310).
    assert header.count("popovertarget=") >= 2 and "data-menu" in header
    assert "<details" not in header, "no menu in the masthead is a <details> any more"
    assert "Account menu, applicant" in header
    assert "Your details" in header
    assert "Settings" in header
    assert "Sign out" in header
    assert "data-theme-switch" in header

    # The profile button is the avatar and nothing else (#282): the name is a row of
    # the menu now, and the trigger's aria-label is the control's only name -- so the
    # name is in the label and in nothing the trigger draws.
    start = header.index('aria-label="Account menu, applicant"')
    trigger = header[start : header.index("</button>", start)]
    visible = trigger[trigger.index(">") + 1 :]
    assert user.display_name not in visible
    panel = header[header.index("</button>", start) : header.index("Your details")]
    assert user.display_name in panel, "and it is in the menu, where it says who you are"
    assert user.email in panel

    # The + menu starts what a person starts (#282): a listing -- through the capture,
    # which is how a listing usually starts -- an application, a company, a reminder,
    # and the three documents. *Reminder* is in for *event*, which has no route that
    # makes one from nowhere, and *Application* was not asked for and is there on
    # purpose. The header's *Capture* and *Record* buttons left long ago; the + menu is
    # what takes their place, as entries of a menu rather than as buttons.
    assert reverse("jobs:capture_create") in header
    assert reverse("applications:create") in header
    assert reverse("jobs:company_create") in header
    assert reverse("applications:reminder_create") in header
    assert reverse("documents:cv_create") in header
    assert reverse("documents:letter_create") in header
    assert reverse("documents:upload_create") in header
    # Export has stayed out: it is a settings section of its own, beside deleting the
    # account, and a menu with two ways to the same page teaches people to read neither.
    assert reverse("core:export") not in header


def test_the_header_floats_and_everything_that_has_to_clear_it_reads_one_height(client, user):
    """`sticky top-0` on the header, and the offsets that follow it all read the height the
    script measures, with the same fallback where it does not run (#195)."""
    from pathlib import Path

    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    header = html.split("<header", 1)[1].split(">", 1)[0]
    assert "sticky" in header and "top-0" in header and "data-site-header" in header

    profile = client.get(reverse("accounts:profile")).content.decode()
    assert "lg:top-[calc(var(--header-height,3.5rem)+1rem)]" in profile, "the sidebar clears it"

    root = Path(__file__).resolve().parents[1]
    static = root / "src" / "postulo" / "static"
    stylesheet = (static / "css" / "app.css").read_text(encoding="utf-8")
    assert "scroll-padding-top" in stylesheet and "--header-height" in stylesheet
    script = (static / "js" / "app.js").read_text(encoding="utf-8")
    assert '"--header-height"' in script


def test_the_bar_is_cleared_by_everything_that_has_to_clear_it():
    """Below `md` the main navigation is a bar fixed to the foot of the window (#299). The
    script measures it into `--bottom-bar-height`, and the page's own padding, the scroll
    padding a focused link is placed by, and the failure alert all read that one value, with
    the same fallback where no script runs -- the header's arrangement, at the other edge."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    static = root / "src" / "postulo" / "static"
    compiled = (static / "css" / "app.css").read_text(encoding="utf-8")
    start = compiled.index("@media (width < 48rem) {\n  .nav-main {")
    bar = compiled[start : compiled.index("\n}\n", start)]
    assert "position: fixed" in bar and "bottom: 0px" in bar
    fallback = "var(--bottom-bar-height, 4.5rem)"
    assert re.search(r"body:has\(\.nav-main\) \{\s*padding-bottom: " + re.escape(fallback), bar)
    assert "scroll-padding-bottom: calc(" + fallback + " + 1rem)" in bar
    assert re.search(r"\.page-alert \{\s*bottom: calc\(" + re.escape(fallback), bar)

    script = (static / "js" / "app.js").read_text(encoding="utf-8")
    assert '"--bottom-bar-height"' in script and '"--header-height"' in script
    assert "ResizeObserver" in script, "a label that wraps changes a height, not the window"


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
