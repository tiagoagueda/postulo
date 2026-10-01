"""Several postal addresses per account, and the one place they deliberately differ (#92).

> file another issue with the same criteria but with postal addresses, multiple valid ones,
> one primary

The companion to telephone numbers, and the interesting part is where it does *not* copy
them: **an address is not unique across the instance.** A mobile number belongs to one
person; a home does not. Spouses share one, flatmates share one, an adult child at home
shares one, and two siblings on a family instance share one — and a family instance is
exactly the kind of small self-hosted deployment this project is built for.

A uniqueness constraint would refuse the second member of a household their own address
*and*, in refusing it, disclose that somebody else on this server lives there.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from postulo.core import postal
from postulo.core.models import PostalAddress

pytestmark = pytest.mark.django_db


def an_address(user, holder=None, **parts) -> PostalAddress:
    fields = {
        "street": "Rua do Exemplo 1",
        "postcode": "1000-001",
        "municipality": "Lisboa",
        "country": "PT",
    }
    fields.update(parts)
    return PostalAddress.objects.create(owner=user, holder=holder or user.profile, **fields)


# ------------------------------------------------- the difference from a phone number


def test_two_accounts_may_hold_the_same_address(user, other_user):
    """A household is not a mistake, and this is the whole reason #92 is its own issue."""
    an_address(user)

    theirs = an_address(other_user)

    assert theirs.pk, "the second person at one address was refused"


def test_one_account_may_not_list_the_same_address_twice(user):
    """Unique per owner: listing your own home twice is the mistake worth catching."""
    an_address(user)

    with pytest.raises(IntegrityError), transaction.atomic():
        an_address(user)


def test_a_second_address_that_differs_in_case_or_spacing_is_the_same_address(user):
    an_address(user)

    with pytest.raises(IntegrityError), transaction.atomic():
        an_address(user, street="rua  do exemplo   1")


def test_but_one_extra_character_is_a_different_address(user):
    """Guessing past case and spacing needs the reference database this does not have."""
    an_address(user)

    other = an_address(user, street="Rua do Exemplo 1A")

    assert other.pk


# ------------------------------------------------------------- exactly one primary


def test_only_one_address_can_be_primary_for_a_holder(user):
    an_address(user, is_primary=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        an_address(user, street="Rua Outra 2", is_primary=True)


def test_making_one_primary_moves_the_flag(user):
    first = an_address(user, is_primary=True)
    second = an_address(user, street="Rua Outra 2")

    postal.make_primary(second)

    first.refresh_from_db()
    second.refresh_from_db()
    assert second.is_primary and not first.is_primary


def test_deleting_the_primary_leaves_something_primary_behind(user):
    """A holder with addresses and no primary is a holder whose letter has no address."""
    first = an_address(user, is_primary=True)
    an_address(user, street="Rua Outra 2")

    first.delete()
    postal.ensure_one_primary(user.profile)

    assert postal.for_holder(user.profile).filter(is_primary=True).count() == 1


# ---------------------------------------------------------- what it is and is not for


def test_the_cv_line_is_a_town_and_a_country_and_never_a_street(user):
    """Guidance across most of Europe is that a CV carries a city and a country. A precise
    address invites a reader to draw conclusions about somebody from where they live, and
    putting one on a document sent to strangers is not a default anybody chose.
    """
    an_address(user, is_primary=True)

    line = postal.location_line(user.profile)

    assert line == "Lisboa, Portugal"
    assert "Rua" not in line and "1000-001" not in line


def test_nothing_here_is_verified_and_nothing_is_a_way_back_in():
    """Postulo is not going to post anything, so it has no way to learn whether an address
    exists and no use for the answer. Valid means well-formed enough to be used.
    """
    fields = {field.name for field in PostalAddress._meta.get_fields()}

    assert "verified_at" not in fields
    assert "is_recovery" not in fields


def test_an_address_typed_oddly_is_saved_exactly_as_typed(user):
    address = an_address(user, street="rua  do   exemplo 1, 3.º Esq.")

    address.refresh_from_db()

    assert address.street == "rua  do   exemplo 1, 3.º Esq.", "only the comparable form folds"


# --------------------------------------------------------------- taking it with you


def test_an_address_is_in_the_archive(user):
    """Taking your data out has to include where you live, or it is not your data out."""
    from postulo.core import export

    an_address(user, is_primary=True)

    document = export.build_document(user)
    rows = document["account"]["profile"]["postal_addresses"]

    assert [row["street"] for row in rows] == ["Rua do Exemplo 1"]
    assert document["postulo"]["format"] >= 9, "the shape changed; an importer must notice"


def test_it_comes_back_from_an_archive(user, other_user):
    """And two people can import the same address, because a household is not a collision."""
    from postulo.core.importer import _restore_postal_addresses

    rows = [
        {
            "street": "Rua do Exemplo 1",
            "postcode": "1000-001",
            "municipality": "Lisboa",
            "country": "PT",
            "is_primary": True,
        }
    ]

    _restore_postal_addresses(user.profile, user, rows)
    _restore_postal_addresses(other_user.profile, other_user, rows)

    assert postal.for_holder(user.profile).count() == 1
    assert postal.for_holder(other_user.profile).count() == 1


def test_an_archive_listing_one_address_twice_lands_once(user):
    from postulo.core.importer import _restore_postal_addresses

    row = {"street": "Rua do Exemplo 1", "municipality": "Lisboa", "country": "PT"}

    _restore_postal_addresses(user.profile, user, [row, dict(row)])

    assert postal.for_holder(user.profile).count() == 1


# ------------------------------------------------------- and the Europass import


def test_europass_stops_throwing_the_street_away(user):
    """The concrete cost of not having somewhere to put it, and it was happening.

    Somebody exported a Europass CV, imported it here, and the two lines that make an
    address an address were silently gone.
    """
    from pathlib import Path

    from postulo.plugins.europass import reader
    from postulo.resume import importing

    fixture = Path(__file__).parent / "data" / "europass.xml"
    record = reader.read(fixture.read_bytes())

    importing.apply(user, record)

    address = postal.primary_for(user.profile)
    assert address is not None
    assert address.street == "Rua do Exemplo 1"
    assert address.postcode == "1000-001"
    assert address.country == "PT"


def test_an_import_never_argues_with_an_address_somebody_typed(user):
    from pathlib import Path

    from postulo.plugins.europass import reader
    from postulo.resume import importing

    mine = an_address(user, street="Where I actually live 9", is_primary=True)
    record = reader.read((Path(__file__).parent / "data" / "europass.xml").read_bytes())

    importing.apply(user, record)

    assert postal.for_holder(user.profile).count() == 1
    mine.refresh_from_db()
    assert mine.street == "Where I actually live 9"


# ------------------------------------------------------------------ on the page


def test_the_profile_page_offers_the_addresses(client, user):
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-postal-addresses" in html
    assert "addresses-TOTAL_FORMS" in html


def test_the_page_says_an_address_is_not_unique_here(client, user):
    """Because the telephone numbers beside it are, and somebody will assume both."""
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "people share a home" in html


# ----------------------------------------------- an address typed into the empty row (#454)


def address_rows(*streets, prefix="addresses"):
    """The POST the block of address rows sends when every row is a new one."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(streets)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, street in enumerate(streets):
        data.update(
            {
                f"{prefix}-{index}-kind": "",
                f"{prefix}-{index}-label": "",
                f"{prefix}-{index}-street": street,
                f"{prefix}-{index}-postcode": "1000-001",
                f"{prefix}-{index}-municipality": "Lisboa",
                f"{prefix}-{index}-region": "",
                f"{prefix}-{index}-country": "PT",
            }
        )
    return data


def test_your_details_saves_an_address_typed_into_the_empty_row(client, user):
    """Nobody could record their own address: the row was saved with no owner, the insert
    failed on `owner_id`, and the page answered 500 with everything else typed on it lost."""
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Alex",
            "last_name": "Morgan",
            "headline": "Saved with the address",
            "location": "",
            **address_rows("Rua do Exemplo 1"),
        },
    )

    assert response.status_code == 302
    address = PostalAddress.objects.get()
    assert address.owner == user and address.holder == user.profile
    assert (address.street, address.municipality, address.country) == (
        "Rua do Exemplo 1",
        "Lisboa",
        "PT",
    )
    assert address.is_primary, "the first address is the one to use"
    user.profile.refresh_from_db()
    assert user.profile.headline == "Saved with the address"


def test_the_rows_give_a_new_address_its_owner_themselves(user):
    """Bound to a holder and saved, with nobody having set an owner: the rows set it, from
    the account behind the holder, so no caller can forget."""
    formset = postal.formset_for(user.profile, data=address_rows("Rua A 1", "Rua B 2"))

    assert formset.is_valid(), formset.errors
    formset.save()

    assert [row.owner for row in postal.for_holder(user.profile)] == [user, user]


def test_a_contacts_address_belongs_to_the_account_that_keeps_the_contact(user):
    """A contact can hold addresses -- an archive brings them -- and the owner is then the
    contact's, which is the rule the telephone and link rows follow."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    someone = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    formset = postal.formset_for(someone, data=address_rows("Rua do Contacto 3"))

    assert formset.is_valid(), formset.errors
    formset.save()

    assert postal.for_holder(someone).get().owner == user


# ------------------------------------- what the account already lists, and the order (#458)


def kept_rows(*rows, prefix="addresses"):
    """The POST for rows the page was drawn with, each a dict of what its boxes hold.

    ``id`` is the saved row the form stands for and ``DELETE`` its *Remove* box; a dict with
    no ``id`` is a row being added, and those come last, as the page draws them.
    """
    saved = [row for row in rows if row.get("id")]
    assert rows[: len(saved)] == tuple(saved), "the saved rows first, as on the page"
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(len(saved)),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        parts = {
            "kind": "",
            "label": "",
            "street": "",
            "postcode": "1000-001",
            "municipality": "Lisboa",
            "region": "",
            "country": "PT",
        }
        parts.update(row)
        for name, value in parts.items():
            data[f"{prefix}-{index}-{name}"] = str(value)
    return data


def your_details(**rows) -> dict:
    return {"first_name": "Alex", "last_name": "Morgan", "location": "", **rows}


def test_moving_into_an_address_being_removed_in_the_same_save(client, user):
    """Somebody moves to their second address: the first row is changed into it and the
    second removed, in one save. The first row's UPDATE ran while the second still existed,
    and the constraint answered with a 500 and took the rest of the page with it."""
    home = an_address(user, street="Rua A 1", is_primary=True)
    second = an_address(user, street="Rua B 2")
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        your_details(
            **kept_rows(
                {"id": home.pk, "street": "Rua B 2"},
                {"id": second.pk, "street": "Rua B 2", "DELETE": "on"},
            )
        ),
    )

    assert response.status_code == 302
    assert [row.street for row in postal.for_holder(user.profile)] == ["Rua B 2"]
    assert postal.for_holder(user.profile).get().pk == home.pk, "the row kept is the one changed"


def test_two_rows_may_change_places_in_one_save(client, user):
    """The same collision by another road: each row changed into what the other held."""
    first = an_address(user, street="Rua A 1", is_primary=True)
    second = an_address(user, street="Rua B 2")
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        your_details(
            **kept_rows(
                {"id": first.pk, "street": "Rua B 2"},
                {"id": second.pk, "street": "Rua A 1"},
            )
        ),
    )

    assert response.status_code == 302
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.street, second.street) == ("Rua B 2", "Rua A 1")
    assert first.comparable and second.comparable, "each took its comparable form back"


def test_a_row_changed_into_what_another_is_changing_out_of(client, user):
    first = an_address(user, street="Rua A 1", is_primary=True)
    second = an_address(user, street="Rua B 2")
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        your_details(
            **kept_rows(
                {"id": first.pk, "street": "Rua B 2"},
                {"id": second.pk, "street": "Rua C 3"},
            )
        ),
    )

    assert response.status_code == 302
    streets = sorted(row.street for row in postal.for_holder(user.profile))
    assert streets == ["Rua B 2", "Rua C 3"]


def test_an_address_a_contact_already_holds_is_said_and_not_a_crash(client, user):
    """The constraint is per owner and not per holder, and a contact can hold addresses --
    an archive brings them. The form compared the page's rows only with one another, so
    this was left for the database to refuse."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    someone = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    an_address(user, holder=someone)
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"), your_details(**address_rows("Rua do Exemplo 1"))
    )

    assert response.status_code == 200
    assert "This address is already listed." in response.content.decode()
    assert not postal.for_holder(user.profile).exists()


def test_somebody_elses_contact_holding_it_is_nobodys_business(client, user, other_user):
    """Per owner still: another account's contact at the same address refuses nothing and
    discloses nothing (#92)."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=other_user, name="Aperture")
    someone = Contact.objects.create(owner=other_user, company=company, name="Cave Johnson")
    an_address(other_user, holder=someone)
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"), your_details(**address_rows("Rua do Exemplo 1"))
    )

    assert response.status_code == 302
    assert postal.for_holder(user.profile).count() == 1


def test_a_row_added_from_another_tab_is_said_too(user):
    """A copy of the page drawn before a row was added elsewhere does not carry that row,
    so nothing on it stands for it; typing the same address is still a repeat."""
    kept = an_address(user, street="Rua A 1", is_primary=True)
    an_address(user, street="Rua B 2")  # added since this copy of the page was drawn

    formset = postal.formset_for(
        user.profile,
        data=kept_rows({"id": kept.pk, "street": "Rua A 1"}, {"street": "Rua B 2"}),
    )

    assert not formset.is_valid()
    assert "This address is already listed." in str(formset.forms[1].non_field_errors())


def test_removing_a_row_and_typing_it_again_in_one_save(user):
    """The rows being removed are left out of the comparison, and the removal is written
    first, so the same address typed into the empty row is not a repeat."""
    kept = an_address(user, street="Rua A 1", is_primary=True)
    was = kept.pk

    formset = postal.formset_for(
        user.profile,
        data=kept_rows({"id": was, "street": "Rua A 1", "DELETE": "on"}, {"street": "Rua A 1"}),
    )

    assert formset.is_valid(), formset.errors
    formset.save()
    assert [row.street for row in postal.for_holder(user.profile)] == ["Rua A 1"]
    assert postal.for_holder(user.profile).get().pk != was
    assert len(formset.deleted_objects) == 1, "and the save still says what went"
