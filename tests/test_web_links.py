"""Several addresses on the web per holder, three kinds, and what switching one off does not do.

Three feature plugins over one table (#189), and the same promise the telephone rows make:
switching one off deletes nothing. The tests that matter most count rows after a switch, and
count them per kind, because a switch for one kind has to leave the other two exactly as
they were.
"""

from __future__ import annotations

import json

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.core import web_links
from postulo.core.models import WebLink
from postulo.plugins.repositories import REPOSITORIES
from postulo.plugins.social_profiles import SOCIAL_PROFILES
from postulo.plugins.websites import WEBSITES

pytestmark = pytest.mark.django_db

SOCIAL = WebLink.Kind.SOCIAL
REPOSITORY = WebLink.Kind.REPOSITORY
WEBSITE = WebLink.Kind.WEBSITE
EVERY_SWITCH = (SOCIAL_PROFILES, REPOSITORIES, WEBSITES)

PROFILE_POST = {"first_name": "Alex", "last_name": "Morgan", "headline": "", "location": ""}


@pytest.fixture
def contact(user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def add(holder, owner, url, *, kind=SOCIAL, label="", primary=False):
    return WebLink.objects.create(
        owner=owner, holder=holder, kind=kind, label=label, url=url, is_primary=primary
    )


def switch_off(person, *plugins):
    person.profile.plugins_off = list(plugins or EVERY_SWITCH)
    person.profile.save(update_fields=["plugins_off"])


def rows(prefix, *entries, primary=None):
    """The POST one block of link rows sends, management form and all."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(entries)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    if primary is not None:
        data[f"{prefix}-primary"] = f"{prefix}-{primary}"
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


# ------------------------------------------------------------------ the invariant


def test_a_holder_may_have_several_links_of_each_kind(contact, user):
    add(contact, user, "https://codeberg.org/cave", kind=REPOSITORY)
    add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL)
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)

    listed = [(row.kind, row.url) for row in contact.web_links.all()]
    assert listed[0] == ("social", "https://www.linkedin.com/in/cave"), "the primary first"
    assert len(listed) == 3


def test_only_one_of_a_kind_can_be_the_primary(contact, user):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    add(contact, user, "https://cave.example", kind=WEBSITE, primary=True)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL, primary=True)


def test_making_one_primary_takes_it_off_the_other_of_its_kind(contact, user):
    first = add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    second = add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL)
    site = add(contact, user, "https://cave.example", kind=WEBSITE, primary=True)

    web_links.set_primary(second)

    first.refresh_from_db()
    second.refresh_from_db()
    site.refresh_from_db()
    assert not first.is_primary and second.is_primary
    assert site.is_primary, "a different kind, so a different question"


def test_an_address_is_listed_once_per_holder_and_freely_between_holders(contact, user, other_user):
    from postulo.jobs.models import Company, Contact

    add(contact, user, "https://aperture.example", kind=WEBSITE, primary=True)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            add(contact, user, "https://aperture.example", kind=SOCIAL)

    colleague = Contact.objects.create(owner=user, company=contact.company, name="Caroline")
    add(colleague, user, "https://aperture.example", kind=WEBSITE, primary=True)
    theirs = Contact.objects.create(
        owner=other_user, company=Company.objects.create(owner=other_user, name="BM"), name="G"
    )
    add(theirs, other_user, "https://aperture.example", kind=WEBSITE, primary=True)
    assert WebLink.objects.filter(url="https://aperture.example").count() == 3, (
        "a website is not a telephone number: two people share one without disclosing it"
    )


def test_a_row_with_no_name_is_shown_by_its_host(contact, user):
    unnamed = add(contact, user, "https://www.codeberg.org/cave/thing", kind=REPOSITORY)
    named = add(contact, user, "https://github.com/cave", kind=REPOSITORY, label="GitHub")

    assert unnamed.display == "codeberg.org" and str(unnamed) == "codeberg.org"
    assert named.display == "GitHub"


# ---------------------------------------------------- what switching it off does not do


def test_each_kind_has_its_own_switch(contact, user):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL)
    add(contact, user, "https://github.com/cave", kind=REPOSITORY, primary=True)
    add(contact, user, "https://codeberg.org/cave", kind=REPOSITORY)

    switch_off(user, SOCIAL_PROFILES)

    shown = [(row.kind, row.url) for row in web_links.links_for(contact, user)]
    assert shown == [
        ("social", "https://www.linkedin.com/in/cave"),
        ("repository", "https://github.com/cave"),
        ("repository", "https://codeberg.org/cave"),
    ], "the social primary alone; every repository, because that switch is still on"
    kept = [(block.kind, count) for block, count in web_links.kept_back(contact, user)]
    assert kept == [("social", 1)], "and the person is told, about that kind only"


def test_switching_it_off_shows_the_primary_and_keeps_the_rest(contact, user):
    add(contact, user, "https://cave.example", kind=WEBSITE, primary=True)
    add(contact, user, "https://blog.cave.example", kind=WEBSITE)
    add(contact, user, "https://old.cave.example", kind=WEBSITE)

    assert len(web_links.links_for(contact, user, WEBSITE)) == 3

    switch_off(user, WEBSITES)

    shown = web_links.links_for(contact, user, WEBSITE)
    assert [row.url for row in shown] == ["https://cave.example"], "the primary, and only it"
    assert contact.web_links.count() == 3, "switching a plugin off deletes nothing"


def test_switching_it_back_on_finds_them_unchanged(contact, user):
    add(contact, user, "https://cave.example", kind=WEBSITE, primary=True)
    add(contact, user, "https://blog.cave.example", kind=WEBSITE, label="Blog")

    switch_off(user)
    user.profile.plugins_off = []
    user.profile.save(update_fields=["plugins_off"])

    rows_after = web_links.links_for(contact, user)
    assert [(row.url, row.label) for row in rows_after] == [
        ("https://cave.example", ""),
        ("https://blog.cave.example", "Blog"),
    ]


# --------------------------------------------------------------------- the pages


def test_the_profile_page_saves_the_rows_of_each_kind(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            **rows(
                "social_profiles",
                {"label": "", "url": "https://www.linkedin.com/in/alex"},
                {"label": "Mastodon", "url": "https://mastodon.example/@alex"},
                primary=1,
            ),
            **rows("websites", {"label": "", "url": "https://alex.example"}),
        },
    )

    assert response.status_code == 302
    listed = {(row.kind, row.url, row.is_primary) for row in user.profile.web_links.all()}
    assert listed == {
        ("social", "https://www.linkedin.com/in/alex", False),
        ("social", "https://mastodon.example/@alex", True),
        ("website", "https://alex.example", True),
    }, "the radio named a row with no key yet; a block with no radio makes its first row primary"

    html = client.get(reverse("accounts:profile")).content.decode()
    for kind in ("social", "repository", "website"):
        assert f'data-web-links="{kind}"' in html, "every kind is a block while its switch is on"


def test_the_same_address_twice_in_one_post_is_refused(client, user):
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            **PROFILE_POST,
            **rows(
                "websites",
                {"label": "", "url": "https://alex.example"},
                {"label": "Again", "url": "https://alex.example"},
            ),
        },
    )

    assert response.status_code == 200, "the form came back rather than saving"
    assert "already listed" in response.content.decode()
    assert not user.profile.web_links.exists()


def test_the_profile_page_offers_one_box_per_kind_while_the_features_are_off(client, user):
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    for name in ("social_profile", "repository", "website"):
        assert f'name="{name}"' in html
    assert "data-web-links" not in html, "a box and a block would be two answers to one question"

    response = client.post(
        reverse("accounts:profile"),
        {**PROFILE_POST, "website": "https://alex.example", "social_profile": "", "repository": ""},
    )

    assert response.status_code == 302
    only = user.profile.web_links.get()
    assert (only.kind, only.url, only.is_primary) == ("website", "https://alex.example", True)


def test_saving_the_one_box_leaves_the_hidden_links_alone(client, user, contact):
    """The rows this person cannot see are not theirs to lose by saving a form."""
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    hidden = add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL)
    switch_off(user)
    client.force_login(user)

    client.post(
        reverse("jobs:contact_update", args=[contact.pk]),
        contact_post(contact, social_profile="https://www.linkedin.com/in/cave-johnson"),
    )

    hidden.refresh_from_db()
    assert hidden.url == "https://mastodon.example/@cave" and not hidden.is_primary, "untouched"
    primary = web_links.primary_for(contact, SOCIAL)
    assert primary.url == "https://www.linkedin.com/in/cave-johnson", "the primary moved"
    assert contact.web_links.count() == 2


def test_clearing_the_box_removes_the_primary_and_promotes_nothing(client, user, contact):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    hidden = add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL)
    switch_off(user)
    client.force_login(user)

    client.post(
        reverse("jobs:contact_update", args=[contact.pk]),
        contact_post(contact, social_profile=""),
    )

    assert web_links.primary_for(contact, SOCIAL) is None
    hidden.refresh_from_db()
    assert not hidden.is_primary, (
        "a hidden row appearing because a box was emptied would be a surprise"
    )


def test_the_page_says_the_hidden_ones_are_still_there(client, user, contact):
    add(contact, user, "https://github.com/cave", kind=REPOSITORY, primary=True)
    add(contact, user, "https://codeberg.org/cave", kind=REPOSITORY)
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()
    assert "Some code repositories are kept for this contact and not shown" in html
    assert "Some social profiles are kept" not in html, "nothing of that kind is kept back"


def test_the_company_page_lists_a_contacts_links_by_the_same_rule(client, user, contact):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL, label="Mastodon")
    client.force_login(user)
    page = reverse("jobs:company_detail", args=[contact.company_id])

    html = client.get(page).content.decode()
    assert 'href="https://www.linkedin.com/in/cave"' in html
    assert 'href="https://mastodon.example/@cave"' in html and ">Mastodon<" in html

    switch_off(user, SOCIAL_PROFILES)
    html = client.get(page).content.decode()
    assert 'href="https://www.linkedin.com/in/cave"' in html
    assert "mastodon.example" not in html, "the primary alone while the switch is off"


# ------------------------------------------------------------------- everything else


def test_deleting_the_holder_takes_its_links_with_it(contact, user):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    add(contact, user, "https://cave.example", kind=WEBSITE, primary=True)
    contact.delete()
    assert not WebLink.objects.exists(), "a generic relation is what gives the cascade"


def test_a_document_prints_the_primary_of_each_kind_and_not_a_list(user):
    from postulo.documents.rendering import contact_details

    profile = user.profile
    add(profile, user, "https://www.linkedin.com/in/alex", kind=SOCIAL, primary=True)
    add(profile, user, "https://mastodon.example/@alex", kind=SOCIAL)
    add(profile, user, "https://github.com/alex", kind=REPOSITORY, primary=True)
    add(profile, user, "https://alex.example", kind=WEBSITE, primary=True)
    switch_off(user)

    details = contact_details(user)
    assert details["linkedin_url"] == "https://www.linkedin.com/in/alex"
    assert details["source_repo_url"] == "https://github.com/alex"
    assert details["website"] == "https://alex.example"


def test_an_export_carries_every_link_even_the_hidden_ones(user, contact):
    """An export is what somebody leaves with, so it carries what is recorded."""
    from postulo.core import export as export_module

    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    add(contact, user, "https://mastodon.example/@cave", kind=SOCIAL, label="Mastodon")
    add(user.profile, user, "https://alex.example", kind=WEBSITE, primary=True)
    switch_off(user)

    document = export_module.build_document(user)
    exported = document["companies"][0]["contacts"][0]["web_links"]
    assert exported == [
        {
            "kind": "social",
            "label": "",
            "url": "https://www.linkedin.com/in/cave",
            "is_primary": True,
        },
        {
            "kind": "social",
            "label": "Mastodon",
            "url": "https://mastodon.example/@cave",
            "is_primary": False,
        },
    ]
    assert document["account"]["profile"]["web_links"] == [
        {"kind": "website", "label": "", "url": "https://alex.example", "is_primary": True}
    ]
    assert "linkedin_url" not in document["companies"][0]["contacts"][0]
    assert "website" not in document["account"]["profile"]


def test_an_older_archive_still_carries_its_columns_in(user, contact):
    from postulo.core.importer import _link_rows, _restore_web_links

    entry = {
        "headline": "Founder",
        "website": "https://cave.example",
        "linkedin_url": "https://www.linkedin.com/in/cave",
        "source_repo_url": "",
    }
    found = _link_rows(entry)
    assert found == [
        {"kind": "website", "url": "https://cave.example", "is_primary": True},
        {"kind": "social", "url": "https://www.linkedin.com/in/cave", "is_primary": True},
    ]
    assert entry == {"headline": "Founder"}, "every column is taken off the record"

    _restore_web_links(contact, user, found)
    assert web_links.primary_for(contact, WEBSITE).url == "https://cave.example"
    assert web_links.primary_for(contact, SOCIAL).url == "https://www.linkedin.com/in/cave"


def test_the_api_keeps_the_linkedin_address_where_it_was(client, user, contact):
    """A client written against the single address keeps working, and sees the list beside it."""
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=("write", "read"))
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    response = client.post(
        f"/api/v1/companies/{contact.company_id}/contacts",
        data=json.dumps(
            {"name": "Caroline", "linkedin_url": "https://www.linkedin.com/in/caroline"}
        ),
        content_type="application/json",
        **bearer,
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["linkedin_url"] == "https://www.linkedin.com/in/caroline"
    assert body["web_links"] == [
        {
            "kind": "social",
            "label": "",
            "url": "https://www.linkedin.com/in/caroline",
            "is_primary": True,
        }
    ]


def test_the_three_features_are_plugins_an_administrator_can_see(client, django_user_model):
    from postulo.plugins import registry

    names = {getattr(plugin, "name", "") for plugin in registry.plugins("feature")}
    assert set(EVERY_SWITCH) <= names

    admin = django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    for label in ("Several social profiles", "Several code repositories", "Several websites"):
        assert label in html


def test_one_persons_links_are_never_another_persons(user, other_user, contact):
    add(contact, user, "https://www.linkedin.com/in/cave", kind=SOCIAL, primary=True)
    assert not WebLink.objects.for_user(other_user).exists()
    assert WebLink.objects.for_user(user).count() == 1


def test_a_contact_can_be_created_with_its_links_in_one_go(client, user):
    """A create view has no holder when the rows are built; they bind to it on save."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    response = client.post(
        reverse("jobs:contact_create"),
        {
            "name": "Caroline",
            "role": "",
            "company": company.pk,
            "email": "",
            "notes": "",
            **rows("websites", {"label": "", "url": "https://caroline.example"}),
        },
    )

    assert response.status_code == 302, response.content.decode()[:500]
    contact = Contact.objects.get(owner=user, name="Caroline")
    assert web_links.primary_for(contact, WEBSITE).url == "https://caroline.example"
    assert contact.web_links.get().owner == user
