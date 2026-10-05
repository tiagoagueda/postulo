"""A web link is on a service: LinkedIn, a Mastodon server, somebody's Forgejo, or Other (#305).

Four things are held here.

**What each service Postulo ships accepts and refuses**, a table per service. A pattern is
a promise in both directions: a real address refused sends somebody to *Other* for no
reason they can see, and the front page of a site accepted is a profile that is not one.
The tables are real shapes -- a profile, a company page, an organisation, a project, with a
trailing slash, a tracking parameter, a country's subdomain, an accented name -- so a
pattern tightened later fails here on the address it would have cost somebody.

**Other refuses nothing**, and nothing is refused in retrospect: a row stored before there
were services, a row whose service no installed plugin knows any more, and an address on a
known host that does not have the shape, are all still rows.

**The registry is a plugin's to add to.** The list is not in the model, and a package
installed beside Postulo contributes services through the one name on the plugin surface.

**And the service travels**: through the rows on *Your details* and on a contact, the API,
the archive, and the migration that worked it out for the rows already there.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.core import link_services, web_links
from postulo.core.models import WebLink
from postulo.plugins import registry

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1]

SOCIAL = WebLink.Kind.SOCIAL
REPOSITORY = WebLink.Kind.REPOSITORY
WEBSITE = WebLink.Kind.WEBSITE

PROFILE_POST = {"first_name": "Alex", "last_name": "Morgan", "headline": "", "location": ""}


# ------------------------------------------------------ what each shipped service accepts
#
# (accepted, refused). The refused ones are the site's own pages, another host, and no
# path at all; what a service is permissive about is under "accepted".

SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "linkedin": (
        (
            "https://www.linkedin.com/in/alex-morgan-1a2b3c/",
            "https://linkedin.com/in/alex-morgan",
            "http://www.linkedin.com/in/alex-morgan",
            "https://pt.linkedin.com/in/jo%C3%A3o-silva-1a2b3c",  # a country, and an accent
            "https://www.linkedin.com/in/joão-silva/?locale=pt_PT",
            "https://www.linkedin.com/in/alex-morgan/details/experience/",
            "https://www.linkedin.com/company/cr%C3%A9dit-agricole/about/",
            "https://www.linkedin.com/school/universidade-de-lisboa/",
            "https://www.linkedin.com/showcase/aperture-labs",
            "https://www.linkedin.com/pub/alex-morgan/1/2b/3c4",
        ),
        (
            "https://www.linkedin.com/",
            "https://www.linkedin.com/feed/",
            "https://www.linkedin.com/jobs/view/1234567",
            "https://www.linkedin.com/in/",
            "https://evil-linkedin.com/in/alex",
            "https://linkedin.com.example.org/in/alex",
            "ftp://www.linkedin.com/in/alex",
        ),
    ),
    "mastodon": (
        (
            "https://mastodon.social/@alex",
            "https://fosstodon.org/@alex",  # any server
            "https://social.example.org/@alex@fosstodon.org",  # seen from another server
            "https://mastodon.social/users/alex",
            "https://mastodon.social/web/@alex",
            "https://hachyderm.io/@alex/with_replies",
            "https://mastodon.social/@alex?utm_source=cv",
        ),
        (
            "https://mastodon.social/",
            "https://mastodon.social/explore",
            "https://mastodon.social/tags/python",
            "https://mastodon.social/about",
        ),
    ),
    "bluesky": (
        (
            "https://bsky.app/profile/alex.bsky.social",
            "https://bsky.app/profile/alex.example.org/",
            "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx",
            "https://bsky.app/profile/alex.bsky.social/lists",
        ),
        (
            "https://bsky.app/",
            "https://bsky.app/search?q=alex",
            "https://bsky.app/profile/",
            "https://example.org/profile/alex.bsky.social",
        ),
    ),
    "x": (
        (
            "https://x.com/alexmorgan",
            "https://twitter.com/alex_morgan",
            "https://www.twitter.com/alexmorgan/",
            "https://mobile.twitter.com/alexmorgan",
            "https://x.com/@alexmorgan",
            "https://x.com/alexmorgan?lang=pt",
            "https://x.com/alexmorgan/with_replies",
        ),
        (
            "https://x.com/",
            "https://x.com/home",
            "https://x.com/i/flow/login",
            "https://x.com/search?q=alex",
            "https://x.com/a-name-with-hyphens",
            "https://x.com/a_name_longer_than_fifteen",
        ),
    ),
    "xing": (
        (
            "https://www.xing.com/profile/Alex_Morgan",
            "https://www.xing.com/profile/Alex_Morgan2/cv",
            "https://www.xing.com/profile/J%C3%B6rg_M%C3%BCller",
            "https://www.xing.com/pages/aperture-science",
            "https://www.xing.com/companies/aperturescience",
        ),
        (
            "https://www.xing.com/",
            "https://www.xing.com/jobs/lisboa-developer-1234",
            "https://www.xing.com/profile/",
        ),
    ),
    "facebook": (
        (
            "https://www.facebook.com/alex.morgan",
            "https://facebook.com/alex.morgan/",
            "https://m.facebook.com/alex.morgan",
            "https://pt-pt.facebook.com/alex.morgan",
            "https://www.facebook.com/profile.php?id=100001234567890",
            "https://www.facebook.com/people/Alex-Morgan/100001234567890/",
            "https://www.facebook.com/aperturescience/about",
            "https://www.facebook.com/groups/lisbondevs",
        ),
        (
            "https://www.facebook.com/",
            "https://www.facebook.com/login",
            "https://www.facebook.com/watch/?v=1234",
            "https://www.facebook.com/marketplace/lisbon",
            "https://www.facebook.com/sharer/sharer.php?u=x",
        ),
    ),
    "instagram": (
        (
            "https://www.instagram.com/alex.morgan/",
            "https://instagram.com/alex_morgan",
            "https://www.instagram.com/alex.morgan/?hl=pt",
            "https://www.instagram.com/alex.morgan/tagged/",
        ),
        (
            "https://www.instagram.com/",
            "https://www.instagram.com/p/CxYz123abc/",
            "https://www.instagram.com/reel/CxYz123abc/",
            "https://www.instagram.com/explore/tags/lisbon/",
            "https://www.instagram.com/accounts/login/",
        ),
    ),
    "threads": (
        (
            "https://www.threads.com/@alex.morgan",
            "https://www.threads.net/@alex.morgan",
            "https://threads.net/@alex_morgan/",
            "https://www.threads.com/@alex.morgan?igshid=abc",
        ),
        (
            "https://www.threads.com/",
            "https://www.threads.com/alex.morgan",
            "https://www.threads.net/search",
        ),
    ),
    "youtube": (
        (
            "https://www.youtube.com/@alexmorgan",
            "https://youtube.com/@alexmorgan/videos",
            "https://m.youtube.com/@alexmorgan",
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            "https://www.youtube.com/c/AlexMorgan",
            "https://www.youtube.com/user/alexmorgan/",
            "https://www.youtube.com/@%EC%95%8C%EB%A0%89%EC%8A%A4",  # a handle in Hangul
        ),
        (
            "https://www.youtube.com/",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/results?search_query=alex",
            "https://youtu.be/dQw4w9WgXcQ",
        ),
    ),
    "github": (
        (
            "https://github.com/alexmorgan",
            "https://github.com/alexmorgan/",
            "https://github.com/alexmorgan/thing",
            "https://github.com/alexmorgan/thing.js/tree/main/src",
            "https://github.com/alexmorgan?tab=repositories",
            "https://github.com/orgs/aperture-science",
            "https://www.github.com/alexmorgan",
            "https://gist.github.com/alexmorgan/0123456789abcdef",
        ),
        (
            "https://github.com/",
            "https://github.com/settings/profile",
            "https://github.com/login",
            "https://github.com/explore",
            "https://alexmorgan.github.io/",
        ),
    ),
    "gitlab": (
        (
            "https://gitlab.com/alexmorgan",
            "https://gitlab.com/alexmorgan/thing",
            "https://gitlab.com/aperture/labs/testing/thing",  # groups nest
            "https://gitlab.com/alexmorgan/thing/-/tree/main",
            "https://gitlab.gnome.org/GNOME/gtk",  # one somebody runs
            "https://git.example.org/alexmorgan/thing/",
        ),
        (
            "https://gitlab.com/",
            "https://gitlab.com/explore",
            "https://gitlab.com/users/sign_in",
            "https://gitlab.com/-/profile",
            "https://gitlab.com/help",
        ),
    ),
    "codeberg": (
        (
            "https://codeberg.org/alexmorgan",
            "https://codeberg.org/alexmorgan/thing",
            "https://codeberg.org/alexmorgan/thing/src/branch/main",
            "https://codeberg.org/forgejo/",
        ),
        (
            "https://codeberg.org/",
            "https://codeberg.org/explore/repos",
            "https://codeberg.org/user/login",
            "https://alexmorgan.codeberg.page/",
        ),
    ),
    "forgejo": (
        (
            "https://git.example.org/alexmorgan",
            "https://git.example.org/alexmorgan/thing",
            "https://source.example.org/aperture/thing/src/branch/main",
            "https://gitea.com/gitea/tea",
            "https://forge.example.org:3000/alexmorgan/thing/",
        ),
        (
            "https://git.example.org/",
            "https://git.example.org/explore/repos",
            "https://git.example.org/user/login",
            "https://git.example.org/api/v1/version",
        ),
    ),
    "bitbucket": (
        (
            "https://bitbucket.org/alexmorgan",
            "https://bitbucket.org/alexmorgan/thing",
            "https://bitbucket.org/alexmorgan/thing/src/main/",
            "https://bitbucket.org/alexmorgan/workspace/projects/AP",
        ),
        (
            "https://bitbucket.org/",
            "https://bitbucket.org/account/signin/",
            "https://bitbucket.org/product/features",
        ),
    ),
    "sourcehut": (
        (
            "https://sr.ht/~alexmorgan",
            "https://sr.ht/~alexmorgan/thing",
            "https://git.sr.ht/~alexmorgan/thing",
            "https://git.sr.ht/~alexmorgan/thing/tree/main",
            "https://hg.sr.ht/~alexmorgan/thing/",
        ),
        (
            "https://sr.ht/",
            "https://sr.ht/projects",
            "https://git.sr.ht/alexmorgan/thing",
        ),
    ),
}


def cases(which: int):
    return [(key, url) for key, shapes in SHAPES.items() for url in shapes[which]]


def test_every_shipped_service_has_its_table():
    """A service added to the table without its shapes fails here, not on somebody's page."""
    from postulo.plugins.link_services.services import SERVICES

    assert set(SHAPES) == set(SERVICES)
    assert all(accepted and refused for accepted, refused in SHAPES.values())


@pytest.mark.parametrize(("key", "url"), cases(0))
def test_a_service_accepts_an_address_of_its_own(key, url):
    service = link_services.find(key)

    assert service.accepts(url), f"{service.label} refused a real address"
    assert service.handle(url), "and has something to call it by"


@pytest.mark.parametrize(("key", "url"), cases(1))
def test_a_service_refuses_what_is_not_a_profile_or_a_project_there(key, url):
    assert not link_services.find(key).accepts(url)


def test_the_list_is_the_one_that_was_decided():
    """Short and conventional: nine places a profile is, six a project is, and no websites."""
    assert list(link_services.services_for(SOCIAL)) == [
        "linkedin",
        "mastodon",
        "bluesky",
        "x",
        "xing",
        "facebook",
        "instagram",
        "threads",
        "youtube",
    ]
    assert list(link_services.services_for(REPOSITORY)) == [
        "github",
        "gitlab",
        "codeberg",
        "forgejo",
        "bitbucket",
        "sourcehut",
    ]
    assert link_services.services_for(WEBSITE) == {}, "a website is its own thing"


def test_a_name_with_an_accent_passes_however_the_browser_copied_it():
    """#638 is what a pattern of ASCII letters costs: the address most people outside the
    English-speaking world have. The path is read percent-decoded, in any script."""
    linkedin = link_services.find("linkedin")

    for url in (
        "https://www.linkedin.com/in/jo%C3%A3o-silva-1a2b3c/",
        "https://www.linkedin.com/in/joão-silva-1a2b3c/",
    ):
        assert linkedin.accepts(url)
        assert linkedin.handle(url) == "joão-silva-1a2b3c"


def test_no_service_reaches_the_network():
    import inspect

    from postulo.plugins.link_services import services

    for module in (services, link_services):
        code = inspect.getsource(module).split('"""', 2)[2]
        for forbidden in ("requests", "urlopen", "httpx", "socket"):
            assert forbidden not in code


# ---------------------------------------------------------------------------- Other


@pytest.mark.parametrize(
    "url",
    [
        "https://alex.example.org",
        "https://forum.example.org/u/alex",
        "https://www.linkedin.com/feed/",  # a known host, and not a profile's shape
        "https://unknown.example/@alex",
    ],
)
def test_other_takes_any_web_address_and_keeps_its_name(url):
    assert link_services.settle(SOCIAL, link_services.OTHER, url, "A forum") == ("", "A forum")


def test_a_named_service_has_to_be_one_the_kind_offers():
    with pytest.raises(ValidationError) as refused:
        link_services.settle(SOCIAL, "github", "https://github.com/alex")
    assert refused.value.code == "service"

    with pytest.raises(ValidationError) as unknown:
        link_services.settle(SOCIAL, "myspace", "https://myspace.example/alex")
    assert unknown.value.code == "service"


def test_the_refusal_says_what_an_address_there_looks_like():
    with pytest.raises(ValidationError) as refused:
        link_services.settle(SOCIAL, "linkedin", "https://example.org/in/alex")

    assert refused.value.code == "address"
    (message,) = refused.value.messages
    assert "LinkedIn" in message and "https://www.linkedin.com/in/name" in message
    assert "Other" in message, "and says where an address of another shape goes"


def test_the_refusal_is_in_the_readers_language():
    from django.utils import translation

    with translation.override("pt-pt"):
        with pytest.raises(ValidationError) as refused:
            link_services.settle(SOCIAL, "linkedin", "https://example.org/in/alex")
        (message,) = refused.value.messages

    assert "LinkedIn" in message and "https://www.linkedin.com/in/name" in message
    assert "does not look like" not in message


def test_a_named_service_drops_the_name_because_it_is_the_name():
    assert link_services.settle(
        REPOSITORY, "codeberg", "https://codeberg.org/alex/thing", "typed"
    ) == ("codeberg", "")


# ------------------------------------------------------------ the guess from the host


@pytest.mark.parametrize(
    ("kind", "url", "expected"),
    [
        (SOCIAL, "https://www.linkedin.com/in/alex", "linkedin"),
        (SOCIAL, "https://pt.linkedin.com/in/alex", "linkedin"),
        (SOCIAL, "https://twitter.com/alex", "x"),
        (SOCIAL, "https://mastodon.social/@alex", "mastodon"),
        (REPOSITORY, "https://github.com/alex/thing", "github"),
        (REPOSITORY, "https://gitlab.com/alex/thing", "gitlab"),
        (REPOSITORY, "https://codeberg.org/alex", "codeberg"),
        (REPOSITORY, "https://git.sr.ht/~alex/thing", "sourcehut"),
        (REPOSITORY, "https://gitea.com/gitea/tea", "forgejo"),
    ],
)
def test_an_address_with_nothing_chosen_takes_the_service_its_host_says(kind, url, expected):
    assert link_services.guess(url, kind).key == expected
    assert link_services.settle(kind, "", url) == (expected, "")


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        (SOCIAL, "https://forum.example.org/u/alex"),
        # A shape alone is never enough: any server could be a Mastodon, and nearly any
        # address has an owner and a project in it.
        (SOCIAL, "https://fosstodon.org/@alex"),
        (REPOSITORY, "https://git.example.org/alex/thing"),
        # A known host, and not the shape: Other, as it was before there were services.
        (SOCIAL, "https://www.linkedin.com/feed/"),
        (REPOSITORY, "https://github.com/"),
        # The right host under the wrong kind is not that kind's service.
        (WEBSITE, "https://github.com/alex"),
        (SOCIAL, "https://github.com/alex"),
    ],
)
def test_where_nothing_matches_it_is_other_and_nothing_is_refused(kind, url):
    assert link_services.guess(url, kind) is None
    assert link_services.settle(kind, "", url) == ("", "")


def test_a_name_of_its_own_keeps_a_pasted_address_other():
    """A name only means something under Other, so choosing a service for an address
    somebody named would cost the link the name. A name that says only what the service is
    says nothing the service does not."""
    url = "https://www.linkedin.com/in/alex"

    assert link_services.settle(SOCIAL, "", url, "Work profile") == ("", "Work profile")
    assert link_services.settle(SOCIAL, "", url, "LinkedIn") == ("linkedin", "")
    assert link_services.settle(SOCIAL, "", url, " linkedin ") == ("linkedin", "")


# ------------------------------------------------------------------- a federated host


def test_a_federated_service_is_chosen_and_then_accepted_on_any_host():
    mastodon = link_services.find("mastodon")
    forgejo = link_services.find("forgejo")

    assert mastodon.any_host and forgejo.any_host
    assert link_services.settle(SOCIAL, "mastodon", "https://fosstodon.org/@alex") == (
        "mastodon",
        "",
    )
    assert link_services.settle(
        REPOSITORY, "forgejo", "https://source.example.org/aperture/thing"
    ) == ("forgejo", "")
    with pytest.raises(ValidationError):
        link_services.settle(SOCIAL, "mastodon", "https://fosstodon.org/about")


def test_a_link_with_no_name_is_called_by_its_service_and_its_handle(user):
    """On the service's own host the handle alone; anywhere else the host goes in front,
    because there the host is half of who somebody is."""

    def shown(kind, service, url):
        return WebLink(owner=user, kind=kind, service=service, url=url).display

    assert shown(SOCIAL, "linkedin", "https://www.linkedin.com/in/alex-morgan/") == (
        "LinkedIn alex-morgan"
    )
    assert shown(REPOSITORY, "github", "https://github.com/alex/thing/tree/main") == (
        "GitHub alex/thing"
    )
    assert shown(SOCIAL, "mastodon", "https://mastodon.social/@alex") == "Mastodon @alex"
    assert shown(SOCIAL, "mastodon", "https://fosstodon.org/@alex") == (
        "Mastodon fosstodon.org/@alex"
    )
    assert shown(REPOSITORY, "gitlab", "https://gitlab.gnome.org/GNOME/gtk/-/tree/main") == (
        "GitLab gitlab.gnome.org/GNOME/gtk"
    )
    assert shown(REPOSITORY, "forgejo", "https://git.example.org/alex/thing") == (
        "Forgejo or Gitea git.example.org/alex/thing"
    )
    # Other is what it always was: the name, or the host.
    assert shown(SOCIAL, "", "https://www.forum.example.org/u/alex") == "forum.example.org"
    named = WebLink(owner=user, kind=SOCIAL, label="A forum", url="https://forum.example.org")
    assert named.display == "A forum"


def test_two_rows_on_one_service_are_told_apart(user):
    """#303's rule for the bin and its dialog still holds: a link nobody named is called by
    its address there, so two profiles on one service never share a name."""
    from postulo.accounts import removals

    first = WebLink(owner=user, kind=REPOSITORY, service="github", url="https://github.com/a/x")
    second = WebLink(owner=user, kind=REPOSITORY, service="github", url="https://github.com/a/y")

    assert removals.words(removals.LINK, first) == "github.com/a/x"
    assert removals.words(removals.LINK, first) != removals.words(removals.LINK, second)
    assert first.display != second.display


# ------------------------------------------------------------------------ the registry


class Elsewhere:
    """What a package registered under `postulo.link_services` looks like once loaded."""

    name = "elsewhere"
    version = "1.0"
    kind = "link-service"
    label = "Elsewhere"

    def __init__(self):
        from postulo.plugins.api import LinkKind, LinkService

        self.services = (
            LinkService(
                "elsewhere",
                "Elsewhere",
                LinkKind.SOCIAL,
                re.compile(r"/u/(?P<handle>[^/]+)/?"),
                hosts=("elsewhere.example",),
                icon="no-such-icon",
                example="https://elsewhere.example/u/name",
            ),
            LinkService(
                "portfolio",
                "A portfolio host",
                LinkKind.WEBSITE,
                re.compile(r"/(?P<handle>[^/]+)/?"),
                hosts=("portfolio.example",),
                icon="briefcase",
            ),
            # Postulo's own key: not this package's to redefine.
            LinkService(
                "linkedin",
                "Not LinkedIn",
                LinkKind.SOCIAL,
                re.compile(r"/.*"),
                hosts=("example.org",),
            ),
            # And three that cannot be used at all.
            LinkService("other", "Other", LinkKind.SOCIAL, re.compile(r"/.*"), any_host=True),
            LinkService("nowhere", "Nowhere", LinkKind.SOCIAL, re.compile(r"/.*")),
            LinkService("odd", "Odd", "podcast", re.compile(r"/.*"), any_host=True),
            "not a service",
        )


@pytest.fixture
def elsewhere(monkeypatch):
    """`Elsewhere`, as the registry would find it through its entry point."""
    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [Elsewhere()] if kind == "link-service" else [],
    )
    registry._cache.pop("link-service", None)
    yield Elsewhere
    registry._cache.pop("link-service", None)


def test_the_services_come_from_a_plugin():
    found = registry.plugins("link-service")

    assert [plugin.name for plugin in found] == ["link-services"]
    assert len(found[0].services) == len(link_services.registry())


def test_the_group_is_open_and_the_installer_knows_it():
    """Where the identifier group is empty, this one is advertised: a package installed
    beside Postulo may add services, and the installer takes a wheel that declares one."""
    from postulo.plugins import base, installing

    assert base.LINK_SERVICE_GROUP == "postulo.link_services"
    assert registry.GROUPS["link-service"] == "postulo.link_services"
    assert "postulo.link_services" in installing.plugin_groups()


def test_a_package_installed_beside_postulo_adds_its_services(elsewhere):
    found = registry.plugins("link-service")
    assert [plugin.name for plugin in found] == ["elsewhere", "link-services"]

    offered = link_services.services_for(SOCIAL)
    assert list(offered)[-1] == "elsewhere", "after the ones Postulo ships"
    assert link_services.guess("https://elsewhere.example/u/alex", SOCIAL).key == "elsewhere"
    assert list(link_services.services_for(WEBSITE)) == ["portfolio"], "a kind that had none"


def test_a_key_postulo_ships_is_not_another_packages_to_redefine(elsewhere, caplog):
    """Or installing a package could change what `linkedin` means on every row stored."""
    with caplog.at_level("WARNING", logger="postulo.core.link_services"):
        found = link_services.registry()

    assert found["linkedin"].label == "LinkedIn"
    assert not found["linkedin"].accepts("https://example.org/anything")
    assert "offered twice" in caplog.text


def test_a_service_that_cannot_be_used_is_left_out_and_logged(elsewhere, caplog):
    with caplog.at_level("WARNING", logger="postulo.core.link_services"):
        found = link_services.registry()

    assert not {"other", "nowhere", "odd"} & set(found)
    assert caplog.text.count("cannot be used") == 4


def test_an_icon_a_plugin_got_wrong_costs_a_picture_and_not_a_page(elsewhere):
    assert link_services.find("elsewhere").icon_name == "user", "the kind's own"
    assert link_services.find("portfolio").icon_name == "briefcase"


def test_every_icon_a_row_can_draw_is_one_postulo_ships():
    """`{% icon %}` raises on a name it has no file for, and these names come from a table
    and not from a template, so an icon nobody vendored would be a 500 on *Your details*."""
    listed = {
        line.split("#", 1)[0].strip()
        for line in (ROOT / "assets" / "icons.txt").read_text(encoding="utf-8").splitlines()
    }
    drawn = {service.icon for service in link_services.registry().values()}
    drawn |= set(link_services.KIND_ICONS.values()) | {link_services.OTHER_ICON}

    assert drawn <= listed, f"add {sorted(drawn - listed)} to assets/icons.txt"
    assert all(link_services.icon_exists(name) for name in drawn), "run `npm run sync:icons`"


def test_a_registry_is_not_somebodys_to_switch_off():
    from postulo.plugins.policy import GOVERNED_KINDS, UNGOVERNED_KINDS

    assert "link-service" in UNGOVERNED_KINDS
    assert "link-service" not in GOVERNED_KINDS


def test_the_list_is_not_frozen_into_the_model():
    """What the old docstring was afraid of, and still does not happen: no list of brands
    in a model's choices, and so none in a migration."""
    field = WebLink._meta.get_field("service")

    assert field.choices is None and field.blank
    assert field.max_length == link_services.MAX_KEY_LENGTH
    assert "linkedin" not in migration_source()


def test_the_plugin_carries_its_own_translations():
    """Core never translates a plugin's strings: the one name here that is words rather
    than a brand is in the plugin's own catalogue, and not in Postulo's."""
    own = ROOT / "src/postulo/plugins/link_services/locale/pt_PT/LC_MESSAGES/django.po"
    core = ROOT / "src/postulo/locale/pt_PT/LC_MESSAGES/django.po"

    assert 'msgid "Forgejo or Gitea"' in own.read_text(encoding="utf-8")
    assert 'msgid "Forgejo or Gitea"' not in core.read_text(encoding="utf-8")


# -------------------------------------------------------------------------- fixtures


@pytest.fixture
def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def add(holder, owner, url, *, kind=SOCIAL, service="", label="", primary=False):
    return WebLink.objects.create(
        owner=owner,
        holder=holder,
        kind=kind,
        service=service,
        label=label,
        url=url,
        is_primary=primary,
    )


def rows(prefix, *entries, initial=0):
    """The POST one block of link rows sends, management form and all."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(entries)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, entry in enumerate(entries):
        for name, value in entry.items():
            data[f"{prefix}-{index}-{name}"] = value
    return data


def contact_post(contact, **extra):
    return {
        "name": contact.name,
        "role": "",
        "company": contact.company_id,
        "email": "",
        "notes": "",
        **extra,
    }


def migration_module():
    """The migration that added the service, found by its name and not by its number:
    another branch takes a number too, and the landing renumbers."""
    (path,) = (ROOT / "src/postulo/core/migrations").glob("*_weblink_service.py")
    return importlib.import_module(f"postulo.core.migrations.{path.stem}")


def migration_source() -> str:
    (path,) = (ROOT / "src/postulo/core/migrations").glob("*_weblink_service.py")
    return path.read_text(encoding="utf-8").split('"""', 2)[2]


# ------------------------------------------------------- the rows already stored


def test_the_migration_works_out_the_service_of_the_rows_already_there(user, contact):
    """By the host, where the address has the service's shape; *Other* everywhere else.
    Nothing is refused and no address is touched."""
    from django.apps import apps

    stored = {
        "linkedin": add(contact, user, "https://www.linkedin.com/in/cave", primary=True),
        "named after it": add(contact, user, "https://pt.linkedin.com/in/c2", label="LinkedIn"),
        "named": add(contact, user, "https://www.linkedin.com/in/c3", label="Work profile"),
        "not a profile": add(contact, user, "https://www.linkedin.com/feed/"),
        "another server": add(contact, user, "https://fosstodon.org/@cave"),
        "unknown": add(contact, user, "https://forum.example.org/u/cave"),
        "github": add(contact, user, "https://github.com/cave/thing", kind=REPOSITORY),
        "a forge": add(contact, user, "https://git.example.org/cave", kind=REPOSITORY),
        "website": add(user.profile, user, "https://github.com/cave", kind=WEBSITE),
    }
    before = {name: row.url for name, row in stored.items()}

    migration_module().guess_services(apps, None)

    after = {}
    for name, row in stored.items():
        row.refresh_from_db()
        after[name] = (row.service, row.label)
        assert row.url == before[name], "no address is touched"
    assert after == {
        "linkedin": ("linkedin", ""),
        "named after it": ("linkedin", ""),  # the service says it now
        "named": ("", "Work profile"),  # a name of its own is theirs, and stays
        "not a profile": ("", ""),
        "another server": ("", ""),  # a shape alone is not guessed from
        "unknown": ("", ""),
        "github": ("github", ""),
        "a forge": ("", ""),
        "website": ("", ""),  # the right host under a kind with no services
    }
    assert stored["linkedin"].is_primary, "and nothing else about a row moved"


def test_the_migration_leaves_a_row_that_already_says_alone(user, contact):
    from django.apps import apps

    chosen = add(contact, user, "https://www.linkedin.com/in/cave", service="mastodon")

    migration_module().guess_services(apps, None)

    chosen.refresh_from_db()
    assert chosen.service == "mastodon", "only a row with no service is read"


def test_a_service_nothing_knows_any_more_reads_as_other_and_keeps_its_key(client, user):
    """A plugin that offered a service and was then removed: the row is still a row, shown
    by its host, and saving the page does not quietly take the key away -- switching a
    plugin off deletes nothing, and that includes a word on a row."""
    row = add(user.profile, user, "https://elsewhere.example/u/alex", service="elsewhere")
    assert row.display == "elsewhere.example" and row.service_label == "Other"
    assert row.icon == "globe"

    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert re.search(r'<option value="other"[^>]*selected', html), "drawn as Other"

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            **rows(
                "social_profiles",
                {
                    "id": row.pk,
                    "service": "other",
                    "label": "",
                    "url": "https://elsewhere.example/u/alex",
                },
                initial=1,
            ),
        },
    )

    assert response.status_code == 302, response.content.decode()[:500]
    row.refresh_from_db()
    assert row.service == "elsewhere"


# ----------------------------------------------------------------- the rows on a page


def form(kind=SOCIAL, instance=None, **data):
    return web_links.WebLinkForm(data=data, instance=instance, kind=kind)


def test_a_row_takes_a_service_and_an_address_that_goes_with_it():
    row = form(service="linkedin", label="typed", url="https://www.linkedin.com/in/alex")

    assert row.is_valid(), row.errors
    assert (row.cleaned_data["service"], row.cleaned_data["label"]) == ("linkedin", "")


def test_an_address_that_is_not_the_services_is_refused_beside_the_address():
    row = form(service="linkedin", label="", url="https://example.org/in/alex")

    assert not row.is_valid()
    assert list(row.errors) == ["url"], "beside the address, which is what is wrong"
    assert "https://www.linkedin.com/in/name" in row.errors["url"][0]


def test_other_is_posted_as_other_and_stored_as_nothing():
    row = form(service="other", label="A forum", url="https://www.linkedin.com/feed/")

    assert row.is_valid(), row.errors
    assert (row.cleaned_data["service"], row.cleaned_data["label"]) == ("", "A forum")


@pytest.mark.parametrize("posted", [{"service": ""}, {}], ids=["nothing chosen", "no field"])
def test_a_new_row_with_nothing_chosen_takes_its_service_from_the_address(posted):
    """A row left as it was drawn, and a client that never heard of the field."""
    row = form(label="", url="https://www.linkedin.com/in/alex", **posted)

    assert row.is_valid(), row.errors
    assert row.cleaned_data["service"] == "linkedin"


def test_a_service_of_another_kind_is_not_a_choice():
    row = form(service="github", label="", url="https://github.com/alex")

    assert not row.is_valid() and "service" in row.errors


def test_the_choice_is_the_kinds_services_then_other_and_a_new_row_starts_unchosen(user):
    new = form(REPOSITORY)
    assert [value for value, _label in new.fields["service"].choices] == [
        "",
        "github",
        "gitlab",
        "codeberg",
        "forgejo",
        "bitbucket",
        "sourcehut",
        "other",
    ]

    saved = add(user.profile, user, "https://alex.example.org", kind=REPOSITORY)
    kept = web_links.WebLinkForm(instance=saved, kind=REPOSITORY)
    assert "" not in dict(kept.fields["service"].choices), (
        "a stored row has a service or is Other: there is no unchosen to go back to"
    )
    assert kept.initial["service"] == "other", "and a row with none is Other"


def test_a_kind_with_no_services_has_no_choice_to_draw(user):
    """A select holding Other alone would be a control that offers nothing."""
    row = form(WEBSITE, label="Blog", url="https://alex.example.org")

    assert "service" not in row.fields and row.service_icon == ""
    assert row.is_valid(), row.errors
    assert (row.cleaned_data["service"], row.cleaned_data["label"]) == ("", "Blog")


def test_each_option_says_its_icon_and_its_hosts_and_the_chosen_icon_is_the_one_drawn(user):
    saved = add(user.profile, user, "https://fosstodon.org/@alex", service="mastodon")
    row = web_links.WebLinkForm(instance=saved, kind=SOCIAL)
    html = str(row["service"])

    assert re.search(r'<option value="linkedin" data-icon="user" data-hosts="linkedin.com"', html)
    assert re.search(
        r'<option value="mastodon" selected data-icon="at-sign" '
        r'data-hosts="mastodon.social mastodon.online">',
        html,
    )
    assert re.search(r'<option value="other" data-icon="globe">', html)
    assert "data-service-select" in html
    # The one icon the page draws beside the native select, for a page without scripts,
    # and after the select every icon its list can show, once each, for the select's own
    # control to copy (#301).
    assert row.service_icon == "at-sign"
    after = html[html.index("</select>") :]
    assert re.match(r"</select>\s*<template data-option-icons>", after)
    assert sorted(re.findall(r'data-icon="([a-z-]+)"', after)) == [
        "at-sign",
        "globe",
        "user",
        "video",
    ]


def test_a_stored_row_saved_as_it_was_is_not_checked_again(user, monkeypatch):
    """Nothing is refused in retrospect: a pattern tightened after a row was saved does not
    turn the next save of the page into an error about a row nobody touched."""
    saved = add(user.profile, user, "https://www.linkedin.com/feed/", service="linkedin")
    posted = {"service": "linkedin", "label": "", "url": "https://www.linkedin.com/feed/"}

    untouched = web_links.WebLinkForm(data=posted, instance=saved, kind=SOCIAL)
    assert untouched.is_valid(), untouched.errors
    assert untouched.cleaned_data["service"] == "linkedin"

    moved = web_links.WebLinkForm(
        data={**posted, "url": "https://www.linkedin.com/jobs/"}, instance=saved, kind=SOCIAL
    )
    assert not moved.is_valid() and "url" in moved.errors, "a changed address is checked"


def test_a_stored_row_posted_without_a_choice_keeps_what_it_has(user):
    """A script, or a copy of the page from before there was a choice."""
    saved = add(user.profile, user, "https://alex.example.org/me", label="Mine")
    row = web_links.WebLinkForm(
        data={"label": "Mine", "url": "https://www.linkedin.com/in/alex"},
        instance=saved,
        kind=SOCIAL,
    )

    assert row.is_valid(), row.errors
    assert (row.cleaned_data["service"], row.cleaned_data["label"]) == ("", "Mine"), (
        "Other it was and Other it stays: only a new row is guessed for"
    )


@pytest.mark.parametrize("page", ["profile", "contact"])
def test_the_page_saves_the_service_of_each_row(client, user, contact, page):
    client.force_login(user)
    posted = rows(
        "social_profiles",
        {"service": "mastodon", "label": "", "url": "https://fosstodon.org/@alex"},
        {"service": "other", "label": "A forum", "url": "https://forum.example.org/u/alex"},
        {"service": "", "label": "", "url": "https://www.linkedin.com/in/alex"},
    )
    posted |= rows(
        "repositories",
        {"service": "forgejo", "label": "", "url": "https://git.example.org/alex/thing"},
    )
    posted |= rows("websites", {"label": "Blog", "url": "https://alex.example.org"})
    if page == "profile":
        url, holder = reverse("accounts:profile"), user.profile
        response = client.post(url, {**PROFILE_POST, **posted})
    else:
        url, holder = reverse("jobs:contact_update", args=[contact.pk]), contact
        response = client.post(url, contact_post(contact, **posted))

    assert response.status_code == 302, response.content.decode()[:800]
    saved = {(row.kind, row.service, row.label, row.url) for row in holder.web_links.all()}
    assert saved == {
        ("social", "mastodon", "", "https://fosstodon.org/@alex"),
        ("social", "", "A forum", "https://forum.example.org/u/alex"),
        ("social", "linkedin", "", "https://www.linkedin.com/in/alex"),
        ("repository", "forgejo", "", "https://git.example.org/alex/thing"),
        ("website", "", "Blog", "https://alex.example.org"),
    }


@pytest.mark.parametrize("page", ["profile", "contact"])
def test_the_page_comes_back_saying_what_an_address_there_looks_like(client, user, contact, page):
    client.force_login(user)
    posted = rows(
        "repositories",
        {"service": "github", "label": "", "url": "https://example.org/alex/thing"},
    )
    if page == "profile":
        response = client.post(reverse("accounts:profile"), {**PROFILE_POST, **posted})
        holder = user.profile
    else:
        response = client.post(
            reverse("jobs:contact_update", args=[contact.pk]), contact_post(contact, **posted)
        )
        holder = contact

    assert response.status_code == 200, "the form came back rather than saving"
    html = response.content.decode()
    assert "That does not look like an address on GitHub" in html
    assert "https://github.com/name/project" in html
    # The sentence is the address's own error: the box names it as what describes it.
    error = re.search(r'<div id="(id_repositories-0-url_error)" role="alert">', html)
    assert error and re.search(
        rf'<input type="url" name="repositories-0-url"[^>]*aria-describedby="[^"]*{error[1]}', html
    )
    assert not holder.web_links.exists()


def row_markup(html: str, kind: str, index: int = 0) -> str:
    """One row of one block, as it was drawn."""
    block = html[html.index(f'data-web-links="{kind}"') :]
    block = block[: block.index("</fieldset>")]
    return block.split("<li ")[index + 1]


@pytest.mark.parametrize("page", ["profile", "contact"])
def test_a_row_reads_service_then_name_then_address_then_its_controls(client, user, contact, page):
    """What it is first, then the value (#213), in the order written -- which is the order
    read, tabbed through and announced. The name box is marked for the stylesheet, which
    shows it only while Other is chosen or it holds something, with no script."""
    holder = user.profile if page == "profile" else contact
    add(holder, user, "https://www.linkedin.com/in/alex", service="linkedin", primary=True)
    add(holder, user, "https://forum.example.org/u/alex", label="A forum")
    client.force_login(user)
    url = (
        reverse("accounts:profile")
        if page == "profile"
        else reverse("jobs:contact_update", args=[contact.pk])
    )

    html = client.get(url).content.decode()
    on_a_service = row_markup(html, "social", 0)
    other = row_markup(html, "social", 1)
    new = row_markup(html, "social", 2)

    for row in (on_a_service, other, new):
        places = [
            row.index('name="social_profiles-'),
            row.index("-service"),
            row.index("-label"),
            row.index("-url"),
            row.index("-primary"),
        ]
        assert places == sorted(places), "service, name, address, then the controls"
        assert "data-if-other" in row and "data-name-if-other" in row
        assert not re.search(r"\border-", row), "no order utilities: the source is the order"
    assert "data-has-name" not in on_a_service, "hidden: a named service needs no name"
    assert "data-has-name" in other, "a name that is there is never hidden"
    assert re.search(r'<option value="other"[^>]*selected', other)
    assert re.search(r'<option value="linkedin"[^>]*selected', on_a_service)
    assert "data-has-name" not in new
    assert re.search(r'<option value=""[^>]*selected', new), "a new row starts unchosen"
    # The chosen service's icon is drawn beside the select, and no other: the rest are
    # in the template after the select, for its own control to copy (#301).
    for row, icon in ((on_a_service, "user"), (other, "globe"), (new, "globe")):
        beside = re.search(r"<span[^>]*data-option-mark[^>]*>(.*?)</span>", row, re.S).group(1)
        assert re.findall(r'data-icon="([a-z-]+)"', beside) == [icon]
        listed = re.search(r"<template data-option-icons>(.*?)</template>", row, re.S).group(1)
        assert sorted(re.findall(r'data-icon="([a-z-]+)"', listed)) == [
            "at-sign",
            "globe",
            "user",
            "video",
        ]


def test_the_websites_block_has_no_select_and_its_name_is_always_there(client, user):
    add(user.profile, user, "https://alex.example.org", kind=WEBSITE, primary=True)
    client.force_login(user)

    row = row_markup(client.get(reverse("accounts:profile")).content.decode(), "website")

    assert "<select" not in row and "data-if-other" not in row
    assert row.index("websites-0-label") < row.index("websites-0-url"), "name, then address"


def test_the_name_box_follows_the_select_without_a_script():
    """`:has()` on the group, written as the rule that hides, so a browser that cannot read
    it shows the box: always there is the safe way to be wrong (#309)."""
    css = (ROOT / "src/postulo/static/css/app.css").read_text(encoding="utf-8")

    assert (
        '[data-if-other]:not([data-has-name]):not(:has(select option[value="other"]:checked))'
        in css.replace("\n", "")
        or "[data-if-other]:not([data-has-name])" in css
    )
    assert "select[data-service-select]" in css, "and the choice is drawn as a choice"


def test_the_one_box_guesses_too_and_refuses_nothing(user):
    """While a kind's feature is off there is one box and no choice, so the address says."""
    first = web_links.save_only_link(user.profile, user, SOCIAL, "https://x.com/alexmorgan")
    assert first.service == "x"

    moved = web_links.save_only_link(user.profile, user, SOCIAL, "https://www.linkedin.com/feed/")
    assert (moved.pk, moved.service) == (first.pk, ""), "a new address is read again"

    kept = web_links.save_only_link(user.profile, user, SOCIAL, "https://www.linkedin.com/feed/")
    assert kept.service == ""


# ------------------------------------------------------------------------------ the API


def bearer(user):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=("write", "read"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_contact(client, user, contact, **payload):
    return client.post(
        f"/api/v1/companies/{contact.company_id}/contacts",
        data=json.dumps({"name": "Caroline", **payload}),
        content_type="application/json",
        **bearer(user),
    )


def test_the_api_reads_and_writes_a_link_with_its_service(client, user, contact):
    response = post_contact(
        client,
        user,
        contact,
        web_links=[
            {"kind": "repository", "service": "forgejo", "url": "https://git.example.org/c/x"},
            {"kind": "social", "url": "https://www.linkedin.com/in/caroline"},
            {
                "kind": "social",
                "service": "other",
                "label": "A forum",
                "url": "https://forum.example.org/u/caroline",
                "is_primary": True,
            },
        ],
    )

    assert response.status_code == 201, response.content
    listed = {
        (row["kind"], row["service"], row["label"], row["is_primary"]): row["url"]
        for row in response.json()["web_links"]
    }
    assert listed == {
        ("repository", "forgejo", "", True): "https://git.example.org/c/x",
        ("social", "linkedin", "", False): "https://www.linkedin.com/in/caroline",
        ("social", "", "A forum", True): "https://forum.example.org/u/caroline",
    }
    assert response.json()["linkedin_url"] == "https://forum.example.org/u/caroline", (
        "the single address is the primary social profile, whatever service it is on"
    )

    detail = client.get(f"/api/v1/companies/{contact.company_id}", **bearer(user)).json()
    caroline = next(person for person in detail["contacts"] if person["name"] == "Caroline")
    assert {row["service"] for row in caroline["web_links"]} == {"forgejo", "linkedin", ""}


@pytest.mark.parametrize(
    ("link", "said"),
    [
        (
            {"kind": "social", "service": "linkedin", "url": "https://example.org/in/c"},
            "does not look like an address on LinkedIn",
        ),
        (
            {"kind": "social", "service": "github", "url": "https://github.com/c"},
            "not a service this type of link can be on",
        ),
        ({"kind": "podcast", "url": "https://example.org/c"}, "not one of the types of link"),
    ],
)
def test_the_api_refuses_a_link_that_is_not_its_services_and_writes_nothing(
    client, user, contact, link, said
):
    from postulo.jobs.models import Contact

    response = post_contact(client, user, contact, web_links=[link])

    assert response.status_code == 422, response.content
    assert said in response.content.decode()
    assert not Contact.objects.filter(name="Caroline").exists(), "no contact left behind it"


def test_the_single_address_is_still_taken_and_is_never_refused(client, user, contact):
    """`linkedin_url` has always taken whatever profile a client had, so it is read as an
    address with nothing chosen: LinkedIn where it is one, Other where it is not."""
    there = post_contact(client, user, contact, linkedin_url="https://www.linkedin.com/in/c")
    assert there.status_code == 201, there.content
    assert [(row["service"], row["is_primary"]) for row in there.json()["web_links"]] == [
        ("linkedin", True)
    ]

    elsewhere = post_contact(client, user, contact, linkedin_url="https://example.org/caroline")
    assert elsewhere.status_code == 201, elsewhere.content
    assert [row["service"] for row in elsewhere.json()["web_links"]] == [""]


# -------------------------------------------------------------------------- the archive


def test_the_archive_carries_the_service_and_brings_it_back(user, contact):
    from postulo.core import export
    from postulo.core.importer import _link_rows, _restore_web_links

    add(contact, user, "https://fosstodon.org/@cave", service="mastodon", primary=True)
    add(contact, user, "https://forum.example.org/u/cave", label="A forum")
    # Other by choice, on a host a service answers on: it has to come back as Other.
    add(contact, user, "https://www.linkedin.com/in/cave")

    document = export.build_document(user)
    written = document["companies"][0]["contacts"][0]["web_links"]

    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 27
    assert [(row["service"], row["label"]) for row in written] == [
        ("mastodon", ""),
        ("", "A forum"),
        ("", ""),
    ]

    contact.web_links.all().delete()
    _restore_web_links(contact, user, _link_rows({"web_links": written}))

    assert [(row.service, row.label, row.url) for row in contact.web_links.all()] == [
        ("mastodon", "", "https://fosstodon.org/@cave"),
        ("", "A forum", "https://forum.example.org/u/cave"),
        ("", "", "https://www.linkedin.com/in/cave"),
    ]


def test_an_older_archive_has_each_links_service_worked_out_from_its_address(user, contact):
    """Before the format carried one, a link said nothing about its service. Read as an
    address pasted with nothing chosen -- including the single columns from before format
    15 -- and never refused."""
    from postulo.core.importer import _link_rows, _restore_web_links

    older = {
        "linkedin_url": "https://www.linkedin.com/in/cave",
        "web_links": [
            {
                "kind": "repository",
                "label": "",
                "url": "https://codeberg.org/cave",
                "is_primary": 1,
            },
            {"kind": "repository", "label": "Work", "url": "https://github.com/aperture"},
            {"kind": "website", "label": "", "url": "https://github.com/cave"},
        ],
    }

    _restore_web_links(contact, user, _link_rows(older))

    assert {(row.url, row.service, row.label) for row in contact.web_links.all()} == {
        ("https://codeberg.org/cave", "codeberg", ""),
        ("https://github.com/aperture", "", "Work"),
        ("https://github.com/cave", "", ""),
        ("https://www.linkedin.com/in/cave", "linkedin", ""),
    }


def test_a_service_this_instance_does_not_know_restores_as_other(user, contact):
    """A newer archive, or one from an instance with a plugin this one has not: the link
    comes in, by its host, and only the word is lost. One that names a service the address
    is not on is Other too, rather than a row that contradicts itself."""
    from postulo.core.importer import _restore_web_links

    _restore_web_links(
        contact,
        user,
        [
            {"kind": "social", "service": "elsewhere", "url": "https://elsewhere.example/u/c"},
            {"kind": "social", "service": "linkedin", "url": "https://example.org/in/c"},
            {"kind": "social", "service": 7, "url": "https://example.org/seven"},
        ],
    )

    assert [(row.service, row.display) for row in contact.web_links.all()] == [
        ("", "elsewhere.example"),
        ("", "example.org"),
        ("", "example.org"),
    ]


def test_a_candidate_file_and_a_europass_website_have_their_service_worked_out(user):
    """A file that says nothing about a link's service -- every one written before there
    were services, and every one written by hand -- has it worked out from the address.
    What a file does say is a claim: kept where this instance can check it, and *Other*
    where it cannot, so no link is ever refused over its service."""
    from postulo.core import export
    from postulo.resume import candidate

    links = [
        # Says nothing: the address says.
        {"kind": "repository", "url": "https://codeberg.org/alex", "is_primary": True},
        {"kind": "social", "label": "A forum", "url": "https://forum.example.org/u/alex"},
        # Says a service this instance offers, on an address that is one of its own.
        {"kind": "social", "service": "mastodon", "url": "https://fosstodon.org/@alex"},
        # Says Other, on a host a service answers on: Other it stays.
        {"kind": "social", "service": "", "url": "https://www.linkedin.com/in/alex"},
        # Says a service only the other instance knew, and one the address is not on.
        {"kind": "social", "service": "elsewhere", "url": "https://elsewhere.example/u/alex"},
        {"kind": "repository", "service": "github", "url": "https://git.example.org/alex"},
    ]
    held = candidate.read(
        json.dumps(
            {
                "postulo": {"candidate_format": export.CANDIDATE_FORMAT},
                "account": {"profile": {"web_links": links}},
            }
        ).encode()
    )
    drawn = candidate.plan(user, held)
    candidate.apply(user, held)

    outcomes = [row.outcome for section in drawn.sections for row in section.rows]
    assert outcomes == [candidate.ADD] * len(links), "none refused"
    assert {(row.url, row.service, row.label) for row in user.profile.web_links.all()} == {
        ("https://codeberg.org/alex", "codeberg", ""),
        ("https://forum.example.org/u/alex", "", "A forum"),
        ("https://fosstodon.org/@alex", "mastodon", ""),
        ("https://www.linkedin.com/in/alex", "", ""),
        ("https://elsewhere.example/u/alex", "", ""),
        ("https://git.example.org/alex", "", ""),
    }

    # What the Europass importer does with the one website a file holds.
    site = web_links.save_only_link(user.profile, user, WEBSITE, "https://alex.example.org")
    assert site.service == "", "a website is Other: its kind has no services"


# ----------------------------------------- an address a browser may send somewhere else
#
# A service's name on a row is a promise about where the link goes, so an address is one of
# a service's only where a browser and this code agree about the host.

#: Addresses ``urlsplit`` reads as LinkedIn's. A backslash in the authority is a slash to a
#: browser, so the first is a page on ``evil.example``; and what stands before an ``@`` is a
#: name to sign in with, which no profile's address has and which is how one host is
#: dressed as another.
SENT_ELSEWHERE = (
    "https://evil.example\\@linkedin.com/in/x",
    "https://evil.example%2F@linkedin.com/in/x",
    "https://user:pw@linkedin.com/in/x",
)


@pytest.mark.parametrize("url", SENT_ELSEWHERE)
def test_an_address_a_browser_may_send_elsewhere_is_no_named_services(url):
    linkedin = link_services.find("linkedin")

    assert not linkedin.accepts(url)
    assert linkedin.handle(url) == ""
    assert link_services.guess(url, SOCIAL) is None
    assert link_services.settle(SOCIAL, "", url) == ("", ""), "Other, and nothing refused"
    with pytest.raises(ValidationError) as refused:
        link_services.settle(SOCIAL, "linkedin", url)
    assert refused.value.code == "address"
    assert link_services.display("linkedin", SOCIAL, url) == "LinkedIn"


def test_a_service_on_any_host_is_not_on_a_host_dressed_as_another():
    """Accepted on its shape wherever it is, so the host is all there is to be wrong about."""
    mastodon = link_services.find("mastodon")

    assert mastodon.accepts("https://fosstodon.org/@alex")
    for url in (
        "https://evil.example\\@mastodon.social/@alex",
        "https://evil.example\\.fosstodon.org/@alex",
        "https://mastodon.social@evil.example/@alex",
        "https://alex@fosstodon.org/@alex",
    ):
        assert not mastodon.accepts(url), url
        assert link_services.guess(url, SOCIAL) is None, url


@pytest.mark.parametrize("url", SENT_ELSEWHERE)
def test_a_row_does_not_file_such_an_address_under_a_service(url):
    chosen = form(service="linkedin", label="", url=url)
    assert not chosen.is_valid()
    assert "does not look like an address on LinkedIn" in str(chosen.errors["url"])

    pasted = form(service="", label="", url=url)
    assert pasted.is_valid(), pasted.errors
    assert pasted.cleaned_data["service"] == "", "nothing chosen, so it is Other"


def test_the_api_does_not_file_such_an_address_under_a_service(client, user, contact):
    from postulo.jobs.models import Contact

    url = SENT_ELSEWHERE[0]
    refused = post_contact(
        client, user, contact, web_links=[{"kind": "social", "service": "linkedin", "url": url}]
    )
    assert refused.status_code == 422, refused.content
    assert "does not look like an address on LinkedIn" in refused.content.decode()
    assert not Contact.objects.filter(name="Caroline").exists(), "no contact left behind it"

    taken = post_contact(
        client,
        user,
        contact,
        linkedin_url=SENT_ELSEWHERE[1],
        web_links=[{"kind": "social", "url": url}],
    )
    assert taken.status_code == 201, taken.content
    assert {(row["url"], row["service"]) for row in taken.json()["web_links"]} == {
        (SENT_ELSEWHERE[1], ""),
        (url, ""),
    }


def test_a_link_a_plugin_saves_is_not_given_a_service_by_such_an_address(user):
    """`save_web_link` on the plugin surface, which is the one box's rule: nothing chosen."""
    from postulo.plugins.api import save_web_link

    for url in SENT_ELSEWHERE:
        row = save_web_link(user.profile, user, SOCIAL, url)
        assert (row.url, row.service) == (url, "")


def test_a_candidate_file_naming_the_service_of_such_an_address_brings_in_other(user):
    from postulo.core import export
    from postulo.resume import candidate

    links = [{"kind": "social", "service": "linkedin", "url": url} for url in SENT_ELSEWHERE]
    # And one that says nothing, so the address would have said.
    links.append({"kind": "social", "url": "https://evil.example\\@linkedin.com/in/y"})
    held = candidate.read(
        json.dumps(
            {
                "postulo": {"candidate_format": export.CANDIDATE_FORMAT},
                "account": {"profile": {"web_links": links}},
            }
        ).encode()
    )
    candidate.apply(user, held)

    assert {(row.url, row.service) for row in user.profile.web_links.all()} == {
        (link["url"], "") for link in links
    }


def test_the_migration_guesses_no_service_for_such_an_address(user, contact):
    from django.apps import apps

    stored = [add(contact, user, url) for url in SENT_ELSEWHERE]

    migration_module().guess_services(apps, None)

    for row in stored:
        row.refresh_from_db()
        assert row.service == "", row.url


# ------------------------------------------------- a path that decodes to a control

#: The path is read percent-decoded, and what is read is what a link with no name is called
#: by. A NUL, an escape sequence that recolours a terminal, a carriage return and a C1
#: control are none of them part of anybody's name.
CONTROLS = (
    ("linkedin", SOCIAL, "https://linkedin.com/in/%00%1B%5B31m"),
    ("linkedin", SOCIAL, "https://www.linkedin.com/in/alex/%0D"),
    ("x", SOCIAL, "https://x.com/alex/%C2%85"),
    ("mastodon", SOCIAL, "https://fosstodon.org/users/%00"),
    ("github", REPOSITORY, "https://github.com/%00/%00"),
)


@pytest.mark.parametrize(("key", "kind", "url"), CONTROLS)
def test_a_path_that_decodes_to_a_control_character_is_no_named_services(key, kind, url):
    service = link_services.find(key)

    assert not service.accepts(url)
    assert link_services.settle(kind, "", url) == ("", ""), "Other, and nothing refused"
    with pytest.raises(ValidationError):
        link_services.settle(kind, key, url)
    assert link_services.display(key, kind, url) == str(service.label)


def test_no_control_character_reaches_the_page_through_what_a_link_is_called(client, user):
    """Pasted with nothing chosen, it was guessed to be LinkedIn and called by its handle:
    a raw NUL and an escape character in the page, wherever the row is named."""
    url = "https://linkedin.com/in/%00%1B%5B31m"
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {**PROFILE_POST, **rows("social_profiles", {"service": "", "label": "", "url": url})},
    )

    assert response.status_code == 302, response.content.decode()[:500]
    row = user.profile.web_links.get()
    assert (row.url, row.service, row.display) == (url, "", "linkedin.com")
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "\x00" not in html and "\x1b" not in html


def test_an_address_longer_than_a_link_can_be_is_no_named_services():
    """Five hundred characters is what the column, the page and the API each hold an
    address to, and so what bounds the time a pattern can take; said where the pattern is
    run, it holds for an address a plugin hands over as well."""
    longest = "https://www.linkedin.com/in/".ljust(link_services.MAX_ADDRESS_LENGTH, "a")
    linkedin = link_services.find("linkedin")

    assert WebLink._meta.get_field("url").max_length == link_services.MAX_ADDRESS_LENGTH
    assert linkedin.accepts(longest)
    assert not linkedin.accepts(longest + "a")
    assert link_services.settle(SOCIAL, "", longest + "a") == ("", "")


# ------------------------------------------------------ what the API takes for a link


def test_a_contact_takes_as_many_links_as_a_candidate_file_and_no_more(client, user, contact):
    """Twenty thousand were taken, and took most of a minute. One more than the limit is
    refused before anything is written."""
    from postulo.api import schemas
    from postulo.jobs.models import Contact
    from postulo.resume import candidate

    def links(count: int) -> list[dict]:
        return [{"kind": "website", "url": f"https://site-{n}.example.org"} for n in range(count)]

    assert schemas.MAX_WEB_LINKS == candidate.MAX_CONTACT_ROWS

    over = post_contact(client, user, contact, web_links=links(schemas.MAX_WEB_LINKS + 1))
    assert over.status_code == 422, over.content
    assert "web_links" in over.content.decode()
    assert not Contact.objects.filter(name="Caroline").exists()
    assert not WebLink.objects.exists()

    full = post_contact(client, user, contact, web_links=links(schemas.MAX_WEB_LINKS))
    assert full.status_code == 201, full.content
    assert len(full.json()["web_links"]) == schemas.MAX_WEB_LINKS


@pytest.mark.parametrize("label", ["A\x00forum", "x" * 61], ids=["a NUL", "too long"])
def test_the_api_refuses_a_name_the_page_would_refuse(client, user, contact, label):
    """A NUL was stored, where PostgreSQL would have answered with a 500."""
    from postulo.jobs.models import Contact

    response = post_contact(
        client,
        user,
        contact,
        web_links=[
            {
                "kind": "social",
                "service": "other",
                "label": label,
                "url": "https://forum.example.org/u/caroline",
            }
        ],
    )

    assert response.status_code == 422, response.content
    assert "label" in response.content.decode()
    assert not Contact.objects.filter(name="Caroline").exists()


def test_the_api_measures_a_name_once_the_space_around_it_is_gone(client, user, contact):
    """As the page does: sixty characters with a space either side are sixty characters."""
    response = post_contact(
        client,
        user,
        contact,
        web_links=[
            {
                "kind": "social",
                "service": "other",
                "label": f" {'x' * 60} ",
                "url": "https://forum.example.org/u/caroline",
            }
        ],
    )

    assert response.status_code == 201, response.content
    assert [row["label"] for row in response.json()["web_links"]] == ["x" * 60]


# ------------------------------------------------ a registry somebody wrote carelessly


@pytest.fixture
def beside(monkeypatch):
    """Whatever a test names, as the registry would find it through its entry point."""

    def install(*plugins):
        monkeypatch.setattr(
            registry,
            "_load_third_party",
            lambda kind: list(plugins) if kind == "link-service" else [],
        )
        registry._cache.pop("link-service", None)

    yield install
    registry._cache.pop("link-service", None)


class Careless:
    """A package whose table holds two services that would each have cost a page."""

    name = "careless"
    version = "1.0"
    kind = "link-service"
    label = "Careless"

    def __init__(self):
        from postulo.plugins.api import LinkKind, LinkService

        self.services = (
            # A pattern over bytes cannot read an address, which is text.
            LinkService(
                "bytes",
                "Bytes",
                LinkKind.SOCIAL,
                re.compile(rb"/u/[^/]+"),
                hosts=("bytes.example",),
            ),
            # An icon is a name.
            LinkService(
                "seven",
                "Seven",
                LinkKind.SOCIAL,
                re.compile(r"/u/[^/]+"),
                hosts=("seven.example",),
                icon=7,
            ),
            LinkService(
                "sound",
                "Sound",
                LinkKind.SOCIAL,
                re.compile(r"/u/(?P<handle>[^/]+)"),
                hosts=("sound.example",),
            ),
        )


def test_a_pattern_over_bytes_and_an_icon_that_is_no_name_are_left_out(
    beside, caplog, client, user
):
    """Each passed for usable. The pattern raised on every save of an address on its host,
    and the icon on every drawing of a block that offered it: a 500 either way, for
    everybody, from one line of somebody else's table."""
    beside(Careless())

    with caplog.at_level("WARNING", logger="postulo.core.link_services"):
        found = link_services.registry()

    assert "sound" in found, "the rest of the table stands"
    assert not {"bytes", "seven"} & set(found)
    assert caplog.text.count("cannot be used") == 2

    assert link_services.settle(SOCIAL, "", "https://bytes.example/u/alex") == ("", "")
    client.force_login(user)
    assert client.get(reverse("accounts:profile")).status_code == 200


def test_a_class_built_on_the_shipped_one_is_not_one_postulo_ships(beside, caplog):
    """Postulo's keys are Postulo's because its own plugins are read first, and "its own"
    meant "an instance of a class it ships" -- which a subclass is. A package had only to
    inherit from `LinkServices` to be read as shipped, ahead of it, and take `linkedin`."""
    from postulo.plugins.api import LinkKind, LinkService
    from postulo.plugins.link_services import LinkServices

    class Imposter(LinkServices):
        name = "imposter"
        services = (
            LinkService(
                "linkedin",
                "Not LinkedIn",
                LinkKind.SOCIAL,
                re.compile(r"/.*"),
                hosts=("example.org",),
            ),
        )

    beside(Imposter())

    with caplog.at_level("WARNING", logger="postulo.core.link_services"):
        found = link_services.registry()

    assert found["linkedin"].label == "LinkedIn"
    assert not found["linkedin"].accepts("https://example.org/anything")
    assert "offered twice" in caplog.text


# ------------------------------------------------- a pattern reads an address once
#
# A pattern is run on whatever somebody pastes, in their request. One that goes back over
# what it has read -- a repetition inside a repetition -- takes time that doubles with every
# character, and five hundred characters of it is a worker gone for good. Nothing times a
# pattern out, and nothing new is depended on to do it: an address is at most five hundred
# characters, a pattern has to be one that reads it once, and this is what holds the
# patterns Postulo ships to that.

#: How long one pattern may take over one path, in seconds. A pattern that reads its input
#: once takes microseconds over five hundred characters; this is thousands of times that,
#: so a busy machine does not fail it and a careless pattern still does.
PATIENCE = 0.1

#: The lengths tried, shortest first and ending at the longest path there can be. A
#: pattern whose time doubles per character is caught on the way up, at a length that
#: costs a second, rather than at five hundred, which would never come back.
LENGTHS = (*range(8, 33, 2), 64, 125, 250, link_services.MAX_ADDRESS_LENGTH)


def awkward_paths(length: int, start: str):
    """Paths of ``length`` characters that almost match: a long run of what a pattern
    repeats, then one character that is nobody's, so every way of dividing the run up is
    tried by a pattern that can divide it more than one way."""
    for unit in ("a", "/", "@", "a/", "a@", "a/-/", "-/", "a-/", "/-", ".", "a.a_a-", "%2F"):
        run = (start + unit * length)[: length - 1]
        for last in ("a", " ", "\n", "/"):
            yield run + last


def seconds(pattern: re.Pattern, path: str) -> float:
    """How long the pattern takes over the path: the fastest of three where the first was
    slow, so that a machine busy with something else is not taken for a slow pattern."""
    import time

    fastest = float("inf")
    for _attempt in range(3):
        began = time.perf_counter()
        pattern.fullmatch(path)
        fastest = min(fastest, time.perf_counter() - began)
        if fastest <= PATIENCE:
            break
    return fastest


def first_slow_path(pattern: re.Pattern, starts) -> str:
    """The first awkward path the pattern is slow over, described; empty where none is."""
    for length in LENGTHS:
        for start in starts:
            for path in awkward_paths(length, start):
                took = seconds(pattern, path)
                if took > PATIENCE:
                    return f"{took:.2f}s over {length} characters: {path[:40]!r}"
    return ""


def starts_of(service) -> list[str]:
    """Where an awkward path begins: bare, as a name on a few services does, and as far
    into the service's own example as its last slash, which is past whatever its pattern
    is strict about."""
    from urllib.parse import urlsplit

    example = urlsplit(service.example).path
    return list(dict.fromkeys(["/", "/@", "/~", example[: example.rfind("/") + 1] or "/"]))


def test_the_check_catches_a_pattern_that_goes_back_over_what_it_read():
    """The guard guarded: a repetition inside a repetition is found, in the textbook shape
    and in the shape a part of a path is one slip away from, and a pattern that reads once
    is not."""
    assert first_slow_path(re.compile(r"/(a+)+b"), ["/"])
    assert first_slow_path(re.compile(r"/(?:[^/\s]+/?)+-"), ["/"])
    assert not first_slow_path(re.compile(r"/[^/\s]+(?:/.*)?"), ["/"])


def test_every_shipped_pattern_reads_an_address_once():
    """A careless edit to the shipped table fails here, by name, in a few seconds."""
    from postulo.plugins.link_services.services import SERVICES

    slow = {
        key: found
        for key, service in SERVICES.items()
        if (found := first_slow_path(service.pattern, starts_of(service)))
    }

    assert not slow, slow


# ----------------------------------------- the migration, when what it reads is broken


def test_the_migration_fails_out_loud_when_the_code_it_reads_has_moved(user, contact, monkeypatch):
    """It asks the registry as it is on the day it runs, through an import. A rename in
    what it imports was caught with everything else and passed over row by row: the
    migration was recorded as run, every row was left *Other*, and nothing said so."""
    from django.apps import apps

    row = add(contact, user, "https://www.linkedin.com/in/cave")

    def moved(*args, **kwargs):
        raise AttributeError("module 'postulo.core.link_services' has no attribute 'guess'")

    monkeypatch.setattr(link_services, "settle", moved)

    with pytest.raises(AttributeError):
        migration_module().guess_services(apps, None)
    row.refresh_from_db()
    assert row.service == ""


def test_the_migration_still_passes_over_a_row_that_is_refused(user, contact, monkeypatch):
    """Nothing chosen refuses nothing as Postulo ships; a rule that did would cost that
    row its guess and no more."""
    from django.apps import apps

    refused = add(contact, user, "https://www.linkedin.com/in/refused")
    read = add(contact, user, "https://www.linkedin.com/in/cave")
    settle = link_services.settle

    def particular(kind, chosen, url, label=""):
        if url.endswith("/refused"):
            raise ValidationError("Not this one.", code="address")
        return settle(kind, chosen, url, label)

    monkeypatch.setattr(link_services, "settle", particular)

    migration_module().guess_services(apps, None)

    refused.refresh_from_db()
    read.refresh_from_db()
    assert (refused.service, read.service) == ("", "linkedin")


# ------------------------------------------------- the name typed beside a fixed prefix (#678)
#
# A row on a service with a prefix asks for the name alone, with the prefix shown in front
# of it. What is stored is still the whole address, so the API, the archive and the candidate
# file are untouched (`tests/test_export.py` and the fingerprint in `test_candidate_file.py`
# hold that), and a stored row that is not the prefix and a name is drawn as it is.

#: A name that is one on each service that has a prefix.
NAMES = {
    "linkedin": "alex-morgan",
    "bluesky": "alex.bsky.social",
    "x": "alex_morgan",
    "xing": "Alex_Morgan",
    "facebook": "alex.morgan",
    "instagram": "alex.morgan",
    "threads": "alex.morgan",
    "youtube": "alexmorgan",
}


def test_each_social_service_with_a_front_says_it_and_the_rest_say_none():
    shipped = link_services.services_for(SOCIAL)
    assert {key for key, service in shipped.items() if service.prefix} == set(NAMES)
    assert shipped["mastodon"].prefix == "", "on any server, so there is no fixed front"
    assert not any(service.prefix for service in link_services.services_for("repository").values())


@pytest.mark.parametrize("key", sorted(NAMES))
def test_a_prefix_is_https_on_the_services_host_and_a_name_after_it_reads_back(key):
    from urllib.parse import urlsplit

    service = link_services.find(key)
    name = NAMES[key]

    assert urlsplit(service.prefix).scheme == "https"
    assert service.hosted(service.prefix)
    assert service.prefix[-1] in "/@"
    assert service.accepts(service.prefix + name)
    assert service.handle(service.prefix + name).removeprefix("@") == name
    assert service.username_in(service.prefix + name) == name
    assert service.example.startswith(service.prefix), "taken from the service's own example"


@pytest.mark.parametrize(
    ("posted", "key", "stored"),
    [
        ("name", "linkedin", "https://www.linkedin.com/in/name"),
        ("  name  ", "linkedin", "https://www.linkedin.com/in/name"),
        ("@name", "threads", "https://www.threads.com/@name"),
        ("name", "threads", "https://www.threads.com/@name"),
        ("name", "x", "https://x.com/name"),
        ("joão-silva", "linkedin", "https://www.linkedin.com/in/joão-silva"),
        # A whole address pasted into the box is the same canonical address.
        ("https://www.linkedin.com/in/name", "linkedin", "https://www.linkedin.com/in/name"),
        ("https://linkedin.com/in/name/?trk=x", "linkedin", "https://www.linkedin.com/in/name"),
        ("linkedin.com/in/name", "linkedin", "https://www.linkedin.com/in/name"),
        ("https://twitter.com/name", "x", "https://x.com/name"),
        ("https://www.threads.net/@name", "threads", "https://www.threads.com/@name"),
        (
            "https://www.linkedin.com/in/jo%C3%A3o",
            "linkedin",
            "https://www.linkedin.com/in/jo%C3%A3o",
        ),
    ],
)
def test_the_name_typed_is_stored_as_the_address_it_makes(posted, key, stored):
    row = form(service=key, label="", url=posted)

    assert row.is_valid(), row.errors
    assert row.cleaned_data["url"] == stored
    assert (row.cleaned_data["service"], row.cleaned_data["label"]) == (key, "")


def test_an_address_that_is_not_a_profile_there_is_pasted_and_kept_as_pasted():
    """A company page is not a person's profile, so it is not rewritten into one."""
    company = "https://www.linkedin.com/company/aperture/"
    row = form(service="linkedin", label="", url=company)

    assert row.is_valid(), row.errors
    assert row.cleaned_data["url"] == company

    post = "https://x.com/name/status/12345"
    tweet = form(service="x", label="", url=post)
    assert tweet.is_valid(), tweet.errors
    assert tweet.cleaned_data["url"] == post


def test_a_pasted_address_whose_query_is_the_profile_is_kept_as_pasted():
    """Facebook's profile.php holds the person in its query string: dropping the query as
    tracking would store a link to nobody."""
    pasted = "https://www.facebook.com/profile.php?id=100001234567890"
    row = form(service="facebook", label="", url=pasted)

    assert row.is_valid(), row.errors
    assert row.cleaned_data["url"] == pasted


@pytest.mark.parametrize(
    ("key", "typed"),
    [
        ("linkedin", "two words"),
        ("linkedin", "a?b"),
        ("linkedin", "a#b"),
        ("linkedin", "a\\b"),
        ("x", "a_name_that_is_too_long"),
        ("x", "bad-name"),
        ("instagram", "x" * 31),
        ("instagram", "no spaces"),
        ("bluesky", "nodots"),
        ("youtube", "ab"),
        ("threads", "@@name"),
    ],
)
def test_a_name_that_breaks_the_services_rule_is_refused_beside_the_box_saying_the_rule(key, typed):
    row = form(service=key, label="", url=typed)

    assert not row.is_valid()
    assert list(row.errors) == ["url"]
    message = row.errors["url"][0]
    assert "That is not a username on " in message
    assert link_services.find(key).example in message


def test_the_handle_rules_are_written_where_each_service_publishes_them():
    """Each rule added to a pattern carries a comment naming where it is written."""
    source = (ROOT / "src/postulo/plugins/link_services/services.py").read_text(encoding="utf-8")

    for rule, where in (
        ("BLUESKY_HANDLE", "atproto.com/specs/handle"),
        ("INSTAGRAM_NAME", "Changing your username"),
        ("YOUTUBE_HANDLE", "Handles on YouTube"),
    ):
        assert where in source
        assert source.index(where) < source.index(f"{rule} = ")


def test_a_row_on_a_service_with_no_front_is_the_whole_address_box_as_before():
    mastodon = form(service="mastodon", label="", url="https://fosstodon.org/@alex")

    assert mastodon.is_valid(), mastodon.errors
    assert mastodon.cleaned_data["url"] == "https://fosstodon.org/@alex"
    other = form(service="other", label="Forum", url="forum.example.org/u/alex")
    assert other.is_valid() and other.cleaned_data["url"] == "https://forum.example.org/u/alex"
    assert not form(REPOSITORY, service="github", label="", url="name/project").is_valid()


def test_a_stored_row_in_the_canonical_form_is_drawn_as_its_name_and_saves_unchanged(user):
    saved = add(user.profile, user, "https://www.linkedin.com/in/alex", service="linkedin")
    drawn = web_links.WebLinkForm(instance=saved, kind=SOCIAL)
    box = str(drawn["url"])

    assert drawn.prefix_of_service == "https://www.linkedin.com/in/"
    assert 'value="alex"' in box and 'type="text"' in box
    assert 'aria-describedby="id_url_prefix"' in box
    assert not drawn.kept_as_written

    untouched = web_links.WebLinkForm(
        data={"service": "linkedin", "label": "", "url": "alex"}, instance=saved, kind=SOCIAL
    )
    assert untouched.is_valid(), untouched.errors
    assert "url" not in untouched.changed_data
    moved = web_links.WebLinkForm(
        data={"service": "linkedin", "label": "", "url": "alex2"}, instance=saved, kind=SOCIAL
    )
    assert moved.is_valid() and moved.changed_data == ["url"]
    assert moved.cleaned_data["url"] == "https://www.linkedin.com/in/alex2"


@pytest.mark.parametrize(
    "stored",
    [
        "https://linkedin.com/in/alex",
        "https://www.linkedin.com/in/alex/",
        "https://www.linkedin.com/in/alex?trk=x",
        "https://www.linkedin.com/company/aperture",
        "https://pt.linkedin.com/in/alex",
    ],
)
def test_a_stored_row_that_is_not_the_prefix_and_a_name_is_drawn_whole_and_kept(user, stored):
    saved = add(user.profile, user, stored, service="linkedin")
    drawn = web_links.WebLinkForm(instance=saved, kind=SOCIAL)

    assert drawn.prefix_of_service == ""
    assert f'value="{stored}"' in str(drawn["url"]) and 'type="url"' in str(drawn["url"])
    assert drawn.kept_as_written and drawn.service_prefix == "https://www.linkedin.com/in/"

    untouched = web_links.WebLinkForm(
        data={"service": "linkedin", "label": "", "url": stored}, instance=saved, kind=SOCIAL
    )
    assert untouched.is_valid(), untouched.errors
    assert untouched.changed_data == []
    assert untouched.cleaned_data["url"] == stored, "nothing is rewritten"


def test_retyping_the_name_moves_a_kept_row_to_the_short_form(user):
    saved = add(user.profile, user, "https://linkedin.com/in/alex/", service="linkedin")
    retyped = web_links.WebLinkForm(
        data={"service": "linkedin", "label": "", "url": "alex"}, instance=saved, kind=SOCIAL
    )

    assert retyped.is_valid(), retyped.errors
    assert retyped.cleaned_data["url"] == "https://www.linkedin.com/in/alex"


def test_a_name_that_was_refused_comes_back_in_the_box_as_it_was_typed():
    row = form(service="linkedin", label="", url="two words")
    row.full_clean()

    assert row.prefix_of_service == "https://www.linkedin.com/in/"
    assert 'value="two words"' in str(row["url"])


@pytest.mark.parametrize("page", ["profile", "contact"])
def test_the_page_draws_the_prefix_beside_the_box_and_saves_the_address(
    client, user, contact, page
):
    holder = user.profile if page == "profile" else contact
    add(holder, user, "https://www.threads.com/@alex", service="threads", primary=True)
    add(holder, user, "https://www.linkedin.com/company/aperture", service="linkedin")
    client.force_login(user)
    url = (
        reverse("accounts:profile")
        if page == "profile"
        else reverse("jobs:contact_update", args=[contact.pk])
    )

    html = client.get(url).content.decode()
    named, kept, new = (row_markup(html, "social", index) for index in range(3))

    assert re.search(
        r'<span data-align="start" id="id_social_profiles-0-url_prefix">'
        r'<bdi dir="ltr">https://www.threads.com/@</bdi></span>',
        named,
    )
    assert 'aria-describedby="id_social_profiles-0-url_prefix"' in named
    assert 'data-address-label data-username="Username" data-address="Address">Username<' in named
    assert "data-kept-as-written" not in named
    # Kept as written: whole box, prefix put away, and the line that says so.
    assert 'id="id_social_profiles-1-url_prefix" hidden' in kept
    assert "data-kept-as-written" in kept and ">Address<" in kept
    assert "aria-describedby" not in re.search(r"<input[^>]*-1-url[^>]*>", kept)[0]
    # A new row starts on nothing, so with scripts off it is the whole-address box.
    assert 'id="id_social_profiles-2-url_prefix" hidden' in new and ">Address<" in new
    # Each option carries its prefix for the script, and Mastodon has none.
    assert 'data-prefix="https://www.linkedin.com/in/"' in named
    assert not re.search(r'value="mastodon"[^>]*data-prefix', named)

    posted = rows(
        "social_profiles",
        {"service": "linkedin", "label": "", "url": "alex-morgan"},
        {"service": "threads", "label": "", "url": "@sam"},
    )
    posted |= rows("repositories") | rows("websites")
    if page == "profile":
        response = client.post(reverse("accounts:profile"), {**PROFILE_POST, **posted})
    else:
        response = client.post(url, contact_post(contact, **posted))
    assert response.status_code == 302, response.content.decode()[:800]
    assert set(holder.web_links.filter(kind=SOCIAL).values_list("url", flat=True)) >= {
        "https://www.linkedin.com/in/alex-morgan",
        "https://www.threads.com/@sam",
    }


def test_the_prefix_is_text_in_the_page_and_not_a_control(client, user):
    add(user.profile, user, "https://www.linkedin.com/in/alex", service="linkedin")
    client.force_login(user)

    row = row_markup(client.get(reverse("accounts:profile")).content.decode(), "social")

    group = re.search(r'<div class="input-group" data-address-group>(.*?)</div>', row, re.S)[1]
    assert re.findall(r"<(input|select|button|textarea)\b", group) == ["input"], (
        "one control in the group: the box, and the prefix is a span"
    )


def test_the_single_box_with_several_profiles_off_has_no_prefix(user):
    """Switching *Several social profiles* off is the way Postulo was before services."""
    from django import forms

    box = forms.Form()
    web_links.add_single_boxes(box, user)

    for field in box.fields.values():
        assert type(field) is forms.URLField
        assert "data-address-box" not in field.widget.attrs
