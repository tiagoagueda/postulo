"""Browser tests: a real Chromium driven against a live server.

These are excluded from the default run (``-m "not e2e"`` in ``pyproject.toml``) because
they need a browser installed. Run them with::

    uv run playwright install chromium
    uv run pytest -m e2e

CI runs them on every push in their own job, with traces kept on failure.
"""

import os

import pytest

# Playwright's synchronous API drives an event loop inside the test thread, and Django
# refuses ORM calls from a thread that has a running loop unless told otherwise. Here the
# test thread *is* that thread, on purpose, so the guard is switched off for this process.
# It protects production code from accidental blocking in async views; it has nothing to
# protect in a test that is blocking by design.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytest.importorskip("playwright", reason="the browser tests need the e2e dependency group")

EMAIL = "alex.morgan@example.org"
PASSWORD = "correct-horse-battery-staple"  # a test account's password, not a secret


@pytest.fixture
def applicant(db):
    """A person with a verified address and one CV, ready to sign in and send things."""
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from postulo.documents.models import CV

    user = get_user_model().objects.create_user(
        email=EMAIL, password=PASSWORD, first_name="Alex", last_name="Morgan"
    )
    EmailAddress.objects.create(user=user, email=EMAIL, verified=True, primary=True)
    CV.objects.create(owner=user, name="Main CV", headline="Django developer")
    return user
