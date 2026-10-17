"""An address that is not http or https is not kept from a file or from a remote answer (#770)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from postulo.core import export, importer, kind_files, updates
from postulo.core.models import WebLink
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db

SCRIPT = "javascript:alert(document.cookie)"


def archive_of(document):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def exported(user):
    company = Company.objects.create(
        owner=user,
        name="Aperture",
        website="https://aperture.test",
        careers_url="https://aperture.test/jobs",
    )
    JobPosting.objects.create(
        owner=user, company=company, title="Tester", url="https://aperture.test/jobs/1"
    )
    contact = Contact.objects.create(owner=user, company=company, name="Alex")
    WebLink.objects.create(
        owner=user,
        holder=contact,
        kind=WebLink.Kind.WEBSITE,
        url="https://alex.test",
        is_primary=True,
    )
    return json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))


def test_the_walk_blanks_what_is_not_http_in_every_address_field():
    document = {
        "a": [{"url": SCRIPT, "website": "data:text/html,x", "careers_url": "ftp://x/y"}],
        "b": {"external_url": "vbscript:x", "credential_url": " JAVASCRIPT:1 ", "name": SCRIPT},
        "ok": [{"url": "https://fine.example/p", "website": "http://also.example", "url2": SCRIPT}],
    }

    dropped = importer.drop_unsafe_addresses(document)

    assert dropped == 5
    assert document["a"][0] == {"url": "", "website": "", "careers_url": ""}
    assert document["b"]["name"] == SCRIPT, "only address fields are touched"
    assert document["ok"][0]["url"] == "https://fine.example/p"


def test_a_restored_archive_carries_no_script_address(user, other_user):
    document = exported(user)
    company = document["companies"][0]
    company["website"] = SCRIPT
    company["postings"][0]["url"] = SCRIPT
    for contact in company["contacts"]:
        for link in contact.get("web_links", []):
            link["url"] = SCRIPT

    report = importer.load(other_user, archive_of(document))

    assert not Company.objects.filter(owner=other_user, website__startswith="javascript").exists()
    assert not WebLink.objects.filter(owner=other_user, url__startswith="javascript").exists()
    assert not JobPosting.objects.filter(owner=other_user, url__startswith="javascript").exists()
    assert any("were left out" in line for line in report.skipped)


def test_a_company_file_with_a_script_address_is_refused_by_the_plan(user):
    Company.objects.create(owner=user, name="Aperture", website="https://aperture.test")
    document = kind_files.build_document(user, "companies")
    document["companies"][0]["website"] = SCRIPT
    document["companies"][0]["name"] = "Another"
    held = kind_files.read("companies", json.dumps(document).encode())

    plan = kind_files.plan(user, held)

    assert [row["outcome"] for row in plan["sections"][0]["rows"]] == ["refused"]


def test_a_contact_file_leaves_out_a_script_link(user):
    contact = Contact.objects.create(owner=user, name="Alex")
    WebLink.objects.create(
        owner=user,
        holder=contact,
        kind=WebLink.Kind.WEBSITE,
        url="https://alex.test",
        is_primary=True,
    )
    document = kind_files.build_document(user, "contacts")
    document["contacts"][0]["web_links"][0]["url"] = SCRIPT
    document["contacts"][0]["name"] = "Someone Else"
    held = kind_files.read("contacts", json.dumps(document).encode())

    kind_files.apply(user, held)

    assert not WebLink.objects.filter(url__startswith="javascript").exists()


def test_the_update_check_does_not_keep_a_release_address_that_runs_script(monkeypatch, settings):
    settings.POSTULO_UPDATE_CHECK = True
    for page, kept in (
        (SCRIPT, ""),
        ("https://source.example/releases/1", "https://source.example/releases/1"),
    ):
        monkeypatch.setattr(updates, "enabled", lambda: True)
        monkeypatch.setattr(
            updates, "_fetch", lambda source, page=page: {"tag_name": "v9.9.9", "html_url": page}
        )

        updates.check()

        assert updates.status().get("url", kept) == kept


@pytest.mark.parametrize("address", [SCRIPT, "data:text/html,x", "file:///etc/hosts"])
def test_the_plugin_surfaces_link_writer_stores_no_script_address(user, address):
    """The door #770's walk did not reach: `save_web_link`, which a plugin hands
    a card's URL (#991)."""
    from django.core.exceptions import ValidationError

    from postulo.plugins import api

    company = Company.objects.create(owner=user, name="Aperture")
    contact = Contact.objects.create(owner=user, company=company, name="Alex")
    with pytest.raises(ValidationError):
        api.save_web_link(contact, user, WebLink.Kind.WEBSITE, address)
    assert not WebLink.objects.filter(owner=user).exists()
