"""The foot of every page: this instance, what it runs on, its source, help, its notice (#212).

It used to say Postulo's slogan and Postulo's version to everybody, and nothing else -- not
the instance's own name, which an administrator sets, nor where its code was, which the
AGPL entitles the users of a modified Postulo to, nor where the help was. What it says now
depends on who is looking: the version is for people signed in, because it is the first
thing a stranger would want in order to know which advisories apply.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo import __version__
from postulo.core import context_processors
from postulo.core.models import SiteSettings

pytestmark = pytest.mark.django_db

UPSTREAM = "https://source.tiagoagueda.com/postulo/postulo"


def footer_of(response) -> str:
    assert response.status_code == 200, response.status_code
    html = response.content.decode()
    return html[html.index("<footer") : html.index("</footer>") + len("</footer>")]


def links_in(footer: str) -> dict[str, str]:
    """Each link's text, whitespace folded, mapped to its whole tag."""
    found = {}
    for match in re.finditer(r"(<a\b[^>]*>)(.*?)</a>", footer, re.DOTALL):
        found[" ".join(match.group(2).split())] = match.group(1)
    return found


def href(tag: str) -> str:
    return re.search(r'href="([^"]*)"', tag).group(1)


# ------------------------------------------------------------------ who sees what


def test_a_stranger_is_told_what_runs_here_but_not_which_version(client):
    footer = footer_of(client.get(reverse("core:home")))

    assert "Powered by Postulo" in footer
    assert __version__ not in footer, "the exact version is for people signed in"
    assert "data-version" not in footer
    assert set(links_in(footer)) == {"Source code", "Help"}


def test_somebody_signed_in_gets_the_version_linked_to_its_release_notes(client, user):
    client.force_login(user)
    links = links_in(footer_of(client.get(reverse("core:home"))))

    version = links[f"Powered by Postulo {__version__}"]
    assert "data-version" in version
    assert href(version) == f"{UPSTREAM}/releases/tag/v{__version__}"
    assert set(links) == {f"Powered by Postulo {__version__}", "Source code", "Help"}


def test_the_release_notes_are_named_by_the_tag_a_release_is_given():
    """`v` + `__version__`, which `release_tools.py check` holds the tag to."""
    assert context_processors.release_notes_url().endswith(f"/releases/tag/v{__version__}")


@pytest.mark.parametrize(
    "path",
    ["/accounts/login/", "/accounts/password/reset/", "/accounts/signup/"],
)
def test_allauths_pages_have_the_same_foot(client, path):
    footer = footer_of(client.get(path, follow=True))
    assert "Powered by Postulo" in footer and "Source code" in footer
    assert __version__ not in footer


def test_the_second_factor_page_has_it_too_and_still_no_version(client):
    """Half signed in: the password was right and the code is still to come. Not signed in
    yet, so not told the version either."""
    from allauth.account.models import EmailAddress
    from allauth.mfa.totp.internal.auth import TOTP
    from django.contrib.auth import get_user_model

    person = get_user_model().objects.create_user(
        email="alex@example.org", password="a-fairly-long-password-42", username="alex"
    )
    EmailAddress.objects.create(user=person, email=person.email, verified=True, primary=True)
    TOTP.activate(person, "JBSWY3DPEHPK3PXP")

    signed = client.post(
        reverse("account_login"), {"login": "alex", "password": "a-fairly-long-password-42"}
    )
    assert signed.url == reverse("mfa_authenticate"), "the password alone is not enough"
    response = client.get(reverse("mfa_authenticate"))

    footer = footer_of(response)
    assert "Source code" in footer
    assert __version__ not in footer


def test_the_signed_in_allauth_pages_carry_the_version(client, user):
    client.force_login(user)
    footer = footer_of(client.get("/accounts/password/change/"))
    assert f"Powered by Postulo {__version__}" in footer


# ------------------------------------------------------------------- this instance


def test_an_instance_nobody_named_keeps_postulos_slogan(client):
    footer = footer_of(client.get(reverse("core:home")))
    assert "your job search, on your server" in footer


def test_a_named_instance_says_its_own_name_instead(client):
    SiteSettings.objects.create(pk=1, instance_name="Chercheurs d'emploi")

    footer = footer_of(client.get(reverse("core:home")))

    assert "<bdi>Chercheurs d&#x27;emploi</bdi>" in footer
    assert "your job search, on your server" not in footer


def test_a_tagline_follows_the_name(client):
    SiteSettings.objects.create(pk=1, instance_name="Acme", tagline="وظائف للجميع")

    footer = footer_of(client.get(reverse("core:home")))

    assert "<bdi>Acme</bdi> — <bdi>وظائف للجميع</bdi>" in footer
    assert "your job search, on your server" not in footer


def test_a_tagline_on_an_unnamed_instance_replaces_the_slogan_too(client):
    SiteSettings.objects.create(pk=1, tagline="For the Tuesday group")

    footer = footer_of(client.get(reverse("core:home")))

    assert "<bdi>Postulo</bdi> — <bdi>For the Tuesday group</bdi>" in footer
    assert "your job search, on your server" not in footer


def test_what_an_administrator_typed_is_escaped(client):
    SiteSettings.objects.create(pk=1, instance_name="<b>Acme</b>", tagline="<script>x</script>")

    footer = footer_of(client.get(reverse("core:home")))

    assert "<b>" not in footer and "<script>" not in footer


# ------------------------------------------------------------ the operator's addresses


def test_the_source_is_upstream_until_the_operator_says_otherwise(client, settings):
    assert href(links_in(footer_of(client.get(reverse("core:home"))))["Source code"]) == UPSTREAM

    settings.POSTULO_SOURCE_URL = "https://git.example.org/acme/postulo"
    footer = footer_of(client.get(reverse("core:home")))

    assert href(links_in(footer)["Source code"]) == "https://git.example.org/acme/postulo"


def test_help_is_the_documentation(client):
    footer = footer_of(client.get(reverse("core:home")))
    assert href(links_in(footer)["Help"]) == f"{UPSTREAM}/wiki"


def test_a_legal_notice_appears_only_when_it_is_set(client, user, settings):
    assert "Legal notice" not in footer_of(client.get(reverse("core:home")))

    settings.POSTULO_LEGAL_NOTICE_URL = "https://example.org/impressum"
    for signed_in in (False, True):
        if signed_in:
            client.force_login(user)
        links = links_in(footer_of(client.get(reverse("core:home"))))
        assert href(links["Legal notice"]) == "https://example.org/impressum", signed_in


# -------------------------------------------------------------- what it must not be


def test_every_address_is_marked_as_leaving_the_instance(client, user, settings):
    settings.POSTULO_LEGAL_NOTICE_URL = "https://example.org/impressum"
    client.force_login(user)

    links = links_in(footer_of(client.get(reverse("core:home"))))

    assert len(links) == 4
    for text, tag in links.items():
        assert 'rel="noopener noreferrer external"' in tag, text


def test_its_links_are_a_landmark_of_their_own_with_a_name(client):
    footer = footer_of(client.get(reverse("core:home")))
    assert re.search(r'<nav aria-label="[^"]+">', footer)
    assert footer.count("<nav") == 1


def test_nothing_is_fetched_and_nothing_asks_for_money(client, user, settings):
    """No badge, no remote picture, nothing the policy would have to allow -- and none of
    the ask the README makes and the application never repeats (#172)."""
    settings.POSTULO_LEGAL_NOTICE_URL = "https://example.org/impressum"
    client.force_login(user)

    footer = footer_of(client.get(reverse("core:home"))).lower()

    for fetches in ("<img", "<script", "<iframe", "<link", "src="):
        assert fetches not in footer, fetches
    for ask in ("coffee", "donat", "sponsor", "support", "fund"):
        assert ask not in footer, ask


def test_it_spans_the_screen_and_stays_quiet(client):
    footer = footer_of(client.get(reverse("core:home")))
    opening = footer[: footer.index(">") + 1]
    assert "text-xs" in opening and "text-ink-500" in opening
    assert "max-w-" not in footer
