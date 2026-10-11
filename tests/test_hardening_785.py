"""The built-in local store cannot be connected by hand and copied to (#785)."""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.documents import archiving
from postulo.documents.models import DocumentCopy, UploadedDocument
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


def test_the_address_for_making_a_local_store_connection_answers_404(client, user):
    client.force_login(user)

    assert client.get("/settings/connections/add/store/local/").status_code == 404
    assert client.post("/settings/connections/add/store/local/", {"label": "x"}).status_code == 404
    assert not Connection.objects.filter(plugin="local").exists()


def test_a_connection_to_the_local_store_that_exists_anyway_is_never_copied_to(user):
    connection = Connection.objects.create(
        owner=user, kind="store", plugin="local", label="Mine", config={}
    )
    document = UploadedDocument.objects.create(
        owner=user, title="CV", kind="cv", file=ContentFile(b"%PDF-1.7", name="cv.pdf")
    )

    assert archiving.schedule_copies(document, connections=[connection]) == []
    assert archiving.backfill(connection) == 0
    assert not DocumentCopy.objects.exists()


def test_the_pick_page_still_offers_the_stores_that_need_a_connection(client, user):
    client.force_login(user)

    page = client.get(reverse("connections:pick"))

    assert page.status_code == 200
