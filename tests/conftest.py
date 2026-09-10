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
def _no_inherited_language():
    """Every test starts in the instance's own language, whatever the last one left active.

    `LocaleMiddleware` activates a language per request and nothing deactivates it
    afterwards, so a test that signs in as somebody reading Postulo in Portuguese leaves
    Portuguese active in the thread for every test that follows. It shows up as a test
    asserting an English string and being handed a translated one -- passing alone, failing
    in company, and failing differently depending on which tests ran before it, which is the
    worst shape a failure can have.

    Reset after rather than before, so a test that activates a language on purpose is left
    alone while it runs and cleans up after itself either way.
    """
    from django.utils import translation

    yield
    translation.deactivate()


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
