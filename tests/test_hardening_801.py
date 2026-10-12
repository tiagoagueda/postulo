"""/metrics is never served without a token (#801)."""

from __future__ import annotations

import logging

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


@override_settings(POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="")
def test_enabled_without_a_token_it_refuses_and_says_so(client, caplog):
    with caplog.at_level(logging.ERROR, logger="postulo.core.views_metrics"):
        response = client.get(reverse("core:metrics"))
    assert response.status_code == 503
    assert b"postulo_" not in response.content
    assert any("POSTULO_METRICS_TOKEN" in r.getMessage() for r in caplog.records)


@override_settings(POSTULO_METRICS_ENABLED=True, POSTULO_METRICS_TOKEN="t" * 32)
def test_with_a_token_it_answers_the_right_one_and_refuses_the_rest(client):
    assert client.get(reverse("core:metrics")).status_code == 401
    ok = client.get(reverse("core:metrics"), HTTP_AUTHORIZATION="Bearer " + "t" * 32)
    assert ok.status_code == 200 and b"postulo_" in ok.content


@override_settings(POSTULO_METRICS_ENABLED=False)
def test_off_it_is_a_404(client):
    assert client.get(reverse("core:metrics")).status_code == 404
