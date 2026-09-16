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


@pytest.fixture(autouse=True)
def _no_inherited_drafts():
    """No test is handed a PDF another test drew (#220).

    `documents.pdf` keeps the last few *draft* renders in the process, keyed by the SHA-256
    of their HTML, so that reloading a report download does not redraw it. A worker is one
    process and so is the suite: without this, a test that stands in for the renderer and a
    test that expects a real one would answer each other across a whole file, depending on
    the order they ran in.
    """
    from postulo.documents import pdf

    pdf.forget_drafts()
    yield
    pdf.forget_drafts()


class InstalledSource:
    """A capture source installed on the instance rather than shipped inside it.

    Since #200 that difference decides whose switch a plugin is: what Postulo ships is the
    administrator's, what was installed is the person's. The registry's third-party loader
    is what says a plugin was installed, so this stands in for one.
    """

    name = "example-source"
    version = "1.0"
    kind = "source"

    def can_handle(self, url: str) -> bool:
        return False

    def parse(self, url: str, html: str):
        return None


@pytest.fixture
def third_party(monkeypatch):
    """The name of a source installed on the instance, registered for the test."""
    from postulo.plugins import registry

    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [InstalledSource()] if kind == "source" else [],
    )
    registry.plugins("source", refresh=True)
    try:
        yield InstalledSource.name
    finally:
        registry._cache.pop("source", None)
