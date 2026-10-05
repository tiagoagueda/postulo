"""Choosing which identifiers are shown and in what order, under Settings -> Appearance (#672).

Modelled on `tests/test_navigation.py`: two lists on the profile, what was placed and what
was switched off, never what was offered. Hiding is a way of drawing, never of keeping.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO

import pytest
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier, Profile
from postulo.core import export, identifier_order, identifiers, importer, navigation
from postulo.documents import printing
from postulo.documents.models import CV
from postulo.jobs.models import Company, CompanyIdentifier

pytestmark = pytest.mark.django_db

ORCID = "0000-0002-1825-0097"
LEI = "5493001KJTIIGC8Y1R12"


def appearance(client, **overrides):
    """What the page posts: the navigation's fields, and the identifier list as the page
    draws it (every key, every switch on) unless told otherwise."""
    keys = identifier_order.default_order()
    values = {
        "theme": "system",
        "density": "comfortable",
        "quiet_after_days": 14,
        "navigation": list(navigation.HIDEABLE),
        "ident_order": keys,
        "identifiers_shown": keys,
    }
    values.update(overrides)
    return client.post(reverse("settings:appearance"), values, follow=True)


def a_company(user):
    company = Company.objects.create(owner=user, name="Acme")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="lei", value=LEI)
    return company


def drawn_schemes(html: str) -> list[str]:
    block = re.search(r"<dl[^>]*data-identifiers>(.*?)</dl>", html, re.S).group(1)
    return re.findall(r"<dt[^>]*>\s*(.*?)\s*</dt>", block, re.S)


# ------------------------------------------------------------------ the helper


def test_the_default_order_is_the_registrys(user):
    assert identifier_order.order_of(user.profile) == list(identifiers.registry())
    assert identifier_order.hidden_keys(user.profile) == set()
    assert identifier_order.to_store(identifier_order.default_order()) == []


def test_placed_keys_come_first_and_a_scheme_added_later_after_them():
    order = identifier_order.complete(["wikidata", "lei"])
    assert order[:2] == ["wikidata", "lei"]
    assert set(order) == set(identifiers.registry())
    assert order[2:] == [k for k in identifiers.registry() if k not in ("wikidata", "lei")]


def test_a_stored_list_is_believed_only_as_far_as_the_registry_goes():
    assert identifier_order.known_keys(["nonsense", "lei", "lei", 5, None, "wikidata"]) == [
        "lei",
        "wikidata",
    ]
    assert identifier_order.known_keys({"lei": "first"}) == []
    assert identifier_order.known_keys("lei") == []


def test_a_forged_move_is_passed_over():
    order = identifier_order.default_order()
    assert identifier_order.move(order, "no-such", "up") == order
    assert identifier_order.move(order, "lei", "sideways") == order
    assert identifier_order.move(order, order[0], "up") == order, "past the end is no wrap"
    assert not identifier_order.can_move(order, order[-1], "down")


def test_a_forged_post_changes_nothing_it_should_not(client, user):
    client.force_login(user)
    appearance(
        client,
        ident_order=["bogus", "lei"],
        identifiers_shown=["bogus"],
        ident_move="up:bogus",
    )
    assert client.get(reverse("settings:appearance")).status_code == 200
    user.profile.refresh_from_db()
    assert "bogus" not in user.profile.identifier_order
    assert "bogus" not in user.profile.hidden_identifiers
    assert set(user.profile.identifier_order) <= set(identifiers.registry())


def test_a_post_without_the_list_changes_nothing(client, user):
    client.force_login(user)
    user.profile.hidden_identifiers = ["lei"]
    user.profile.save()
    client.post(
        reverse("settings:appearance"),
        {
            "theme": "system",
            "density": "comfortable",
            "quiet_after_days": 14,
            "navigation": list(navigation.HIDEABLE),
        },
    )
    user.profile.refresh_from_db()
    assert user.profile.hidden_identifiers == ["lei"]


# ------------------------------------------------------------ the settings page


def test_the_page_lists_every_scheme_with_a_switch_and_arrows(client, user):
    client.force_login(user)
    html = client.get(reverse("settings:appearance")).content.decode()
    for key in identifiers.registry():
        assert f'id="ident-row-{key}"' in html
        assert f'value="up:{key}"' in html
        assert re.search(
            rf'<input[^>]*name="identifiers_shown"[^>]*value="{key}"[^>]*checked', html
        )
    assert 'name="ident_reset"' not in html, "nothing to go back to yet"


def test_an_arrow_moves_a_scheme_and_the_order_is_kept(client, user):
    client.force_login(user)
    keys = identifier_order.default_order()
    response = appearance(client, ident_move=f"up:{keys[1]}")
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.identifier_order[:2] == [keys[1], keys[0]]
    assert "is now number 1 in the identifiers" in response.content.decode()
    assert 'name="ident_reset"' in client.get(reverse("settings:appearance")).content.decode()


def test_a_refused_page_comes_back_in_the_order_it_was_posted_in(client, user):
    client.force_login(user)
    keys = identifier_order.default_order()
    posted = [keys[1], keys[0], *keys[2:]]
    response = appearance(client, ident_order=posted, quiet_after_days=-3)
    html = response.content.decode()
    assert html.index(f'id="ident-row-{keys[1]}"') < html.index(f'id="ident-row-{keys[0]}"')


def test_back_to_the_usual_order_stores_nothing(client, user):
    client.force_login(user)
    keys = identifier_order.default_order()
    appearance(client, ident_move=f"down:{keys[0]}")
    user.profile.refresh_from_db()
    assert user.profile.identifier_order
    appearance(client, ident_reset="1")
    user.profile.refresh_from_db()
    assert user.profile.identifier_order == []


def test_switching_a_scheme_off_records_it_as_hidden(client, user):
    client.force_login(user)
    keys = identifier_order.default_order()
    appearance(client, identifiers_shown=[k for k in keys if k != "lei"])
    user.profile.refresh_from_db()
    assert user.profile.hidden_identifiers == ["lei"]


# --------------------------------------------------------- where it is applied


def test_the_company_page_follows_the_order_and_counts_what_is_hidden(client, user):
    company = a_company(user)
    client.force_login(user)
    url = reverse("jobs:company_detail", args=[company.pk])
    assert len(drawn_schemes(client.get(url).content.decode())) == 2

    for first in ("wikidata", "lei"):
        user.profile.identifier_order = identifier_order.complete([first])
        user.profile.save()
        drawn = drawn_schemes(client.get(url).content.decode())
        assert drawn[0] == identifiers.label_for(first)

    user.profile.hidden_identifiers = ["lei"]
    user.profile.save()
    html = client.get(url).content.decode()
    assert LEI not in html
    assert "Q95" in html
    assert "data-identifiers-hidden" in html and "1 identifier hidden" in html
    assert f"{reverse('jobs:company_update', args=[company.pk])}#section-identifiers" in html


def test_a_hidden_scheme_stays_in_the_edit_form_and_the_export(client, user):
    company = a_company(user)
    user.profile.hidden_identifiers = ["lei"]
    user.profile.save()
    client.force_login(user)
    form = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    assert LEI in form
    assert LEI in json.dumps(export.build_document(user), default=str)


def test_a_company_with_only_hidden_identifiers_still_says_so(client, user):
    company = Company.objects.create(owner=user, name="Solo")
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="lei", value=LEI)
    user.profile.hidden_identifiers = ["lei"]
    user.profile.save()
    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert "1 identifier hidden" in html


def test_your_details_and_the_cvs_choice_follow_the_order(client, user):
    profile = user.profile
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=ORCID)
    PersonIdentifier.objects.create(profile=profile, scheme="wikidata", value="Q95")
    profile.identifier_order = identifier_order.complete(["wikidata", "orcid"])
    profile.hidden_identifiers = ["orcid"]
    profile.save()
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    # Hidden is not removed: the value is still in the form, after the one placed first.
    assert ORCID in html and "Q95" in html
    assert html.index("Q95") < html.index(ORCID)

    assert [row.scheme for row in printing.offered(user, "identifiers")] == ["wikidata", "orcid"]

    cv = CV.objects.create(owner=user, name="Main")
    form = client.get(reverse("documents:cv_update", args=[cv.pk])).context["form"]
    labels = [str(label) for _pk, label in form.fields["identifier_rows"].choices]
    assert len(labels) == 2, "a switched-off scheme is still a row a CV may print"
    assert "Q95" in labels[0]


def test_rows_of_one_scheme_keep_their_order_by_value_inside_their_place(user):
    company = Company.objects.create(owner=user, name="Many")
    for value in ("b-2", "a-1"):
        CompanyIdentifier.objects.create(
            owner=user, company=company, scheme="other", label=value, value=value
        )
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="lei", value=LEI)
    user.profile.identifier_order = identifier_order.complete(["other"])
    user.profile.save()
    rows = identifier_order.arranged(company.identifiers.all(), user.profile)
    assert [row.value for row in rows] == ["a-1", "b-2", LEI]


def test_the_arrangement_is_one_persons_own(client, user, other_user):
    other_user.profile.hidden_identifiers = ["lei"]
    other_user.profile.identifier_order = ["lei"]
    other_user.profile.save()
    company = a_company(user)
    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()
    assert LEI in html, "somebody else's switches never reach this page"


def test_the_companies_table_lists_identifier_columns_in_the_order(client, user):
    user.profile.identifier_order = identifier_order.complete(["wikidata"])
    user.profile.save()
    client.force_login(user)
    response = client.get(reverse("jobs:company_list"))
    keys = [row.column.key for row in response.context["table"].chooser]
    ids = [k for k in keys if k.startswith("id_")]
    assert ids[0] == "id_wikidata"


# ---------------------------------------------------------------- the archive


def test_the_arrangement_travels_with_the_account(user, other_user):
    profile = user.profile
    profile.identifier_order = identifier_order.move([], "wikidata", "up")
    profile.hidden_identifiers = ["lei"]
    profile.save(update_fields=["identifier_order", "hidden_identifiers"])

    stored = export.build_document(user)["account"]["profile"]
    assert stored["identifier_order"] == profile.identifier_order
    assert stored["hidden_identifiers"] == ["lei"]

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))
    restored = Profile.objects.get(user=other_user)
    assert restored.identifier_order == profile.identifier_order
    assert restored.hidden_identifiers == ["lei"]


def test_an_archive_naming_a_scheme_this_instance_lacks_is_passed_over(client, user, other_user):
    document = export.build_document(user)
    document["account"]["profile"]["identifier_order"] = ["from-elsewhere", "lei", {"x": 1}]
    document["account"]["profile"]["hidden_identifiers"] = "lei"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer))
    restored = Profile.objects.get(user=other_user)
    assert restored.identifier_order == ["lei"]
    assert restored.hidden_identifiers == []
    client.force_login(other_user)
    assert client.get(reverse("settings:appearance")).status_code == 200
