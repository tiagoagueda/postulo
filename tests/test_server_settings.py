"""Server settings: the instance, for administrators, with the environment still winning."""

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import site
from postulo.core.models import SiteSettings
from postulo.core.server_sections import SECTIONS

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org",
        password=PASSWORD,
        username="admin-one",
        first_name="Ada",
        last_name="Min",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture(autouse=True)
def _no_policy_in_the_environment(monkeypatch):
    for variable in site.ENV_OVERRIDES.values():
        monkeypatch.delenv(variable, raising=False)


SECTION_URLS = [section.url_name for section in SECTIONS]


# --------------------------------------------------------------------- access


@pytest.mark.parametrize("url_name", SECTION_URLS)
def test_every_section_is_for_administrators_only(client, user, url_name):
    assert client.get(reverse(url_name)).status_code == 302, "anonymous: to the sign-in page"
    client.force_login(user)
    assert client.get(reverse(url_name)).status_code == 403


@pytest.mark.parametrize("url_name", SECTION_URLS)
def test_every_section_renders_inside_the_sidebar(client, admin, url_name):
    client.force_login(admin)
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Server settings" in html
    for section in SECTIONS:
        assert reverse(section.url_name) in html
    assert html.count('aria-current="page"') == 1


def test_the_menu_shows_server_settings_to_administrators_only(client, user, admin):
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert reverse("server:index") not in html
    assert reverse("accounts:invite_list") not in html, "no longer in the main navigation"
    client.force_login(admin)
    html = client.get(reverse("core:home")).content.decode()
    header = html[: html.index("</header>")]
    assert reverse("server:index") in header
    assert reverse("accounts:invite_list") not in header
    assert client.get(reverse("server:index")).url == reverse("server:overview")


def test_invitations_now_live_under_people(client, admin):
    client.force_login(admin)
    html = client.get(reverse("server:people")).content.decode()
    assert reverse("accounts:invite_list") in html
    html = client.get(reverse("accounts:invite_list")).content.decode()
    assert "Server settings" in html and html.count('aria-current="page"') == 1


# ------------------------------------------------------------------- overview


def test_the_overview_says_what_is_running(client, admin):
    from postulo import __version__

    client.force_login(admin)
    html = client.get(reverse("server:overview")).content.decode()
    assert f"<dd data-version>{__version__}</dd>" in html
    assert "sqlite" in html
    assert reverse("admin:index") in html and reverse("core:healthz") in html
    assert "none yet" in html, "no backup has been taken"


# --------------------------------------------------------------------- people


def test_people_lists_accounts_and_makes_or_unmakes_administrators(client, admin, user):
    client.force_login(admin)
    html = client.get(reverse("server:people")).content.decode()
    assert 'data-person="applicant"' in html and "Member" in html
    assert "1 administrator" in html

    client.post(reverse("server:person_admin", args=[user.pk]))
    user.refresh_from_db()
    assert user.is_staff and user.is_superuser

    client.post(reverse("server:person_admin", args=[user.pk]))
    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser


def test_the_last_administrator_cannot_be_removed_or_deactivated(client, admin):
    client.force_login(admin)
    response = client.post(reverse("server:person_admin", args=[admin.pk]), follow=True)
    assert "last administrator" in response.content.decode()
    admin.refresh_from_db()
    assert admin.is_staff

    response = client.post(reverse("server:person_active", args=[admin.pk]), follow=True)
    assert "cannot deactivate the account you are signed in with" in response.content.decode()
    admin.refresh_from_db()
    assert admin.is_active


def test_deactivating_keeps_the_data_and_blocks_the_sign_in(client, admin, user):
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    client.force_login(admin)
    client.post(reverse("server:person_active", args=[user.pk]))
    user.refresh_from_db()
    assert not user.is_active
    assert Profile.objects.filter(user=user).exists()

    other = client.__class__()
    response = other.post(reverse("account_login"), {"login": "applicant", "password": PASSWORD})
    assert response.status_code == 200, "the form comes back; no session"

    client.post(reverse("server:person_active", args=[user.pk]))
    user.refresh_from_db()
    assert user.is_active


# ----------------------------------------------------------------- the policy


def test_registration_follows_the_page_unless_the_environment_pins_it(
    client, admin, user, settings, monkeypatch
):
    assert site.registration_open() is False
    client.force_login(admin)
    response = client.post(reverse("server:signin"), {"registration_open": "true"})
    assert response.status_code == 302
    assert SiteSettings.get().registration_open is True
    assert site.registration_open() is True
    assert SiteSettings.get().updated_by == admin

    # The landing page and allauth follow it.
    anonymous = client.__class__()
    assert reverse("account_signup") in anonymous.get(reverse("core:home")).content.decode()
    assert anonymous.get(reverse("account_signup")).status_code == 200

    # The environment wins once it speaks, and the page says so.
    monkeypatch.setenv("POSTULO_REGISTRATION_OPEN", "false")
    settings.POSTULO_REGISTRATION_OPEN = False
    assert site.registration_open() is False
    html = client.get(reverse("server:signin")).content.decode()
    assert 'data-pinned="registration_open"' in html and "POSTULO_REGISTRATION_OPEN" in html
    assert 'name="registration_open"' not in html


def test_unset_means_the_codes_default_which_a_test_may_change(settings):
    assert SiteSettings.get().registration_open is None
    settings.POSTULO_REGISTRATION_OPEN = True
    assert site.registration_open() is True


def test_capture_policy_reaches_the_fetcher(client, admin):
    from postulo.plugins.fetching import robots_allow

    client.force_login(admin)
    client.post(reverse("server:capture"), {"capture_ignore_robots": "true"})
    assert site.capture_ignore_robots() is True
    assert robots_allow("https://example.org/anything") is True
    html = client.get(reverse("server:capture")).content.decode()
    assert 'data-effective="ignored"' in html


def test_defaults_name_the_instance_and_seed_new_accounts(client, admin):
    client.force_login(admin)
    response = client.post(
        reverse("server:defaults"),
        {
            "instance_name": "Jobs at Home",
            "tagline": "Where the search lives.",
            "default_language": "pt-pt",
            "default_time_zone": "Europe/Lisbon",
        },
    )
    assert response.status_code == 302
    html = client.get(reverse("core:home")).content.decode()
    assert "<title>Jobs at Home</title>" in html or "Jobs at Home" in html
    anonymous = client.__class__()
    landing = anonymous.get(reverse("core:home")).content.decode()
    assert "Jobs at Home" in landing and "Where the search lives." in landing

    newcomer = User.objects.create_user(email="new@example.org", password=PASSWORD)
    profile = Profile.objects.get(user=newcomer)
    assert profile.language == "pt-pt" and profile.time_zone == "Europe/Lisbon"
    assert site.default_time_zone() == "Europe/Lisbon"


def test_the_instance_default_time_zone_applies_to_a_profile_without_one(client, user):
    from django.utils import timezone

    row = SiteSettings.get()
    row.default_time_zone = "Pacific/Auckland"
    row.save()
    client.force_login(user)
    client.get(reverse("core:home"))
    assert timezone.get_current_timezone_name() == "Pacific/Auckland"


# ---------------------------------------------------------------------- email


def test_a_test_message_proves_the_mail_settings(client, admin):
    client.force_login(admin)
    html = client.get(reverse("server:email")).content.decode()
    assert "locmem" in html and 'name="to"' in html
    response = client.post(reverse("server:email_test"), {"to": "prove@example.org"}, follow=True)
    assert "Sent to prove@example.org" in response.content.decode()
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["prove@example.org"]

    response = client.post(reverse("server:email_test"), {"to": "not-an-address"})
    assert response.status_code == 200 and "to" in response.context["form"].errors


# -------------------------------------------------------------------- plugins


def test_plugins_lists_the_built_in_sources(client, admin):
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "built in" in html and "postulo.sources" in html
    assert 'data-source="schema.org"' in html


# --------------------------------------------------------- the first account


def test_an_empty_instance_offers_sign_up_and_the_first_account_administers(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    assert site.signup_open_now() is True
    assert reverse("account_signup") in client.get(reverse("core:home")).content.decode()

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "First",
            "last_name": "Person",
            "username": "first",
            "email": "first@example.org",
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )
    assert response.status_code == 302
    first = User.objects.get(username="first")
    assert first.is_staff and first.is_superuser
    assert EmailAddress.objects.get(user=first).verified, "nobody else could have vouched"
    assert response.url == "/", "signed in straight away"
    assert not mail.outbox

    # Now it is not empty: the door closes again, and the next person is a member.
    assert site.signup_open_now() is False
    client.logout()
    response = client.get(reverse("account_signup"))
    assert response.status_code == 200
    assert b'name="password1"' not in response.content, "the form is not offered"
