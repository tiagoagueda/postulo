"""Telephone numbers: the country in front, and something that can actually be dialled.

A recruiter's number written down as `06 12 34 56 78` cannot be dialled from anywhere else,
and the person writing it down is not thinking about that at the time. Six months later it
is unreachable and nothing in the record says which country it belonged to.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core import phones

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ the table


def test_every_country_appears_once_and_the_list_is_ordered():
    codes = [code for code, _dialling, _name in phones.COUNTRIES]
    assert len(codes) == len(set(codes)), "a duplicate would make one of them unreachable"
    assert codes == sorted(codes), "scanned by eye, so it is in order"
    assert len(codes) > 200


def test_every_dialling_code_is_digits():
    for code, dialling, name in phones.COUNTRIES:
        assert dialling.isdigit(), f"{code} ({name}) has {dialling!r}"


def test_a_flag_is_built_from_the_country_code():
    """Unlike a language, a country is a country: nothing has to be decided by hand."""
    assert phones.flag("PT") == "\U0001f1f5\U0001f1f9"
    assert phones.flag("gb") == "\U0001f1ec\U0001f1e7"
    assert phones.flag("") == ""
    assert phones.flag("nonsense") == ""


# ----------------------------------------------------------- combining the two


def test_a_national_number_gains_its_country(_language=None):
    """The case the whole feature exists for."""
    assert phones.combine("06 12 34 56 78", "FR") == "+33612345678"
    assert phones.combine("912345678", "PT") == "+351912345678"


def test_a_number_that_already_says_its_country_is_left_to_say_it():
    """Somebody pasting an international number must not have it mangled by a dropdown."""
    assert phones.combine("+351 912 345 678", "FR") == "+351912345678"
    assert phones.combine("00351912345678", "FR") == "+351912345678"


def test_the_trunk_prefix_goes_and_nothing_else_does():
    assert phones.combine("020 7946 0958", "GB") == "+442079460958"
    assert phones.combine("2079460958", "GB") == "+442079460958", "no zero, nothing removed"


def test_something_unparseable_is_kept_exactly_as_it_was_typed():
    """Refusing to save a number nobody can parse would be the worst outcome available."""
    assert phones.combine("ask reception", "PT") == "ask reception"
    assert phones.combine("06 12 34 56 78", "") == "06 12 34 56 78", "no country chosen"
    assert phones.combine("", "PT") == ""


# ------------------------------------------------------------- reading it back


def test_the_country_is_read_back_from_the_number():
    """Which is why no second column holds it: one fact, one place to be wrong."""
    assert phones.country_of("+351912345678").code == "PT"
    assert phones.country_of("+33612345678").code == "FR"
    assert phones.country_of("06 12 34 56 78") is None


def test_a_longer_dialling_code_wins_over_a_shorter_one():
    """+351 is Portugal, not +35 followed by something."""
    assert phones.country_of("+351912345678").code == "PT"
    assert phones.country_of("+35799123456").code == "CY"


def test_a_number_is_shown_grouped_and_dialled_as_digits():
    assert phones.readable("+33612345678") == "+33 612 345 678"
    assert phones.as_dialled("+33 612 345 678") == "+33612345678"
    assert phones.readable("ask reception") == "ask reception", "left alone"


# -------------------------------------------------------------------- the form


def test_the_contact_form_asks_which_country(client, user):
    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    assert 'name="phone_0"' in html, "the country"
    assert 'name="phone_1"' in html, "the number"
    assert "Country the number is in" in html, "and the chooser has a name of its own"


def test_saving_a_national_number_stores_it_so_it_can_be_dialled(client, user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    client.force_login(user)

    client.post(
        reverse("jobs:contact_create"),
        {
            "name": "Cave Johnson",
            "role": "",
            "company": company.pk,
            "email": "",
            "phone_0": "FR",
            "phone_1": "06 12 34 56 78",
            "linkedin_url": "",
            "notes": "",
        },
    )

    contact = Contact.objects.get(owner=user, name="Cave Johnson")
    assert contact.phone == "+33612345678"


def test_the_form_shows_a_stored_number_split_back_into_its_parts(client, user):
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    contact = Contact.objects.create(
        owner=user, company=company, name="Cave Johnson", phone="+33612345678"
    )
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[contact.pk])).content.decode()

    assert '<option value="FR" selected>' in html
    assert 'value="612345678"' in html


def test_the_country_starts_at_the_one_the_person_reads_postulo_in(client, user):
    user.profile.language = "pt-pt"
    user.profile.save(update_fields=["language"])
    client.force_login(user)

    html = client.get(reverse("jobs:contact_create")).content.decode()
    assert '<option value="PT" selected>' in html


# ----------------------------------------------------------------- displaying


def test_a_number_is_shown_as_something_a_phone_can_ring(client, user):
    """Half of these calls happen on a phone, where text has to be copied out by hand."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    Contact.objects.create(owner=user, company=company, name="Cave Johnson", phone="+33612345678")
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[company.pk])).content.decode()

    assert 'href="tel:+33612345678"' in html
    assert "+33 612 345 678" in html, "grouped, so it can be read aloud"


def test_the_visible_label_points_at_the_number(client, user):
    """A MultiWidget has no single id, and Django's default renders `for=""`."""
    import re

    client.force_login(user)
    html = client.get(reverse("jobs:contact_create")).content.decode()

    labels = re.findall(r'<label[^>]*for="([^"]*)"[^>]*>\s*Phone', html)
    assert labels, "the field has no label at all"
    assert labels[0] == "id_phone_1", "pointed at nothing, which is worse than no label"
