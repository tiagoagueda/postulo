"""The primary number and the primary link of a holder whose rows were already read (#1171).

A sync that walks every contact asks, for each one, which number and which profile to put on
its card. Both answers were a query each however the contacts had been loaded, so a walk over
a thousand contacts was two thousand queries for rows a prefetch had already brought. They are
read from those rows now where there are any, and asked of the database as before where there
are none.

What is protected here is that the answer is the same either way, and that it stays the same
after something is written: a holder read once and then changed must not go on answering from
what it read.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from postulo.core import phone_numbers, web_links
from postulo.core.models import PhoneNumber, WebLink
from postulo.jobs.models import Company, Contact

pytestmark = pytest.mark.django_db

SOCIAL = WebLink.Kind.SOCIAL
REPOSITORY = WebLink.Kind.REPOSITORY
WEBSITE = WebLink.Kind.WEBSITE
KINDS = (SOCIAL, REPOSITORY, WEBSITE)


def number(holder, owner, value, *, primary=False):
    return PhoneNumber.objects.create(owner=owner, holder=holder, number=value, is_primary=primary)


def link(holder, owner, url, *, kind=SOCIAL, primary=False):
    return WebLink.objects.create(
        owner=owner, holder=holder, kind=kind, url=url, is_primary=primary
    )


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture")


@pytest.fixture
def contact(user, company):
    return Contact.objects.create(owner=user, company=company, name="Cave Johnson")


def furnish(user, company, count: int) -> None:
    """Contacts of every shape a walk meets: with a primary and others, with rows and no
    primary, with a primary of another kind only, and with nothing at all."""
    for index in range(count):
        holder = Contact.objects.create(owner=user, company=company, name=f"Contact {index}")
        shape = index % 4
        if shape == 0:
            number(holder, user, f"+35121000{index:04d}")
            number(holder, user, f"+35191000{index:04d}", primary=True)
            link(holder, user, f"https://mastodon.example/@c{index}")
            link(holder, user, f"https://www.linkedin.com/in/c{index}", primary=True)
            link(holder, user, f"https://c{index}.example", kind=WEBSITE, primary=True)
        elif shape == 1:
            number(holder, user, f"+35121000{index:04d}")
            link(holder, user, f"https://mastodon.example/@c{index}")
        elif shape == 2:
            link(holder, user, f"https://codeberg.org/c{index}", kind=REPOSITORY, primary=True)


def walk(contacts) -> tuple[list, int]:
    """What a sync does with each contact, by the names a plugin calls, and what it cost."""
    from postulo.plugins.api import primary_phone_number, primary_web_link

    with CaptureQueriesContext(connection) as asked:
        found = []
        for holder in contacts:
            phone = primary_phone_number(holder)
            profile = primary_web_link(holder, SOCIAL)
            found.append((holder.pk, phone and phone.pk, profile and profile.pk))
    return found, len(asked)


def test_a_walk_over_prefetched_contacts_asks_the_database_nothing(user, company):
    """Every contact cost two queries, for rows the walk had already loaded (#1171)."""
    furnish(user, company, 12)
    mine = Contact.objects.for_user(user).order_by("pk")

    as_before, cost_before = walk(list(mine))
    prefetched, cost = walk(list(mine.prefetch_related("phone_numbers", "web_links")))

    assert cost == 0
    assert cost_before == 24, "and a holder nobody prefetched is asked as it always was"
    assert prefetched == as_before
    assert sum(1 for _pk, phone, _profile in prefetched if phone) == 3
    assert sum(1 for _pk, _phone, profile in prefetched if profile) == 3


def test_every_kind_reads_the_same_from_the_rows_as_from_the_database(user, company):
    """The link of each kind, for every shape of holder, is the one a query would find."""
    furnish(user, company, 8)
    mine = Contact.objects.for_user(user).order_by("pk")

    def answers(contacts):
        return [
            (holder.pk, kind, getattr(web_links.primary_for(holder, kind), "pk", None))
            for holder in contacts
            for kind in KINDS
        ]

    asked = answers(list(mine))
    with CaptureQueriesContext(connection) as queries:
        read = answers(list(mine.prefetch_related("web_links")))

    assert read == asked
    # The contacts and their links, and nothing for any of the twenty-four answers.
    assert len(queries) == 2
    assert sum(1 for _pk, _kind, found in read if found) == 6


def test_rows_with_no_primary_among_them_say_none(user, contact):
    """Numbers and no primary is an answer too, and it is not *the first one*."""
    number(contact, user, "+351211111111")
    link(contact, user, "https://mastodon.example/@cave")
    held = Contact.objects.prefetch_related("phone_numbers", "web_links").get(pk=contact.pk)

    assert phone_numbers.primary_for(held) is None
    assert web_links.primary_for(held, SOCIAL) is None


def test_a_number_saved_on_a_prefetched_holder_is_the_one_read_next(user, contact):
    """The rows read before a write are no longer the holder's rows: the next question is
    asked of the database, and so is the writer's own (#1171)."""
    number(contact, user, "+351912345678", primary=True)
    held = Contact.objects.prefetch_related("phone_numbers").get(pk=contact.pk)
    assert phone_numbers.primary_for(held).number == "+351912345678"

    phone_numbers.save_only_number(held, user, "+351933333333")

    assert phone_numbers.primary_for(held).number == "+351933333333"
    assert held.phone_numbers.count() == 1, "the primary was edited, not joined by another"

    phone_numbers.save_only_number(held, user, "")

    assert phone_numbers.primary_for(held) is None


def test_a_first_number_saved_twice_on_a_prefetched_holder_is_one_row(user, contact):
    """A holder read with no numbers and then given one has one: the second save edits the
    row the first made, where reading the old rows would have made a second primary."""
    held = Contact.objects.prefetch_related("phone_numbers").get(pk=contact.pk)
    assert phone_numbers.primary_for(held) is None

    phone_numbers.save_only_number(held, user, "+351912345678")
    phone_numbers.save_only_number(held, user, "+351933333333")

    assert [row.number for row in PhoneNumber.objects.all()] == ["+351933333333"]
    assert phone_numbers.primary_for(held).number == "+351933333333"


def test_a_primary_settled_on_a_prefetched_holder_is_the_one_read_next(user, contact):
    """Numbers with no primary, read, and then the first of them made the primary."""
    first = number(contact, user, "+351211111111")
    number(contact, user, "+351222222222")
    held = Contact.objects.prefetch_related("phone_numbers").get(pk=contact.pk)
    assert phone_numbers.primary_for(held) is None

    phone_numbers.ensure_one_primary(held)

    assert phone_numbers.primary_for(held).pk == first.pk


def test_a_link_saved_on_a_prefetched_holder_is_the_one_read_next(user, contact):
    """The same for an address on the web, kind by kind (#1171)."""
    link(contact, user, "https://www.linkedin.com/in/cave", primary=True)
    held = Contact.objects.prefetch_related("web_links").get(pk=contact.pk)
    assert web_links.primary_for(held, SOCIAL).url == "https://www.linkedin.com/in/cave"

    web_links.save_only_link(held, user, SOCIAL, "https://www.linkedin.com/in/cave-johnson")
    web_links.save_only_link(held, user, WEBSITE, "https://cave.example")

    assert web_links.primary_for(held, SOCIAL).url == "https://www.linkedin.com/in/cave-johnson"
    assert web_links.primary_for(held, WEBSITE).url == "https://cave.example"
    assert held.web_links.count() == 2

    web_links.save_only_link(held, user, SOCIAL, "")

    assert web_links.primary_for(held, SOCIAL) is None


def test_links_added_to_a_prefetched_holder_are_read_next(user, contact):
    """Rows taken in without a page settle a primary, and the holder says which."""
    held = Contact.objects.prefetch_related("web_links").get(pk=contact.pk)
    assert web_links.primary_for(held, REPOSITORY) is None

    rows = web_links.checked_rows(
        [{"kind": REPOSITORY, "url": "https://codeberg.org/cave", "is_primary": False}]
    )
    (made,) = web_links.add_links(held, user, rows)

    assert web_links.primary_for(held, REPOSITORY).pk == made.pk


def test_rows_saved_from_a_page_on_a_prefetched_holder_are_read_next(user, contact):
    """The block of rows on a page writes through the holder it was given as well."""
    held = Contact.objects.prefetch_related("phone_numbers", "web_links").get(pk=contact.pk)
    assert phone_numbers.primary_for(held) is None

    numbers = phone_numbers.formset_for(
        held,
        asked_by=user,
        data={
            "phone_numbers-TOTAL_FORMS": "1",
            "phone_numbers-INITIAL_FORMS": "0",
            "phone_numbers-MIN_NUM_FORMS": "0",
            "phone_numbers-MAX_NUM_FORMS": "1000",
            "phone_numbers-0-kind": "work",
            "phone_numbers-0-label": "",
            "phone_numbers-0-number_0": "PT",
            "phone_numbers-0-number_1": "+351912345678",
        },
    )
    assert numbers.is_valid(), numbers.errors
    numbers.save()

    links = web_links.formset_for(
        held,
        WEBSITE,
        data={
            "websites-TOTAL_FORMS": "1",
            "websites-INITIAL_FORMS": "0",
            "websites-MIN_NUM_FORMS": "0",
            "websites-MAX_NUM_FORMS": "1000",
            "websites-0-url": "https://cave.example",
        },
        prefix="websites",
    )
    assert links.is_valid(), links.errors
    links.save()

    assert phone_numbers.primary_for(held).number == "+351912345678"
    assert web_links.primary_for(held, WEBSITE).url == "https://cave.example"
