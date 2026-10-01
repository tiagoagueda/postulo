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

import re
from html.parser import HTMLParser

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


# ----------------------------------- an address answers to its country before it is kept (#306)
#
# What each country refuses is `tests/test_postal_rules.py`, country by country. This is the
# page: where the refusal is said, what is left alone, and what never goes through the page.


class AddressRows(HTMLParser):
    """The address rows of a page as they are read: each row's controls in source order,
    and for each part of the address what is written inside its own field."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self._row = None
        self._part = None
        self._depth = 0
        self._items = 0
        self._select = ""
        self._option = False

    def handle_starttag(self, tag, attrs):
        held = dict(attrs)
        if tag == "li" and "data-address-row" in held:
            self._row = {"controls": [], "parts": {}, "invalid": set(), "marks": [], "chosen": {}}
            self.rows.append(self._row)
            return
        if self._row is None:
            return
        if tag == "li":
            self._items += 1  # a sentence of the mark, inside the row
        if tag == "select":
            self._select = held.get("name", "").rsplit("-", 1)[-1]
        if tag == "option":
            self._option = True
            if "selected" in held:
                self._row["chosen"][self._select] = held.get("value", "")
        if tag == "div":
            if self._part is not None:
                self._depth += 1
            elif "data-part" in held:
                self._part, self._depth = held["data-part"], 0
                self._row["parts"][self._part] = ""
                if held.get("data-invalid") == "true":
                    self._row["invalid"].add(self._part)
            for mark in ("data-if-other", "data-has-name", "data-kept-as-it-was"):
                if mark in held:
                    self._row["marks"].append(mark)
        if tag in ("input", "select", "textarea", "button"):
            if held.get("type") == "hidden":
                return
            if "data-remove-trigger" in held:
                self._row["controls"].append("remove")
            elif held.get("name"):
                self._row["controls"].append(held["name"].rsplit("-", 1)[-1])

    def handle_endtag(self, tag):
        if tag == "option":
            self._option = False
        elif tag == "li" and self._items:
            self._items -= 1
        elif tag == "li":
            self._row = None
        elif tag == "div" and self._part is not None:
            if self._depth == 0:
                self._part = None
            else:
                self._depth -= 1

    def handle_data(self, data):
        if self._row is not None and self._part is not None and not self._option:
            if data.strip():
                self._row["parts"][self._part] += " ".join(data.split()) + " "


def rows_of(html: str) -> list[dict]:
    page = AddressRows()
    page.feed(html)
    return page.rows


def switch_off(person) -> None:
    """*Address rules by country* off for one person, as an administrator switches it."""
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.postal_rules import POSTAL_RULES

    PluginPolicy.objects.create(
        plugin=POSTAL_RULES, person=person, state=PluginPolicy.State.FORCED_OFF
    )


def save_details(client, user, *rows):
    client.force_login(user)
    return client.post(reverse("accounts:profile"), your_details(**kept_rows(*rows)))


IN_LISBON = {"street": "Rua do Exemplo 1", "postcode": "1000-001", "municipality": "Lisboa"}


def test_a_postcode_its_country_never_uses_is_refused_beside_the_postcode(client, user):
    """Which part is wrong and what its country expects, next to that part and no other."""
    response = save_details(client, user, {**IN_LISBON, "postcode": "1000"})

    assert response.status_code == 200
    assert list(response.context["addresses"].forms[0].errors) == ["postcode"]
    assert not PostalAddress.objects.exists()
    [row] = rows_of(response.content.decode())
    sentence = "A postcode in this country is written like 1000-001."
    assert sentence in row["parts"]["postcode"]
    assert row["invalid"] == {"postcode"}, "the part's own field is the one marked"
    for part in ("street", "municipality", "region", "country"):
        assert sentence not in row["parts"][part], part


def test_the_error_carries_the_id_its_box_already_names(client, user):
    """Django's widget says `aria-describedby="…_error"` on a box with errors, and the
    sentence beside the part is the element with that id, so it is read with the box."""
    html = save_details(client, user, {**IN_LISBON, "postcode": "1000"}).content.decode()

    box = re.search(r'<input[^>]*name="addresses-0-postcode"[^>]*>', html).group(0)
    assert 'aria-invalid="true"' in box
    assert 'aria-describedby="id_addresses-0-postcode_error"' in box
    assert html.count('id="id_addresses-0-postcode_error"') == 1


def test_a_part_the_country_requires_left_empty_is_refused_beside_that_part(client, user):
    response = save_details(client, user, {**IN_LISBON, "municipality": ""})

    assert response.status_code == 200
    [row] = rows_of(response.content.decode())
    assert row["invalid"] == {"municipality"}
    assert "An address in this country needs a town or city." in row["parts"]["municipality"]


def test_the_part_is_named_the_way_its_country_names_it(client, user):
    """A United States address without its state and with half a ZIP code: the sentences
    say *state* and *ZIP code*, beside the boxes labelled *State* and *ZIP code*."""
    response = save_details(
        client,
        user,
        {
            "street": "1600 Pennsylvania Avenue NW",
            "postcode": "2050",
            "municipality": "Washington",
            "country": "US",
        },
    )

    [row] = rows_of(response.content.decode())
    assert row["invalid"] == {"postcode", "region"}
    assert row["parts"]["region"].startswith("State ")
    assert "An address in this country needs a state." in row["parts"]["region"]
    assert row["parts"]["postcode"].startswith("ZIP code ")
    assert "A ZIP code is written like 20500." in row["parts"]["postcode"]


def test_the_refusal_is_in_the_readers_language(client, user):
    user.profile.language = "pt-PT"
    user.profile.save()

    response = save_details(client, user, {**IN_LISBON, "postcode": "1000"})

    [row] = rows_of(response.content.decode())
    said = row["parts"]["postcode"]
    assert "1000-001" in said
    assert "is written like" not in said, "the plugin's own catalogue says it in Portuguese"


@pytest.mark.parametrize(
    ("typed", "country", "kept"),
    [
        ("1000100", "PT", "1000-100"),
        ("1000 100", "PT", "1000-100"),
        ("1000-100", "PT", "1000-100"),
        ("1234ab", "NL", "1234 AB"),
        ("sw1a1aa", "GB", "SW1A 1AA"),
    ],
)
def test_a_postcode_that_is_plainly_the_countrys_is_kept_the_countrys_way(
    client, user, typed, country, kept
):
    response = save_details(client, user, {**IN_LISBON, "postcode": typed, "country": country})

    assert response.status_code == 302
    assert PostalAddress.objects.get().postcode == kept


def test_two_typings_of_one_postcode_are_one_address(client, user):
    """Put right before the rows are compared, so the same address typed twice with its
    postcode two ways is a repeat and not two rows."""
    response = save_details(
        client, user, {**IN_LISBON, "postcode": "1000001"}, {**IN_LISBON, "postcode": "1000 001"}
    )

    assert response.status_code == 200
    assert "This address is already listed." in response.content.decode()


def test_a_country_the_plugin_does_not_cover_stays_free_form(client, user):
    """Nothing required, no form for the postcode to be in, and kept exactly as typed."""
    response = save_details(
        client,
        user,
        {"street": "", "postcode": "any form at all", "municipality": "", "country": "NZ"},
    )

    assert response.status_code == 302
    kept = PostalAddress.objects.get()
    assert (kept.postcode, kept.country, kept.street) == ("any form at all", "NZ", "")


def test_an_address_with_no_country_stays_free_form(client, user):
    response = save_details(client, user, {"street": "Somewhere", "postcode": "?", "country": ""})

    assert response.status_code == 302
    assert PostalAddress.objects.get().postcode == "?"


# ---------------------------------------------------- an address already kept is left alone


def kept_before(user, **parts) -> PostalAddress:
    """An address its country would refuse, kept before anything refused: straight into the
    table, as a row from before #306 is and as an import writes one."""
    return an_address(user, postcode="1000", municipality="", is_primary=True, **parts)


def as_it_is(row: PostalAddress, **changed) -> dict:
    """The row as the page posts it back, with what somebody changed."""
    fields = ("kind", "label", "street", "postcode", "municipality", "region", "country")
    return {"id": row.pk, **{name: getattr(row, name) for name in fields}, **changed}


def test_a_kept_address_that_would_be_refused_is_marked_and_says_why(client, user):
    kept_before(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    [row, _empty] = rows_of(html)
    assert "data-kept-as-it-was" in row["marks"]
    assert "Kept as it was" in html
    assert "checked the next time you change it" in html
    assert "An address in this country needs a town or city." in html
    assert "A postcode in this country is written like 1000-001." in html
    assert row["invalid"] == set(), "marked, and nothing on it drawn as an error"


def test_a_kept_address_that_fits_is_not_marked(client, user):
    an_address(user, is_primary=True)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-kept-as-it-was" not in html and "Kept as it was" not in html


def test_saving_the_page_without_touching_it_does_not_refuse_the_page(client, user):
    """Everything else on the page saves, and the row is exactly as it was."""
    row = kept_before(user)
    client.force_login(user)

    response = client.post(
        reverse("accounts:profile"),
        {**your_details(**kept_rows(as_it_is(row))), "headline": "Saved all the same"},
    )

    assert response.status_code == 302
    row.refresh_from_db()
    assert (row.postcode, row.municipality) == ("1000", "")
    user.profile.refresh_from_db()
    assert user.profile.headline == "Saved all the same"


def test_a_street_of_two_lines_posted_by_a_browser_is_not_a_change(client, user):
    """A browser posts a text area's line break as two characters and the table holds one,
    which Django's own comparison calls a change. A row nobody touched has not changed."""
    row = kept_before(user, street="Rua do Exemplo 1\nSegundo andar")

    response = save_details(client, user, as_it_is(row, street="Rua do Exemplo 1\r\nSegundo andar"))

    assert response.status_code == 302


def test_its_kind_and_its_star_may_change_without_it_being_asked_again(client, user):
    """The kind and the name are about the address and no part of it."""
    row = kept_before(user)

    response = save_details(client, user, as_it_is(row, kind="work"))

    assert response.status_code == 302
    row.refresh_from_db()
    assert (row.kind, row.postcode) == ("work", "1000")


def test_it_is_checked_the_next_time_its_address_is_changed(client, user):
    """And then it answers like any other: the errors beside its parts, and no mark."""
    row = kept_before(user)

    response = save_details(client, user, as_it_is(row, street="Rua Nova 2"))

    assert response.status_code == 200
    [drawn] = rows_of(response.content.decode())
    assert drawn["invalid"] == {"postcode", "municipality"}
    assert "data-kept-as-it-was" not in drawn["marks"]
    row.refresh_from_db()
    assert row.street == "Rua do Exemplo 1", "nothing of a refused row is written"


def test_and_once_it_fits_it_is_kept_and_the_mark_goes(client, user):
    row = kept_before(user)

    response = save_details(client, user, as_it_is(row, postcode="1000100", municipality="Lisboa"))

    assert response.status_code == 302
    row.refresh_from_db()
    assert (row.postcode, row.municipality) == ("1000-100", "Lisboa")
    assert "data-kept-as-it-was" not in client.get(reverse("accounts:profile")).content.decode()


def test_a_marked_row_beside_a_refused_one_keeps_its_mark(client, user):
    """The page comes back for another row's sake; the row left alone is still only marked."""
    row = kept_before(user)

    response = save_details(
        client, user, as_it_is(row), {**IN_LISBON, "street": "Rua B 2", "postcode": "x"}
    )

    assert response.status_code == 200
    [kept, new] = rows_of(response.content.decode())
    assert "data-kept-as-it-was" in kept["marks"] and kept["invalid"] == set()
    assert new["invalid"] == {"postcode"}


# ----------------------------------------------------------------- with the plugin switched off


def test_switched_off_nothing_is_refused(client, user):
    switch_off(user)

    response = save_details(client, user, {**IN_LISBON, "postcode": "1000", "municipality": ""})

    assert response.status_code == 302
    kept = PostalAddress.objects.get()
    assert (kept.postcode, kept.municipality) == ("1000", "")


def test_switched_off_a_postcode_is_kept_as_it_was_typed(client, user):
    switch_off(user)

    save_details(client, user, {**IN_LISBON, "postcode": "1000100"})

    assert PostalAddress.objects.get().postcode == "1000100"


def test_switched_off_nothing_is_marked_and_nothing_is_said_about_checking(client, user):
    kept_before(user)
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-kept-as-it-was" not in html and "Kept as it was" not in html
    assert "data-rules-help" not in html


def test_switched_on_the_page_says_what_is_refused_and_how_forgiving_it_is(client, user):
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-rules-help" in html
    assert "1000100 is kept as 1000-100" in html
    assert "Nothing checks that an address exists" in html


def test_switched_off_for_somebody_else_changes_nothing_for_this_person(client, user, other_user):
    switch_off(other_user)

    response = save_details(client, user, {**IN_LISBON, "postcode": "1000"})

    assert response.status_code == 200


# -------------------------------------------------------- what never goes through the page


REFUSED_IF_TYPED = {
    "street": "Rua do Exemplo 1",
    "postcode": "1000",
    "municipality": "",
    "country": "PT",
    "is_primary": True,
}


def test_an_archive_is_never_refused_for_an_address_and_brings_it_marked(client, user):
    from postulo.core.importer import _restore_postal_addresses

    _restore_postal_addresses(user.profile, user, [dict(REFUSED_IF_TYPED)])

    kept = postal.for_holder(user.profile).get()
    assert (kept.postcode, kept.municipality) == ("1000", ""), "as the archive held it"
    client.force_login(user)
    assert "data-kept-as-it-was" in client.get(reverse("accounts:profile")).content.decode()


def test_an_archive_keeps_a_postcode_exactly_as_it_was_written(user):
    """Restoring is not typing: nothing is put right on the way in, so an archive of an
    account comes back as the account was."""
    from postulo.core.importer import _restore_postal_addresses

    _restore_postal_addresses(user.profile, user, [{**REFUSED_IF_TYPED, "postcode": "1000100"}])

    assert postal.for_holder(user.profile).get().postcode == "1000100"


def a_candidate_file(*addresses) -> bytes:
    import json

    from postulo.core import export

    return json.dumps(
        {
            "postulo": {
                "candidate_format": export.CANDIDATE_FORMAT,
                "version": "0.5.0",
                "exported_at": "2026-10-01T10:00:00+00:00",
            },
            "account": {"profile": {"postal_addresses": list(addresses)}},
        }
    ).encode()


def test_a_candidate_file_is_never_refused_for_an_address_and_says_it_will_be_marked(client, user):
    from postulo.resume import candidate

    held = candidate.read(a_candidate_file(dict(REFUSED_IF_TYPED)))
    plan = candidate.plan(user, held)

    [row] = [row for section in plan.sections for row in section.rows]
    assert row.outcome == candidate.ADD, "to be added, not refused"
    assert any("marked there until you change it" in note for note in row.notes)

    candidate.apply(user, held)

    kept = postal.for_holder(user.profile).get()
    assert (kept.postcode, kept.municipality) == ("1000", "")
    client.force_login(user)
    assert "data-kept-as-it-was" in client.get(reverse("accounts:profile")).content.decode()


def test_a_candidate_file_with_an_address_that_fits_says_nothing_of_marking(user):
    from postulo.resume import candidate

    whole = {**REFUSED_IF_TYPED, "postcode": "1000-001", "municipality": "Lisboa"}
    plan = candidate.plan(user, candidate.read(a_candidate_file(whole)))

    [row] = [row for section in plan.sections for row in section.rows]
    assert row.outcome == candidate.ADD and row.notes == []


def test_a_candidate_file_says_nothing_of_marking_while_the_rules_are_off(user):
    from postulo.resume import candidate

    switch_off(user)
    plan = candidate.plan(user, candidate.read(a_candidate_file(dict(REFUSED_IF_TYPED))))

    [row] = [row for section in plan.sections for row in section.rows]
    assert row.outcome == candidate.ADD and row.notes == []


def test_a_europass_file_is_never_refused_for_an_address_and_brings_it_marked(client, user):
    from postulo.resume import importing

    record = importing.Record(
        person={
            "address": {
                "street": "Rua do Exemplo 1",
                "postcode": "1000",
                "municipality": "",
                "country": "PT",
            }
        },
        source="xml",
    )

    importing.apply(user, record)

    kept = postal.for_holder(user.profile).get()
    assert (kept.postcode, kept.municipality) == ("1000", "")
    client.force_login(user)
    assert "data-kept-as-it-was" in client.get(reverse("accounts:profile")).content.decode()


# ------------------------------------------------------- the order a row is read in (#306)


def test_on_your_details_a_row_says_what_it_is_then_the_address_then_its_controls(client, user):
    """Kind first, as #213 describes for a telephone number; the name beside it, for a kind
    of Other; the address; and the star and the bin, which #303 drew, at the end."""
    an_address(user, is_primary=True)
    client.force_login(user)

    [kept, empty] = rows_of(client.get(reverse("accounts:profile")).content.decode())

    parts = ["street", "postcode", "municipality", "region", "country"]
    assert kept["controls"] == ["kind", "label", *parts, "primary", "remove"]
    assert empty["controls"] == ["kind", "label", *parts, "primary"], "nothing to remove yet"


def test_the_parts_are_in_the_order_their_country_writes_them(client, user):
    """Portugal writes the postcode before the town, the United States after the state,
    Japan first of all, and Hungary the town before the street."""
    an_address(user, is_primary=True)
    an_address(
        user,
        street="1 Main St",
        municipality="Springfield",
        region="VA",
        postcode="22162",
        country="US",
    )
    an_address(user, street="1-1", municipality="Chiyoda", postcode="100-8111", country="JP")
    an_address(user, street="Virág tér 3", municipality="Budapest", postcode="1037", country="HU")
    client.force_login(user)

    rows = rows_of(client.get(reverse("accounts:profile")).content.decode())

    order = {row["chosen"]["country"]: row["controls"][2:7] for row in rows[:4]}
    assert order == {
        "PT": ["street", "postcode", "municipality", "region", "country"],
        "US": ["street", "municipality", "region", "postcode", "country"],
        "JP": ["postcode", "region", "municipality", "street", "country"],
        "HU": ["municipality", "street", "postcode", "region", "country"],
    }


def test_a_row_sent_back_is_drawn_in_the_order_of_the_country_that_was_posted(client, user):
    """The errors are beside the parts as the posted country orders and names them."""
    response = save_details(
        client, user, {"street": "1 Main St", "municipality": "Springfield", "country": "US"}
    )

    [row] = rows_of(response.content.decode())
    assert row["controls"][2:7] == ["street", "municipality", "region", "postcode", "country"]


def test_switched_off_every_country_is_drawn_in_the_plain_order(client, user):
    an_address(user, street="1-1", municipality="Chiyoda", postcode="100-8111", country="JP")
    switch_off(user)
    client.force_login(user)

    [kept, _empty] = rows_of(client.get(reverse("accounts:profile")).content.decode())

    assert kept["controls"][2:7] == ["street", "postcode", "municipality", "region", "country"]


def test_a_page_that_saves_at_its_foot_reads_in_the_same_order_and_keeps_its_boxes(user):
    """The rows as a second page would draw them, without `remove_at_once`: the contact
    form's arrangement for its telephone numbers, where *Primary* is a radio with its word
    and *Remove* a box that goes when the page is saved. The same order, kind first and the
    two controls last. No page draws address rows this way today: the contact form has
    none."""
    from django.template.loader import render_to_string

    an_address(user, is_primary=True)
    html = render_to_string(
        "partials/postal_addresses.html", {"addresses": postal.formset_for(user.profile)}
    )

    [kept, empty] = rows_of(html)
    parts = ["street", "postcode", "municipality", "region", "country"]
    assert kept["controls"] == ["kind", "label", *parts, "primary", "DELETE"]
    assert empty["controls"] == ["kind", "label", *parts, "primary"]
    assert "data-remove-trigger" not in html and "popovertarget" not in html
    assert ">Primary<" in html and ">Remove<" in html


def test_the_contact_form_draws_no_address_rows(client, user):
    """Written down because #306 speaks of the contact form: it has telephone numbers and
    links, and has never drawn addresses. A contact holds one only where an archive or a
    merge brought it."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    someone = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    client.force_login(user)

    html = client.get(reverse("jobs:contact_update", args=[someone.pk])).content.decode()

    assert "data-postal-addresses" not in html


# ------------------------------------------------------ the name, only for a kind of Other


def test_the_name_is_hidden_by_the_stylesheet_unless_the_kind_is_other(client, user):
    """The kind and the name share a group marked `data-if-other`, and the stylesheet hides
    the name's box unless the group's select has Other chosen -- so it is there the moment
    Other is chosen, with scripts off -- or the row already holds a name."""
    an_address(user, is_primary=True)
    an_address(user, street="Rua B 2", kind="other", label="My sister's")
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    plain, named, empty = rows_of(html)
    assert plain["marks"] == ["data-if-other"] and empty["marks"] == ["data-if-other"]
    assert named["marks"] == ["data-if-other", "data-has-name"], "a name it holds stays drawn"
    groups = re.findall(
        r'<div class="address-line[^"]*"[^>]*data-if-other[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html,
        re.S,
    )
    assert len(groups) == 3
    for group in groups:
        assert group.index("-kind") < group.index("data-name-if-other") < group.index("-label")
        assert "-country" not in group, "the only select the rule can see is the kind's"


def test_the_stylesheet_hides_the_name_unless_other_is_chosen():
    from pathlib import Path

    css = Path(__file__).resolve().parents[1] / "src/postulo/static/css/app.css"
    text = " ".join(css.read_text(encoding="utf-8").split())
    assert (
        '[data-if-other]:not([data-has-name]):not(:has(select option[value="other"]:checked)) '
        "[data-name-if-other]" in text
    )


def test_other_with_no_name_is_refused_beside_the_name(client, user):
    """Sent back with Other chosen, so the stylesheet draws the box, and its error with it."""
    response = save_details(client, user, {**IN_LISBON, "kind": "other", "label": ""})

    assert response.status_code == 200
    html = response.content.decode()
    html = html[html.index("data-postal-addresses") :]
    name = re.search(
        r"<div class=\"field\" data-name-if-other[^>]*>(.*?)</div>\s*</div>", html, re.S
    )
    assert 'data-invalid="true"' in name.group(0)
    assert "Say what this address is." in name.group(1)


# --------------------------- a real address is never refused, nor made into another (#306)
#
# What the review of #306 found the page doing to addresses that are real, and to rows
# nobody wrote in. The table's side of each is in `tests/test_postal_rules.py`.


def blank(**parts) -> dict:
    """A row being added, with nothing in its boxes but what is given."""
    return {"street": "", "postcode": "", "municipality": "", "region": "", **parts}


def test_a_town_and_a_country_are_kept_as_a_place(client, user):
    """*Lisboa* and Portugal: what `location_line` builds a CV's line from, and what the
    page itself says a CV shows. It was refused for want of a postcode."""
    response = save_details(client, user, blank(municipality="Lisboa", country="PT"))

    assert response.status_code == 302
    kept = PostalAddress.objects.get()
    assert (kept.street, kept.postcode, kept.municipality, kept.country) == ("", "", "Lisboa", "PT")
    assert postal.location_line(user.profile) == "Lisboa, Portugal"


@pytest.mark.parametrize(
    "parts",
    [
        {"municipality": "Austin", "region": "TX", "country": "US"},
        {"municipality": "Austin", "country": "US"},
        {"municipality": "Bruxelles", "country": "BE"},
        {"municipality": "Roma", "country": "IT"},
        {"region": "Ontario", "country": "CA"},
        {"municipality": "Chiyoda", "country": "JP"},
    ],
)
def test_nothing_is_required_of_a_place_whatever_its_country_requires_of_an_address(
    client, user, parts
):
    response = save_details(client, user, blank(**parts))

    assert response.status_code == 302
    assert PostalAddress.objects.count() == 1


def test_a_place_is_never_marked_and_is_told_what_a_whole_address_also_carries(client, user):
    """It is not a kept address that fails; it is a shorter thing, allowed. So no amber
    mark, and under it what an address in its country also has, for somebody who meant one."""
    row = an_address(user, street="", postcode="", is_primary=True)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()

    assert "data-kept-as-it-was" not in html and "Kept as it was" not in html
    assert html.count("data-place-note") == 1, "under the place, and not under the empty row"
    note = html[html.index("data-place-note") :]
    note = " ".join(note[: note.index("</div>")].split())
    assert "A place rather than a whole address, and kept as it is." in note
    assert "usually gives a street and number" in note
    assert "An address in this country needs a postcode, written like 1000-001." in note

    # And saving the page leaves it exactly as it is.
    response = save_details(client, user, as_it_is(row))
    assert response.status_code == 302
    row.refresh_from_db()
    assert (row.street, row.postcode, row.municipality) == ("", "", "Lisboa")


def test_a_place_sent_back_is_told_once(client, user):
    """The page comes back for the row's name: what is said under a place is not said again
    as a note about each empty part."""
    response = save_details(
        client, user, blank(municipality="Lisboa", country="PT", kind="other", label="")
    )

    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("data-place-note") == 1
    assert "data-country-note" not in html
    assert list(response.context["addresses"].forms[0].errors) == ["label"]


def test_only_a_place_in_a_country_with_rules_is_told_anything(client, user):
    an_address(user, is_primary=True)
    an_address(user, street="", postcode="", municipality="Auckland", country="NZ")
    client.force_login(user)

    assert "data-place-note" not in client.get(reverse("accounts:profile")).content.decode()


def test_switched_off_a_place_is_told_nothing(client, user):
    an_address(user, street="", postcode="", is_primary=True)
    switch_off(user)
    client.force_login(user)

    assert "data-place-note" not in client.get(reverse("accounts:profile")).content.decode()


@pytest.mark.parametrize(
    "parts",
    [
        {"postcode": "1000", "municipality": "Bruxelles", "country": "BE"},
        {"postcode": "00184", "municipality": "Roma", "region": "RM", "country": "IT"},
        {"postcode": "20500", "municipality": "Washington", "region": "DC", "country": "US"},
        {"postcode": "68151", "municipality": "Mannheim", "country": "DE"},
    ],
)
def test_an_address_left_without_a_street_is_kept_in_every_country(client, user, parts):
    """Belgium, Italy and the United States refused it, and nowhere else did: the German
    guide's own *Citibank Privatkunden AG / 68151 MANNHEIM* was kept, and the same shape in
    the United States was not."""
    response = save_details(client, user, blank(**parts))

    assert response.status_code == 302
    assert PostalAddress.objects.get().street == ""


def test_a_street_left_out_is_remarked_on_in_a_whole_sentence(client, user):
    """What was a refusal in three countries is a note in all of them, and reads as one."""
    response = save_details(
        client,
        user,
        blank(postcode="1000", municipality="Bruxelles", country="BE", kind="other", label=""),
    )

    assert response.status_code == 200
    assert [str(note) for note in response.context["addresses"].forms[0].country_notes] == [
        "An address in this country usually gives a street and number, or else the place "
        "where post is delivered there."
    ]


def test_a_forces_address_is_kept(client, user):
    """Refused twice: for a post town it does not have, and for the form of BFPO 61."""
    response = save_details(
        client,
        user,
        blank(
            street="12345678 LCpl B Jones\nB Company, 1 Loamshire Regt",
            postcode="bfpo 61",
            country="GB",
        ),
    )

    assert response.status_code == 302
    kept = PostalAddress.objects.get()
    assert (kept.postcode, kept.municipality) == ("BFPO 61", "")


@pytest.mark.parametrize(
    ("parts", "kept"),
    [
        (
            {"street": "Stadshusparken", "postcode": "ax 22100", "municipality": "Mariehamn"},
            "AX-22100",
        ),
        (
            {"street": "Mäkelänkatu 25 B 13", "postcode": "00550", "municipality": "Helsinki"},
            "00550",
        ),
    ],
)
def test_an_aland_postcode_is_kept_with_the_ax_it_came_with(client, user, parts, kept):
    response = save_details(client, user, blank(**parts, country="FI"))

    assert response.status_code == 302
    assert PostalAddress.objects.get().postcode == kept


def test_an_overseas_territorys_code_is_kept_under_the_united_kingdom(client, user):
    """Ascension has no entry of its own in the country list, so its people have this one."""
    response = save_details(
        client, user, blank(street="Georgetown", postcode="ascn 1zz", country="GB")
    )

    assert response.status_code == 302
    assert PostalAddress.objects.get().postcode == "ASCN 1ZZ"


def test_the_postal_mark_before_a_japanese_postcode_is_taken_off(client, user):
    response = save_details(
        client,
        user,
        blank(street="1-1", postcode="〒100-8111", municipality="Chiyoda", country="JP"),
    )

    assert response.status_code == 302
    assert PostalAddress.objects.get().postcode == "100-8111"


def test_a_postal_district_typed_as_an_eircode_is_refused_and_the_box_may_be_empty(client, user):
    """*Dublin 4* in the Eircode's box is a real habit. It was saved as ``DUB LIN4``."""
    in_dublin = blank(street="12 Example Road", municipality="Dublin", country="IE")

    response = save_details(client, user, {**in_dublin, "postcode": "Dublin 4"})

    assert response.status_code == 200
    [row] = rows_of(response.content.decode())
    assert row["invalid"] == {"postcode"}
    assert "An Eircode is written like D02 AF30." in row["parts"]["postcode"]
    assert 'value="Dublin 4"' in response.content.decode(), "sent back as it was typed"
    assert not PostalAddress.objects.exists()

    assert save_details(client, user, in_dublin).status_code == 302
    assert PostalAddress.objects.get().postcode == ""


def test_a_kept_postal_district_is_marked_and_never_made_into_an_eircode(client, user):
    """It reached kept data too: a row holding *Dublin 4* -- from before, or from an
    archive -- became ``DUB LIN4`` the first time its street was edited. A kept address is
    never rewritten by being looked at, saved round, or changed somewhere else."""
    row = an_address(
        user,
        street="12 Example Road",
        postcode="Dublin 4",
        municipality="Dublin",
        country="IE",
        is_primary=True,
    )
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    [drawn, _empty] = rows_of(html)
    assert "data-kept-as-it-was" in drawn["marks"]
    assert "An Eircode is written like D02 AF30." in html

    for changed in ({}, {"kind": "work"}):
        assert save_details(client, user, as_it_is(row, **changed)).status_code == 302
        row.refresh_from_db()
        assert row.postcode == "Dublin 4"

    response = save_details(client, user, as_it_is(row, street="14 Example Road"))
    assert response.status_code == 200, "its address changed, so it answers"
    [drawn] = rows_of(response.content.decode())
    assert drawn["invalid"] == {"postcode"}
    row.refresh_from_db()
    assert (row.street, row.postcode) == ("12 Example Road", "Dublin 4"), "and nothing is written"


@pytest.mark.parametrize("postcode", ["1" * 31, "1000\x00001"])
def test_a_part_its_own_field_refuses_is_not_also_said_to_be_missing(client, user, postcode):
    """Too long, or holding a character no text may: the field says so, and the part is
    not in what was cleaned. It was then also told it "needs a postcode", which it has."""
    response = save_details(client, user, {**IN_LISBON, "postcode": postcode})

    assert response.status_code == 200
    errors = response.context["addresses"].forms[0].errors
    assert list(errors) == ["postcode"]
    assert len(errors["postcode"]) == 1, errors["postcode"]
    assert "needs a postcode" not in response.content.decode()


@pytest.mark.parametrize("country", ["US", "PT", "GB", "NZ", ""])
def test_a_new_row_nobody_wrote_in_is_an_empty_row_whatever_its_menus_show(client, user, country):
    """The country a new row starts on is the chooser's doing, and choosing another one --
    or a kind -- is not writing an address. Four errors under a row holding only *United
    States*; and, for a country with no rules, a row kept holding only its country."""
    response = save_details(client, user, blank(country=country, kind="work"))

    assert response.status_code == 302
    assert not PostalAddress.objects.exists()


def test_an_empty_new_row_is_not_spoken_of_when_the_page_comes_back(client, user):
    """Beside a row that is refused: the empty one has no errors, and is drawn again with
    the country that was chosen on it."""
    response = save_details(
        client, user, {**IN_LISBON, "postcode": "1000"}, blank(country="US", kind="work")
    )

    assert response.status_code == 200
    refused, empty = response.context["addresses"].forms
    assert list(refused.errors) == ["postcode"]
    assert not empty.errors and empty.country_notes == [] and empty.a_place == []
    drawn = rows_of(response.content.decode())[1]
    assert drawn["invalid"] == set() and drawn["chosen"]["country"] == "US"


def test_a_row_being_added_with_white_space_in_its_boxes_is_empty_too(client, user):
    response = save_details(client, user, blank(street="  ", municipality="\t", country="US"))

    assert response.status_code == 302
    assert not PostalAddress.objects.exists()


def test_a_kept_row_holding_only_a_country_is_left_alone(client, user):
    """It predates all of this. Not marked, not told anything, and still there after a save."""
    row = an_address(user, street="", postcode="", municipality="", country="US", is_primary=True)
    client.force_login(user)

    html = client.get(reverse("accounts:profile")).content.decode()
    assert "data-kept-as-it-was" not in html and "data-place-note" not in html

    assert save_details(client, user, as_it_is(row)).status_code == 302
    assert PostalAddress.objects.get().pk == row.pk


# --------------------------------------------------- what the model says about itself (#306)


def test_the_model_still_says_valid_cannot_mean_verified():
    """The sentence stays true and stays written: a format check is not a verification."""
    said = " ".join(PostalAddress.__doc__.split())

    assert "**Valid cannot mean verified.**" in said
    assert "**A format check is not a verification.**" in said
