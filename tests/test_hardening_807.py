"""A cell post changes the one field it names and nothing else the page form declares (#807)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.jobs.models import Company, Industry

pytestmark = pytest.mark.django_db


def test_extra_fields_beside_the_cell_are_ignored(client, user):
    company = Company.objects.create(owner=user, name="Aperture", location="Cambridge")
    client.force_login(user)
    response = client.post(
        reverse("jobs:company_cell", args=[company.pk, "name"]),
        {
            "name": "Aperture Science",
            "new_industries": "Smuggling",
            "location_correction": "yes",
            "location": "Elsewhere",
            "website": "https://example.org",
        },
    )
    assert response.status_code == 200, response.content
    company.refresh_from_db()
    assert company.name == "Aperture Science"
    assert company.location == "Cambridge", "only the named field changed"
    assert company.website == ""
    assert not Industry.objects.for_user(user).filter(name="Smuggling").exists()
