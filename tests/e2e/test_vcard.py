"""Contacts as vCard, in a browser: one contact out of one account and into another (#660).

The walk next door opens the page that offers the files; what it cannot reach is the half
that comes after a file has been chosen, which is a table of cards with a box each, none of
them ticked. So the whole trip is made here: a contact is downloaded from the first account,
the file is read by the second, nothing is ticked and nothing arrives, then a card is ticked
and one contact does -- and the review is held to the accessibility rules like any page.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD
from tests.e2e.signing_in import sign_in

from .test_accessibility import AXE, describe, furnished, violations_on  # noqa: F401

pytestmark = pytest.mark.e2e

SECOND = "sam.rivera@example.org"


@pytest.fixture
def second(db):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(email=SECOND, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=SECOND, verified=True, primary=True)
    return user


def test_a_contact_goes_out_of_one_account_and_into_another(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    second,
    tmp_path,
):
    from postulo.core.models import PhoneNumber
    from postulo.jobs.models import Contact

    base = live_server.url
    cave = Contact.objects.filter(owner=furnished["applicant"], name="Cave Johnson").first()
    PhoneNumber.objects.create(
        owner=furnished["applicant"], holder=cave, number="+351912345678", is_primary=True
    )

    sign_in(page, base, f"/jobs/contacts/{cave.pk}/edit/", email=EMAIL)
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download as a vCard").click()
    download = download_info.value
    assert download.suggested_filename.endswith(".vcf")
    kept = tmp_path / "cave.vcf"
    download.save_as(kept)
    text = kept.read_bytes().decode("utf-8")
    assert text.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\n")
    assert "FN:Cave Johnson" in text and "ORG:Aperture Science" in text
    assert "NOTE" not in text, "the notes stay behind unless they are ticked"

    page.context.clear_cookies()
    sign_in(page, base, "/jobs/contacts/vcard/", email=SECOND)
    page.locator("input[type=file]").set_input_files(str(kept))
    page.locator("[data-vcard-upload] button[type=submit]").click()
    review = page.locator("[data-vcard-review]")
    expect(review).to_be_visible()
    expect(review).to_contain_text("Cave Johnson")
    expect(review).to_contain_text("A contact at Aperture Science")
    boxes = review.locator("input[name=chosen]")
    expect(boxes).to_have_count(1)
    expect(boxes.first).not_to_be_checked()
    if AXE.is_file():
        found = violations_on(page, AXE.read_text(encoding="utf-8"))
        assert not found, describe(page.url, found)

    # Nothing ticked, nothing added.
    page.get_by_role("button", name="Add the cards I ticked").click()
    expect(page.get_by_text("No card was ticked, so nothing was added.")).to_be_visible()
    assert not Contact.objects.filter(owner=second).exists()

    # Ticked, and the contact arrives, under a company made for the name on the card.
    page.goto(f"{base}/jobs/contacts/vcard/")
    expect(review).to_be_visible()
    page.get_by_role("checkbox", name="Cave Johnson").check()
    page.get_by_role("button", name="Add the cards I ticked").click()
    expect(page.get_by_text("1 card was added.")).to_be_visible()
    arrived = Contact.objects.get(owner=second)
    assert arrived.name == "Cave Johnson" and arrived.company.name == "Aperture Science"
    # A number is held by one account on the instance, and the first account holds this one:
    # the card still arrives, and the number is reported as left out.
    expect(page.get_by_text("already recorded on this instance")).to_be_visible()
    assert not arrived.phone_numbers.exists()
    assert Contact.objects.filter(owner=second).count() == 1
