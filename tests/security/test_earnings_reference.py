"""The earnings card on a shared listing follows the reader, never the owner (#1257, #896).

Whether an earnings source is on is decided per person. A listing can be shared, so its page
is read by somebody who is not its owner: what that reader is shown is what is on for *them*.
The owner having switched a source off hides nothing from the reader, and, the case that
matters, the owner having it on shows nothing to a reader for whom it is off.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from postulo.applications.services import create_listing
from postulo.core import earnings
from postulo.core.memo import forget_decisions
from postulo.jobs.models import Company, JobPosting, Share
from postulo.plugins import api, registry
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

CARD = "data-earnings-reference"


@pytest.fixture
def office(monkeypatch):
    """An earnings source, registered as one Postulo ships; what it was asked is returned."""
    asked = []

    def figures(isco_code, country, period="", currency=""):
        asked.append(country)
        return [
            api.EarningsFigure(
                measure="mean",
                amount=Decimal("36066"),
                currency="EUR",
                period="year",
                basis="gross",
                year="2022",
                group="Professionals (major group 2)",
            )
        ]

    source = api.EarningsSource(
        key="office",
        label="Statistics Office",
        countries=("PT",),
        offline=True,
        licence="CC BY 4.0",
        figures=figures,
    )
    plugin = type("Office", (), {"name": "office", "kind": earnings.KIND, "sources": (source,)})
    monkeypatch.setitem(registry._builtin, earnings.KIND, [plugin])
    registry._cache.pop(earnings.KIND, None)
    yield asked
    registry._cache.pop(earnings.KIND, None)


@pytest.fixture
def shared(user, other_user):
    """``other_user`` owns a listing in Lisbon; ``user`` was given it, and its company."""
    company = Company.objects.create(owner=other_user, name="Shared Co")
    posting = create_listing(
        other_user, company=company, posting_data={"title": "Shared role", "location": "Lisboa, PT"}
    )
    JobPosting.objects.filter(pk=posting.pk).update(isco_code="2512")
    Share.objects.create(company=company, grantee_user=user, scope=Share.Scope.READ)
    Share.objects.create(posting=posting, grantee_user=user, scope=Share.Scope.READ)
    return posting


def off_for(person) -> None:
    PluginPolicy.objects.create(plugin="office", person=person, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()


def page(client, person, posting) -> str:
    client.force_login(person)
    response = client.get(reverse("jobs:posting_detail", args=[posting.pk]))
    assert response.status_code == 200
    forget_decisions()
    return response.content.decode()


def test_a_reader_with_the_source_off_sees_no_card_on_a_listing_whose_owner_has_it_on(
    client, user, other_user, office, shared
):
    off_for(user)

    assert CARD in page(client, other_user, shared), "the owner has it on"
    office.clear()
    assert CARD not in page(client, user, shared)
    assert office == [], "and the source was not asked for the reader"


def test_a_reader_with_the_source_on_sees_the_card_though_the_owner_switched_it_off(
    client, user, other_user, office, shared
):
    off_for(other_user)

    assert CARD not in page(client, other_user, shared)
    assert CARD in page(client, user, shared)


def test_a_listing_that_was_not_shared_is_not_found_and_asks_nothing(
    client, user, other_user, office, shared
):
    Share.objects.all().delete()
    client.force_login(user)

    response = client.get(reverse("jobs:posting_detail", args=[shared.pk]))

    assert response.status_code == 404 and office == []
