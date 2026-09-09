import pytest
from django.contrib.auth import get_user_model

from postulo.core import site


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


@pytest.fixture(autouse=True)
def _no_inherited_environment(monkeypatch):
    """No test inherits the developer's `.env`.

    `site.overridden_by` asks `os.environ` whether a variable is set, and settings are read
    from a `.env` file into `os.environ` at import. So a machine with a `.env` was running a
    different suite from one without: `POSTULO_DEFAULT_FROM_EMAIL=postulo@localhost` in a
    local file pinned the email from-address, and the test that saves one from the interface
    passed in CI and failed on the machine that wrote it.

    Cleared for every test; a test that wants a pinned variable sets it itself, which also
    makes the pinning visible in the test rather than in somebody's untracked file.
    """
    for variable in site.env_variables():
        monkeypatch.delenv(variable, raising=False)
