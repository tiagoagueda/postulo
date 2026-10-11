"""Five Low findings of the security audit of 2026-10-10 (#771, #773, #774, #775, #787)."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------- #787


def test_a_page_for_a_signed_in_person_is_not_kept(client, user):
    client.force_login(user)

    for name in ("accounts:profile", "applications:list", "core:home"):
        page = client.get(reverse(name), follow=True)
        assert "no-store" in page["Cache-Control"], name
