"""A European Digital Credential: kept as the file it is, and read for what it states (#670).

The fixtures are the European Learning Model's own samples (`tests/data/edc/README.md`): a
sealed certificate of participation, a micro-credential sealed and unsealed, and the data
model's example in two languages.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.documents import credentials, integrity
from postulo.documents.models import UploadedDocument

EDC = Path(__file__).parent / "data" / "edc"
CERTIFICATE = EDC / "github-ELM-Sample-CertOfPart-signed.jsonld"
MICRO_SEALED = EDC / "github-ELM-Sample-MC-Annex1-signed.jsonld"
MICRO_BARE = EDC / "github-ELM-Sample-MC-Annex1-unsigned.jsonld"
EXAMPLE = EDC / "github-ELM-rdf-credential-sample.jsonld"
ALL = (CERTIFICATE, MICRO_SEALED, MICRO_BARE, EXAMPLE)

pytestmark = pytest.mark.django_db

#: The start of a credential in the old XML of the European Learning Model v2.
OLD_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<europassCredential xmlns="http://data.europa.eu/europass/model/credentials/v0.3.0" '
    b'id="urn:credential:1"><title>Old</title></europassCredential>'
)


def _upload(client, path_or_bytes, *, name: str = "", kind: str = "certificate"):
    data = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    return client.post(
        reverse("documents:upload_create"),
        {
            "title": "My credential",
            "kind": kind,
            "notes": "",
            "file": SimpleUploadedFile(name or "credential.jsonld", data),
        },
    )


# ----------------------------------------------------------- what a credential is


@pytest.mark.parametrize("path", ALL, ids=lambda path: path.name)
def test_every_sample_is_recognised_as_a_credential(path):
    found = credentials.credential_in(path.read_bytes())
    assert found is not None
    assert "EuropeanDigitalCredential" in credentials.types_of(found["type"])


def test_the_sealed_and_the_bare_shapes_hold_the_same_credential():
    sealed = credentials.credential_in(MICRO_SEALED.read_bytes())
    bare = credentials.credential_in(MICRO_BARE.read_bytes())
    assert (
        sealed["credentialSubject"]["hasClaim"]["title"]
        == (bare["credentialSubject"]["hasClaim"]["title"])
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"[]",
        b'{"type": "Something"}',
        b'{"payload": "not json", "signatures": []}',
        b'{"payload": "{\\"type\\": \\"Other\\"}", "signatures": []}',
        b"\xff\xfe{}",
        b'{"a":' * 100 + b"1" + b"}" * 100,
        b'{"type": "VerifiableCredential", "x": "' + b"a" * credentials.MAX_BYTES + b'"}',
    ],
    ids=["empty", "text", "list", "other", "payload", "other-payload", "utf16", "deep", "huge"],
)
def test_anything_else_is_not_a_credential(data):
    assert credentials.credential_in(data) is None


def test_the_old_xml_is_recognised_only_to_be_refused():
    assert credentials.is_old_credential(OLD_XML)
    assert not credentials.is_old_credential(b"<?xml version='1.0'?><Candidate/>")
    assert not credentials.is_old_credential(CERTIFICATE.read_bytes())


def test_integrity_judges_a_credential_by_what_it_holds():
    assert integrity.problem_with_bytes("jsonld", CERTIFICATE.read_bytes()) is None
    assert integrity.problem_with_bytes("json", MICRO_BARE.read_bytes()) is None
    refusal = integrity.problem_with_bytes("json", b'{"name": "a CV, not a credential"}')
    assert refusal and "European Digital Credential" in refusal


# ---------------------------------------------------------------- kept as it is


@pytest.mark.parametrize("kind", ["certificate", "diploma", "diploma_supplement"])
def test_a_credential_is_kept_byte_for_byte_as_a_proof(client, user, kind):
    client.force_login(user)
    data = MICRO_SEALED.read_bytes()

    response = _upload(client, MICRO_SEALED, kind=kind)

    assert response.status_code == 302, response.content.decode()[:2000]
    document = UploadedDocument.objects.get(owner=user)
    assert document.kind == kind
    assert document.is_credential
    assert document.checksum == hashlib.sha256(data).hexdigest()
    assert document.size == len(data)
    with document.file.open("rb") as handle:
        assert handle.read() == data, "not a byte changed: the seal covers exact bytes"


def test_a_credential_is_not_kept_as_anything_but_a_proof(client, user):
    client.force_login(user)
    response = _upload(client, CERTIFICATE, kind="cv")
    assert response.status_code == 200
    assert "certificate, a diploma or a diploma supplement" in response.content.decode()
    assert not UploadedDocument.objects.exists()


def test_an_existing_credential_cannot_be_retyped_as_something_else(client, user):
    document = UploadedDocument.objects.create(
        owner=user,
        title="Credential",
        kind="certificate",
        file=SimpleUploadedFile("c.jsonld", CERTIFICATE.read_bytes()),
    )
    client.force_login(user)
    response = client.post(
        reverse("documents:upload_update", args=[document.pk]),
        {"title": "Credential", "kind": "reference", "notes": ""},
    )
    assert response.status_code == 200
    document.refresh_from_db()
    assert document.kind == "certificate"


def test_json_that_is_not_a_credential_is_refused(client, user):
    client.force_login(user)
    response = _upload(client, json.dumps({"name": "x"}).encode(), name="cv.json")
    assert response.status_code == 200
    assert not UploadedDocument.objects.exists()


def test_a_hostile_credential_is_refused_and_not_kept(client, user):
    client.force_login(user)
    deep = b'{"type": ["VerifiableCredential"], "x": ' + b"[" * 200 + b"]" * 200 + b"}"
    response = _upload(client, deep)
    assert response.status_code == 200
    assert not UploadedDocument.objects.exists()


def test_the_old_xml_is_refused_with_where_to_find_the_new_one(client, user):
    client.force_login(user)
    response = _upload(client, OLD_XML, name="credential.xml")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Download it again from your Europass wallet" in html
    assert "not allowed" not in html, "one refusal, the one that can be acted on"
    assert not UploadedDocument.objects.exists()


def test_any_other_xml_is_refused_as_before(client, user):
    client.force_login(user)
    response = _upload(client, b"<?xml version='1.0'?><a/>", name="notes.xml")
    html = response.content.decode()
    assert "Europass wallet" not in html
    assert not UploadedDocument.objects.exists()


# ------------------------------------------------------- said, delivered, owned


@pytest.fixture
def credential(user):
    return UploadedDocument.objects.create(
        owner=user,
        title="Micro-credential",
        kind="certificate",
        file=SimpleUploadedFile("micro.jsonld", MICRO_SEALED.read_bytes()),
    )


def test_the_files_page_says_the_seal_was_not_checked(client, user, credential):
    UploadedDocument.objects.create(
        owner=user, title="Scan", kind="certificate", file=SimpleUploadedFile("s.txt", b"scan")
    )
    client.force_login(user)
    html = client.get(reverse("documents:upload_list")).content.decode()
    assert html.count('data-seal="unchecked"') == 1, "on the credential, and on nothing else"
    assert "Seal not checked by Postulo" in html
    for claim in ("verified", "Verified", "authentic", "Authentic"):
        assert claim not in html


def test_a_credential_is_delivered_as_an_attachment_to_its_owner_only(
    client, user, other_user, credential
):
    url = reverse("documents:upload_download", args=[credential.pk])
    client.force_login(other_user)
    assert client.get(url).status_code == 404

    client.force_login(user)
    response = client.get(url)
    try:
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment;")
        assert b"".join(response.streaming_content) == MICRO_SEALED.read_bytes()
    finally:
        response.close()


def test_the_api_says_the_seal_was_not_checked(client, user, credential):
    from postulo.api.models import ApiToken

    UploadedDocument.objects.create(
        owner=user, title="Scan", kind="certificate", file=SimpleUploadedFile("s.txt", b"scan")
    )
    token = ApiToken.issue(user, "Agent", scopes=("read",))[1]
    response = client.get("/api/v1/documents?source=upload", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 200, response.content
    seals = {row["title"]: row["seal"] for row in response.json()["items"]}
    assert seals == {"Micro-credential": "unchecked", "Scan": None}
