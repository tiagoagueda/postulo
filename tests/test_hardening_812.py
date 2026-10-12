"""A body larger than anything the instance takes is refused before it is read (#812)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

MB = 1024 * 1024


def post_declaring(client, path, megabytes, **extra):
    return client.post(
        path,
        data="x",
        content_type="multipart/form-data; boundary=b",
        CONTENT_LENGTH=str(megabytes * MB),
        **extra,
    )


def test_a_body_past_the_ceiling_is_413_and_never_read(client, settings):
    settings.POSTULO_UPLOAD_CEILING_MB = 25
    response = post_declaring(client, reverse("documents:upload_create"), 200)
    assert response.status_code == 413


def test_a_body_inside_it_goes_on_to_the_view(client, settings):
    settings.POSTULO_UPLOAD_CEILING_MB = 25
    response = post_declaring(client, reverse("documents:upload_create"), 10)
    assert response.status_code != 413


def test_a_get_is_not_asked(client):
    assert client.get(reverse("core:healthz"), CONTENT_LENGTH=str(900 * MB)).status_code == 200


def test_the_backup_upload_has_the_backup_limit(settings):
    from django.test import RequestFactory

    from postulo.core.middleware import UploadCeilingMiddleware

    settings.POSTULO_UPLOAD_CEILING_MB = 25
    settings.POSTULO_BACKUP_UPLOAD_MAX_MB = 512
    ceiling = UploadCeilingMiddleware._ceiling
    factory = RequestFactory()
    backup = ceiling(factory.post(reverse("server:backup_upload")))
    ordinary = ceiling(factory.post(reverse("documents:upload_create")))
    plugin = ceiling(factory.post(reverse("server:plugin_action")))
    assert backup == 513 * MB and ordinary == 26 * MB and plugin == 65 * MB


def test_an_unreadable_length_is_not_a_refusal(client):
    response = client.post(reverse("core:healthz"), CONTENT_LENGTH="not-a-number")
    assert response.status_code != 413
