"""The consent that gives the instance's mailbox a token, and who may finish it (#151).

This is the credential that sends password resets. A refresh token for it outlives a
password and is not changed by changing one, and the callback that stores it is a GET a
stranger's page can cause. So the boundary is tested here, apart from the mechanism in
`tests/test_mail_xoauth2.py`:

- only an administrator can start one, and only the administrator who started it can finish
  it;
- a state value made for somebody's own connection cannot finish the instance's, and the
  other way round, even though both come back to one address;
- a state that was tampered with, or has aged past the round trip's allowance, is refused;
- the client secret and the tokens never reach a page, a column or a log in plain text.
"""

from __future__ import annotations

import pytest
from django.core import signing
from django.urls import reverse

from postulo.core import mail_auth
from postulo.core.models import SiteSettings
from postulo.plugins import consent

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def exchange(token_url, *, client_id, client_secret, form):
        calls.append(form)
        return {"access_token": "access-abc", "refresh_token": "refresh-xyz", "expires_in": 3600}

    monkeypatch.setattr(consent, "exchange", exchange)
    return calls


@pytest.fixture
def configured():
    row = SiteSettings.get()
    row.email_auth = "xoauth2"
    row.email_oauth_provider = "google"
    row.email_oauth_grant = "mailbox"
    row.email_oauth_client_id = "app-id"
    row.email_oauth_secrets = {"client_secret": "client-secret-value"}
    row.save()
    return row


def state(user_pk):
    return signing.dumps({"by": user_pk}, salt=mail_auth.CONSENT_SALT)


def finish(client, value, code="c"):
    return client.get(reverse("connections:consent_callback"), {"code": code, "state": value})


def test_somebody_who_is_not_an_administrator_cannot_start_one(client, user, configured):
    client.force_login(user)

    response = client.post(reverse("server:email_consent"))

    assert response.status_code in (302, 403)
    assert "accounts.google.com" not in response.get("Location", "")


def test_a_stranger_cannot_start_one(client, configured):
    response = client.post(reverse("server:email_consent"))

    assert "accounts.google.com" not in response.get("Location", "")


def test_a_non_administrator_cannot_finish_one_even_with_their_own_valid_state(
    client, user, configured, provider
):
    client.force_login(user)

    finish(client, state(user.pk))

    assert provider == [], "the code was never even exchanged"
    assert not SiteSettings.get().has_email_consent


def test_an_administrator_cannot_finish_one_another_started(
    client, admin, django_user_model, configured, provider
):
    other = django_user_model.objects.create_user(
        email="other@example.org", username="other", password="x-long-password-9", is_staff=True
    )
    client.force_login(other)

    finish(client, state(admin.pk))

    assert provider == []


def test_a_state_made_for_a_connection_cannot_finish_the_instances(
    client, admin, configured, provider
):
    """Both come back to one address; a salt each is what keeps them apart."""
    client.force_login(admin)
    for_a_connection = signing.dumps({"connection": 1, "owner": admin.pk})

    finish(client, for_a_connection)

    assert not SiteSettings.get().has_email_consent


def test_the_instances_state_cannot_finish_a_connection(admin):
    """And the other way: a connection's flow will not read the instance's state."""
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = admin

    with pytest.raises(consent.ConsentFailed):
        consent.finish(request, "c", state(admin.pk))


def test_a_tampered_state_is_refused(client, admin, configured, provider):
    client.force_login(admin)

    finish(client, state(admin.pk)[:-2] + "xx")

    assert provider == []


def test_a_state_past_its_allowance_is_refused(client, admin, configured, provider, monkeypatch):
    """A state value found in a log a day later is worth nothing."""
    import time

    made = state(admin.pk)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + consent.STATE_MAX_AGE + 60)
    client.force_login(admin)

    finish(client, made)

    assert provider == []


def test_the_tokens_and_secret_are_never_in_plain_text(client, admin, configured, provider):
    client.force_login(admin)
    finish(client, state(admin.pk))

    row = SiteSettings.get()
    stored = row.email_oauth_secrets_encrypted
    for secret in ("refresh-xyz", "access-abc", "client-secret-value"):
        assert secret not in stored
    html = client.get(reverse("server:email")).content.decode()
    for secret in ("refresh-xyz", "access-abc", "client-secret-value"):
        assert secret not in html


def test_the_tokens_do_not_travel_in_an_export(admin, configured, provider):
    """An export is what somebody leaves with; the instance's mail credentials are not theirs
    to leave with, and the policy row is not in a person's archive at all.
    """
    import zipfile

    from postulo.core import export

    mail_auth.keep_tokens(
        SiteSettings.get(), {"access_token": "access-abc", "refresh_token": "refresh-xyz"}
    )
    archive = zipfile.ZipFile(export.write_archive(admin))
    everything = b"".join(archive.read(name) for name in archive.namelist())

    assert b"refresh-xyz" not in everything
    assert b"client-secret-value" not in everything
