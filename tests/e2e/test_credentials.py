"""A European Digital Credential on the Files list and on its own page, in a browser (#670).

The seal is said, in grey, on both; the edit page says what the credential states; and both
pages pass axe in both themes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e

MICRO = Path(__file__).parents[1] / "data" / "edc" / "github-ELM-Sample-MC-Annex1-signed.jsonld"


@pytest.fixture
def credential(applicant):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from postulo.documents.models import UploadedDocument

    return UploadedDocument.objects.create(
        owner=applicant,
        title="Micro-credential",
        kind="certificate",
        file=SimpleUploadedFile("micro.jsonld", MICRO.read_bytes()),
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_credential_says_its_seal_was_not_checked_and_what_it_states(
    live_server, page: Page, credential, scheme
):
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    axe_source = AXE.read_text(encoding="utf-8")
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)

    page.goto(f"{live_server.url}/documents/files/")
    expect(page.locator('[data-seal="unchecked"]')).to_have_text("Seal not checked by Postulo")
    found = violations_on(page, axe_source)
    assert not found, describe(f"the files list ({scheme})", found)

    page.goto(f"{live_server.url}/documents/files/{credential.pk}/edit/")
    said = page.locator("[data-credential]")
    expect(said.locator('[data-seal="unchecked"]')).to_be_visible()
    expect(said).to_contain_text("Digital micro-credential creation")
    expect(said).to_contain_text("University of Zed")
    expect(said.locator("[data-eqf]")).to_have_text("6")
    found = violations_on(page, axe_source)
    assert not found, describe(f"a credential's page ({scheme})", found)
