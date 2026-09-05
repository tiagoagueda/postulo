"""The demonstration data: fictional, deterministic, and shaped so Insights has a story."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from postulo.applications import analytics
from postulo.applications.models import Application, Status
from postulo.documents.models import CV, CoverLetter, UploadedDocument
from postulo.jobs.models import Capture, Company
from postulo.resume.models import Experience


@pytest.fixture
def seeded(db, user):
    call_command("seed_demo", user.email, "--no-pdf", verbosity=0)
    return user


def test_it_fills_every_part_of_the_application(seeded):
    assert Company.objects.for_user(seeded).count() >= 10
    assert Application.objects.for_user(seeded).count() >= 25
    assert Experience.objects.for_user(seeded).count() == 3
    assert CV.objects.for_user(seeded).count() == 2
    assert CoverLetter.objects.for_user(seeded).count() == 2
    assert UploadedDocument.objects.for_user(seeded).count() == 1
    assert Capture.objects.for_user(seeded).count() == 2


def test_the_search_has_a_shape_insights_can_read(seeded):
    """Enough applications, ending enough different ways, for every figure to be non-trivial."""
    insights = analytics.build(seeded)
    stages = {stage.status: stage.count for stage in insights.funnel}

    assert insights.applied >= 20
    assert not insights.sample_is_small
    assert stages[Status.INTERVIEWING] >= 3, "interviews that ended in rejection still count"
    assert stages[Status.OFFER] == 1
    assert insights.ghosted == 5
    assert 0 < insights.response_rate < 100
    assert insights.median_days_to_reply is not None
    assert len(insights.sources) >= 3


def test_timelines_are_coherent(seeded):
    """Every settled application reached applied first, and its log says so."""
    for application in Application.objects.for_user(seeded).exclude(status=Status.DRAFT):
        assert application.applied_at is not None
        reached = set(application.events.values_list("to_status", flat=True))
        assert Status.APPLIED in reached
        if not application.is_open:
            assert application.closed_at is not None


def test_it_refuses_to_pile_onto_an_existing_search(seeded):
    with pytest.raises(CommandError, match="already holds"):
        call_command("seed_demo", seeded.email, "--no-pdf", verbosity=0)


def test_reset_starts_again_rather_than_doubling(seeded):
    before = Application.objects.for_user(seeded).count()

    call_command("seed_demo", seeded.email, "--no-pdf", "--reset", verbosity=0)

    assert Application.objects.for_user(seeded).count() == before


def test_the_same_seed_gives_the_same_search(db, django_user_model):
    one = django_user_model.objects.create_user(email="one@example.org", password="x")
    two = django_user_model.objects.create_user(email="two@example.org", password="x")
    call_command("seed_demo", one.email, "--no-pdf", "--seed", "7", verbosity=0)
    call_command("seed_demo", two.email, "--no-pdf", "--seed", "7", verbosity=0)

    titles = lambda u: sorted(  # noqa: E731
        Application.objects.for_user(u).values_list("posting__title", "posting__company__name")
    )
    assert titles(one) == titles(two)


def test_it_creates_the_account_when_asked_to(db, django_user_model):
    call_command("seed_demo", "fresh@example.org", "--no-pdf", "--password", "pw", verbosity=0)

    user = django_user_model.objects.get(email="fresh@example.org")
    assert user.check_password("pw")
    assert Application.objects.for_user(user).exists()
