"""An archive imported into an account whose profile is already filled in (#375).

Moving to a new instance starts with signing up and filling in *Your details*, and only
then is the archive imported. The profile's primary number, address and links, and an
identifier of a scheme it already holds, must not make the import fail: what the profile
has stays, and what the archive had that was left out is said in the report.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from postulo.accounts.models import PersonIdentifier
from postulo.core import export, importer
from postulo.core.models import PhoneNumber, PostalAddress, WebLink

ORCID = "0000-0002-1825-0097"
OTHER_ORCID = "0000-0001-5109-3700"


def an_archive(document):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def fill(profile, owner, *, site, street, number, orcid):
    WebLink.objects.create(owner=owner, holder=profile, kind="website", url=site, is_primary=True)
    PostalAddress.objects.create(
        owner=owner, holder=profile, street=street, municipality="Porto", is_primary=True
    )
    PhoneNumber.objects.create(owner=owner, holder=profile, number=number, is_primary=True)
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=orcid)


@pytest.fixture
def the_archive(other_user):
    fill(
        other_user.profile,
        other_user,
        site="https://archive.example.org",
        street="1 Archive Street",
        number="+351 912 345 678",
        orcid=OTHER_ORCID,
    )
    document = export.build_document(other_user)
    # The number is unique across the instance: the account the archive came from lets go.
    PhoneNumber.objects.filter(owner=other_user).delete()
    return document


def test_a_profile_with_other_primaries_keeps_them(user, the_archive):
    fill(
        user.profile,
        user,
        site="https://mine.example.org",
        street="2 Own Road",
        number="+351 913 000 111",
        orcid=ORCID,
    )

    report = importer.load(user, an_archive(the_archive))

    profile = user.profile
    assert WebLink.objects.get(owner=user, is_primary=True).url == "https://mine.example.org"
    assert WebLink.objects.filter(owner=user).count() == 2
    assert PostalAddress.objects.get(owner=user, is_primary=True).street == "2 Own Road"
    assert PostalAddress.objects.filter(owner=user).count() == 2
    assert PhoneNumber.objects.get(owner=user, is_primary=True).number == "+351 913 000 111"
    assert PhoneNumber.objects.filter(owner=user).count() == 2
    assert PersonIdentifier.objects.get(profile=profile, scheme="orcid").value == ORCID
    assert any("orcid" in line.lower() for line in report.skipped)


def test_what_the_profile_already_holds_is_skipped_and_said(user, the_archive):
    fill(
        user.profile,
        user,
        site="https://archive.example.org",
        street="1 Archive Street",
        number="+351 912 345 678",
        orcid=OTHER_ORCID,
    )

    report = importer.load(user, an_archive(the_archive))

    assert WebLink.objects.filter(owner=user).count() == 1
    assert PostalAddress.objects.filter(owner=user).count() == 1
    assert PhoneNumber.objects.filter(owner=user).count() == 1
    assert PersonIdentifier.objects.filter(profile=user.profile).count() == 1
    assert any("archive.example.org" in line for line in report.skipped)
    assert any("Archive Street" in line for line in report.skipped)
    # Only the last two digits are named, whoever holds the number (#562).
    assert any("ending 78" in line for line in report.skipped)
