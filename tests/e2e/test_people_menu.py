"""The People table, at a width where it used to scroll sideways and at one where it cannot.

Server settings → People needed 967 pixels in a card that is about 730 whatever the window
does, so it scrolled at every width — a 1440-pixel desktop as much as a phone. The actions
column was 335 of those pixels: four buttons spelled out in words, laid end to end.

They are a menu now, and below the md breakpoint each row becomes a card. Both halves need a
browser to be checked honestly: whether the table actually stops overflowing is a question
about layout, and whether a menu built from `<details>` opens and submits is a question about
events.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


@pytest.fixture
def administrator(db):
    """An administrator, and somebody to administer."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    User = get_user_model()
    admin = User.objects.create_user(
        email=EMAIL, password=PASSWORD, first_name="Alex", last_name="Morgan", is_staff=True
    )
    EmailAddress.objects.create(user=admin, email=EMAIL, verified=True, primary=True)
    other = User.objects.create_user(
        email="jordan.mccafferty@a-fairly-long-domain.example",
        password=PASSWORD,
        first_name="Jordan",
        last_name="McCafferty",
    )
    EmailAddress.objects.create(user=other, email=other.email, verified=True, primary=True)
    return admin, other


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()


def overflow(page: Page) -> int:
    """How far the table sticks out of the card that holds it."""
    return page.evaluate("""() => {
        const card = document.querySelector('.card.overflow-x-auto');
        return Math.round(card.scrollWidth - card.clientWidth);
    }""")


@pytest.mark.parametrize("width", [1440, 1024, 768])
def test_the_table_no_longer_scrolls_sideways(page: Page, live_server, administrator, width):
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}/server/people/")

    assert overflow(page) == 0, f"still {overflow(page)}px of sideways scroll at {width}"


def test_a_phone_gets_cards_instead_of_columns(page: Page, live_server, administrator):
    """Five columns have nowhere to go at 390 pixels, so the rows stop being rows."""
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}/server/people/")

    assert overflow(page) == 0
    # The header row is what goes away; the labels on each cell replace it.
    expect(page.locator("thead")).to_be_hidden()
    assert page.locator("td[data-label='Role']").first.evaluate(
        "cell => getComputedStyle(cell, '::before').content.includes('Role')"
    )


def test_the_menu_opens_and_its_actions_still_work(page: Page, live_server, administrator):
    _admin, other = administrator
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/people/")

    row = page.locator(f"tr[data-person='{other.username}']")
    menu = row.locator("details[data-menu]")

    expect(row.get_by_role("link", name="Change username")).to_be_hidden()
    menu.locator("summary").click()
    expect(row.get_by_role("link", name="Change username")).to_be_visible()

    row.get_by_role("button", name="Make administrator").click()

    other.refresh_from_db()
    assert other.is_staff, "the form inside the menu did not submit"


def test_only_one_menu_is_ever_open(page: Page, live_server, administrator):
    """Two panels overlapping each other is nobody's idea of a menu."""
    admin, other = administrator
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/people/")

    page.locator(f"tr[data-person='{admin.username}'] details[data-menu] summary").click()
    page.locator("body").click(position={"x": 5, "y": 5})
    page.locator(f"tr[data-person='{other.username}'] details[data-menu] summary").click()

    expect(page.locator("details[data-menu][open]")).to_have_count(1)
