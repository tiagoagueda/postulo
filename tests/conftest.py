import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def user(db):
    """A saved, ordinary user."""
    return get_user_model().objects.create_user(
        email="applicant@example.org", password="not-a-real-password"
    )


@pytest.fixture
def other_user(db):
    """A second user, for proving that data never leaks between accounts."""
    return get_user_model().objects.create_user(
        email="someone.else@example.org", password="not-a-real-password"
    )
