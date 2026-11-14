"""A place is shown as the city alone while every place is in one country (#1249).

The country stays in what is stored, exported and printed on a document; only what a
person reads on a page loses it, and only while the countries of their places are one.
"""

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.core import export, place_display, postal
from postulo.core.models import PostalAddress
from postulo.jobs.models import Company, JobPosting


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return user


def company_in(user, name, town, country, **extra):
    company = Company.objects.create(owner=user, name=name)
    PostalAddress.objects.create(
        owner=user,
        holder=company,
        kind="work",
        municipality=town,
        country=country,
        is_primary=True,
        **extra,
    )
    return company


def fresh(user):
    """The user as a new request holds it: nothing remembered from the last page."""
    return type(user).objects.get(pk=user.pk)


def test_one_country_shows_the_city_alone_in_every_place(client, signed_in):
    """Paris, France on every row was the same word to skip a hundred times (#1249)."""
    paris = company_in(signed_in, "Acme", "Paris", "FR", street="1 rue de Rivoli")
    company_in(signed_in, "Brie", "Lyon", "FR")
    listing = JobPosting.objects.create(
        owner=signed_in, company=paris, title="Engineer", location="Paris, France"
    )

    page = client.get(reverse("jobs:company_list")).content.decode()
    assert "Paris" in page and "Lyon" in page and "France" not in page
    detail = client.get(reverse("jobs:company_detail", args=[paris.pk])).content.decode()
    assert "1 rue de Rivoli, Paris" in detail and "France" not in detail
    posting = client.get(reverse("jobs:posting_detail", args=[listing.pk])).content.decode()
    assert "Paris" in posting and "France" not in posting
    found = client.get(reverse("jobs:company_list") + "?q=Acme").content.decode()
    assert "France" not in found
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris"


def test_a_second_country_brings_it_back_everywhere_and_removing_it_takes_it_away(
    client, signed_in
):
    """One company elsewhere is enough to put the country on every row, at once (#1249)."""
    company_in(signed_in, "Acme", "Paris", "FR")
    berlin = company_in(signed_in, "Berlin GmbH", "Berlin", "DE")
    page = client.get(reverse("jobs:company_list")).content.decode()
    assert "Paris, France" in page and "Berlin, Germany" in page
    berlin.delete()
    page = client.get(reverse("jobs:company_list")).content.decode()
    assert "France" not in page


def test_an_address_with_no_country_does_not_change_the_answer(signed_in):
    """A company with none, or an unknown code, never forces the country onto the others."""
    paris = company_in(signed_in, "Acme", "Paris", "FR")
    company_in(signed_in, "Nowhere", "Somewhere", "")
    company_in(signed_in, "Odd", "Elsewhere", "ZZ")
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris"


def test_a_shared_company_and_the_persons_own_address_count(user, other_user):
    """The set holds what the person can see and where they live, not only what they own."""
    from postulo.jobs.models import Share

    mine = company_in(user, "Acme", "Paris", "FR")
    theirs = company_in(other_user, "Berlin GmbH", "Berlin", "DE")
    assert place_display.company_place(mine, fresh(user)) == "Paris"
    Share.objects.create(company=theirs, grantee_user=user, scope=Share.Scope.READ)
    assert place_display.company_place(mine, fresh(user)) == "Paris, France"
    other = fresh(other_user)
    assert place_display.company_place(theirs, other) == "Berlin"


def test_the_persons_own_address_in_another_country_keeps_it(user):
    """Their own address is one of the places: a French list beside a home in Spain shows both."""
    company_in(user, "Acme", "Paris", "FR")
    PostalAddress.objects.create(
        owner=user, holder=user.profile, kind="home", municipality="Madrid", country="ES"
    )
    paris = Company.objects.get(name="Acme")
    assert place_display.company_place(paris, fresh(user)) == "Paris, France"


def test_the_setting_has_three_values(client, signed_in):
    """Always keeps the country, never drops it, automatically follows the rule (#1249)."""
    paris = company_in(signed_in, "Acme", "Paris", "FR")
    berlin = company_in(signed_in, "Berlin GmbH", "Berlin", "DE")
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris, France"
    signed_in.profile.show_country = "never"
    signed_in.profile.save()
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris"
    assert place_display.company_place(berlin, fresh(signed_in)) == "Berlin"
    berlin.delete()
    signed_in.profile.show_country = "always"
    signed_in.profile.save()
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris, France"
    signed_in.profile.show_country = "auto"
    signed_in.profile.save()
    assert place_display.company_place(paris, fresh(signed_in)) == "Paris"


def test_the_setting_is_chosen_on_the_appearance_page(client, signed_in):
    """It is a radio group of three on Settings, Appearance, and a post keeps the choice."""
    page = client.get(reverse("settings:appearance")).content.decode()
    assert 'name="show_country"' in page and 'value="never"' in page
    from tests.test_brand import APPEARANCE

    client.post(reverse("settings:appearance"), {**APPEARANCE, "show_country": "never"})
    assert fresh(signed_in).profile.show_country == "never"
    client.post(reverse("settings:appearance"), APPEARANCE)
    assert fresh(signed_in).profile.show_country == "never", "left out, it stays as it was"


def test_the_country_name_follows_the_readers_language(signed_in):
    """Allemagne in French, Germany in English, from the one place."""
    berlin = company_in(signed_in, "Berlin GmbH", "Berlin", "DE")
    company_in(signed_in, "Acme", "Paris", "FR")
    with translation.override("fr-FR"):
        assert place_display.company_place(berlin, fresh(signed_in)) == "Berlin, Allemagne"
    with translation.override("en-GB"):
        assert place_display.company_place(berlin, fresh(signed_in)) == "Berlin, Germany"


def test_a_typed_place_loses_only_the_country_it_need_not_be_told(signed_in):
    """Berlin, Germany stays whole beside French companies; Remote is left alone."""
    company_in(signed_in, "Acme", "Paris", "FR")
    person = fresh(signed_in)
    assert place_display.typed_place("Lyon, France", person) == "Lyon"
    assert place_display.typed_place("Berlin, Germany", person) == "Berlin, Germany"
    assert place_display.typed_place("Remote", person) == "Remote"


def test_the_command_bar_and_the_map_use_the_same_rule(client, signed_in):
    """Search hits and map pins say Paris alone while there is one country."""
    company_in(signed_in, "Acme", "Paris", "FR")
    from postulo.core.search import search_companies
    from postulo.jobs.views import pinned_addresses

    hits = search_companies(fresh(signed_in), "Acme", 5)
    assert "France" not in str(hits) and "Paris" in str(hits)
    pins = pinned_addresses(fresh(signed_in))
    assert pins and all("France" not in pin["place"] for pin in pins)


def test_data_documents_and_exports_keep_the_country(signed_in):
    """Display only: the stored line, a CV's location and the archive carry the country."""
    paris = company_in(signed_in, "Acme", "Paris", "FR")
    assert paris.location == "Paris, France"
    address = paris.postal_addresses.get()
    assert address.one_line() == "Paris, France"
    PostalAddress.objects.create(
        owner=signed_in, holder=signed_in.profile, municipality="Lyon", country="FR"
    )
    assert postal.location_line(signed_in.profile) == "Lyon, France"
    document = export.build_document(signed_in)
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 85
    assert document["account"]["profile"]["show_country"] == "auto"
    assert any(
        entry.get("country") == "FR"
        for company in document["companies"]
        for entry in company.get("postal_addresses", [])
    ), document["companies"]


def test_the_choice_travels_in_the_archive_and_a_bad_value_is_refused(user, other_user):
    """Archive format 85 (#1249): restored as it was; an older file or a wrong value leaves auto."""
    import io
    import json
    import zipfile

    from postulo.core import importer

    user.profile.show_country = "never"
    user.profile.save()
    document = json.loads(zipfile.ZipFile(export.write_archive(user)).read(export.MANIFEST_NAME))

    def load(document):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(export.MANIFEST_NAME, json.dumps(document))
        buffer.seek(0)
        importer.load(other_user, zipfile.ZipFile(buffer))
        other_user.profile.refresh_from_db()
        return other_user.profile

    assert document["account"]["profile"]["show_country"] == "never"
    assert load(document).show_country == "never"
    document["account"]["profile"]["show_country"] = "sometimes"
    other_user.profile.show_country = "auto"
    other_user.profile.save()
    assert load(document).show_country == "auto"
    del document["account"]["profile"]["show_country"]
    assert load(document).show_country == "auto"
