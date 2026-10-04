"""The career preview draws every section the overview does, links included (#612)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.resume.models import Link

pytestmark = pytest.mark.django_db


def test_preview_shows_the_links(client, user):
    Link.objects.create(owner=user, title="My portfolio", url="https://www.example.org/me")
    client.force_login(user)

    html = client.get(reverse("resume:preview")).content.decode()

    assert "My portfolio" in html
    assert "example.org" in html
