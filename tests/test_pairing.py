"""A sync connection's *Pairing* page, and the line on each record's page (#327)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from postulo.jobs.models import Company, Contact
from postulo.plugins import pairing, registry, syncing
from postulo.plugins.base import FieldSpec, RemoteItem, SyncReport
from postulo.plugins.base import TestResult as Outcome  # not a test class, despite the name
from postulo.plugins.models import Connection, SyncLink

pytestmark = pytest.mark.django_db

BOOK = "/dav/cards/"


def card(uid, label, *, kinds=("contact", "company"), href=None, detail=""):
    return RemoteItem(
        collection=BOOK,
        uid=uid,
        label=label,
        collection_label="Cards",
        href=href or f"{BOOK}{uid}.vcf",
        detail=detail,
        kinds=kinds,
    )


class Pairs:
    """An address book in miniature, which pairs by hand and remembers what its runs saw."""

    name = "pairs"
    version = "0.1"
    kind = "sync"
    label = "Pairs"
    items: ClassVar[list] = []
    asked: ClassVar[list] = []
    runs: ClassVar[list] = []
    fail_with: ClassVar[Exception | None] = None

    def config_fields(self):
        return [FieldSpec("url", "Server", type="url")]

    def test(self, config):
        return Outcome(True, "fine")

    def remote_items(self, config, search="", kind="", key=None):
        Pairs.asked.append({"search": search, "kind": kind, "key": key})
        if Pairs.fail_with:
            raise Pairs.fail_with
        if key is not None:
            return [item for item in Pairs.items if item.key == tuple(key)]
        return [
            item
            for item in Pairs.items
            if search.lower() in item.label.lower() and (not kind or kind in item.kinds)
        ]

    def fill_record(self, connection, config, item, kind, into=None):
        from postulo.plugins.api import create_contact, update_contact

        if into is not None:
            return update_contact(into, actor=connection.owner, via=self.name, role=item.detail)
        return create_contact(
            None, actor=connection.owner, via=self.name, name=item.label, role=item.detail
        )

    def sync(self, connection, config):
        """What a run sees: for each link, whether it pairs, skips or makes a new twin."""
        seen = {}
        for contact in Contact.objects.for_user(connection.owner):
            link = SyncLink.for_record(connection, contact)
            if link is None:
                seen[contact.name] = "push as new"
            elif not link.paired:
                seen[contact.name] = "leave alone"
            elif not link.etag:
                seen[contact.name] = f"first comparison with {link.remote_href}"
            else:
                seen[contact.name] = "compare"
        Pairs.runs.append(seen)
        return SyncReport()


class Plain:
    """A sync plugin with no `remote_items`: no page."""

    name = "plain"
    version = "0.1"
    kind = "sync"
    label = "Plain"

    def config_fields(self):
        return []

    def test(self, config):
        return Outcome(True)

    def sync(self, connection, config):
        return SyncReport()


@pytest.fixture(autouse=True)
def plugin(settings):
    cache.clear()
    settings.POSTULO_CONNECTION_PAIRING_RATE = "1000/h"
    Pairs.items = [
        card("ada", "Ada Lovelace", detail="Analyst"),
        card("acme", "Acme Ltd", kinds=("company",)),
        card("me", "Me myself", kinds=("you",)),
    ]
    Pairs.asked = []
    Pairs.runs = []
    Pairs.fail_with = None
    registry.register_builtin("sync", Pairs)
    registry.register_builtin("sync", Plain)
    yield Pairs
    registry.unregister_builtin("sync", Pairs)
    registry.unregister_builtin("sync", Plain)


def a_connection(user, plugin="pairs", label="Phone"):
    return Connection.objects.create(
        owner=user,
        kind="sync",
        plugin=plugin,
        label=label,
        config={"url": "https://dav.example.org/", "interval": "60"},
    )


def page(connection):
    return reverse("connections:pairing", args=[connection.pk])


def apply(client, connection, **data):
    return client.post(reverse("connections:pairing_apply", args=[connection.pk]), data)


def the_run(connection):
    syncing.sync_connection(connection)
    return Pairs.runs[-1]


# ------------------------------------------------------------------------ the surface


def test_an_item_keeps_its_parts_and_its_key():
    item = card("ada", "Ada")
    assert item.key == (BOOK, "ada")
    assert pairing.tidy([item, item, "not an item", card("", "No uid")]) == [item]


def test_tidy_drops_kinds_nobody_knows_and_items_left_with_none():
    odd = card("x", "X", kinds=("planet", "contact", "contact"))
    assert pairing.tidy([odd])[0].kinds == ("contact",)
    assert pairing.tidy([card("y", "Y", kinds=("planet",))]) == []
    assert pairing.tidy([card("z", "Z", kinds=("company",))], kind="contact") == []


def test_the_vocabulary_is_the_cores_own():
    from postulo.plugins import api

    assert api.RECORD_KINDS == tuple(pairing.KINDS)
    assert api.RemoteItem is RemoteItem


# ------------------------------------------------------------------------- the page


def test_a_plugin_without_the_method_gets_no_page(client, user):
    connection = a_connection(user, plugin="plain")
    client.force_login(user)
    assert client.get(page(connection)).status_code == 404
    listed = client.get(reverse("connections:list")).content.decode()
    assert page(connection) not in listed


def test_the_list_offers_the_page_of_a_plugin_that_has_it(client, user):
    connection = a_connection(user)
    client.force_login(user)
    assert page(connection) in client.get(reverse("connections:list")).content.decode()


def test_the_page_opens_on_what_is_paired_without_asking_anybody(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    SyncLink.bind(connection, ada, collection=BOOK, uid="ada", remote_href=f"{BOOK}ada.vcf")
    gone = Contact.objects.create(owner=user, name="Grace")
    SyncLink.bind(connection, gone, collection=BOOK, uid="grace", remote_gone=True)
    client.force_login(user)
    response = client.get(page(connection))
    html = response.content.decode()
    assert response.status_code == 200
    assert Pairs.asked == []
    assert "Ada" in html and f"{BOOK}ada.vcf" in html
    assert "Removed on the other side" in html
    assert reverse("connections:pairing_renew", args=[connection.pk]) in html


def test_a_search_asks_the_plugin_and_draws_a_table_in_a_live_region(client, user):
    connection = a_connection(user)
    client.force_login(user)
    html = client.get(page(connection), {"q": "ada", "kind": "contact"}).content.decode()
    assert Pairs.asked == [{"search": "ada", "kind": "contact", "key": None}]
    assert 'aria-live="polite"' in html
    assert "<caption" in html and "Ada Lovelace" in html
    assert "Acme Ltd" not in html


def test_show_unpaired_leaves_out_what_is_paired(client, user):
    connection = a_connection(user)
    acme = Company.objects.create(owner=user, name="Acme")
    SyncLink.bind(connection, acme, collection=BOOK, uid="acme")
    client.force_login(user)
    html = client.get(page(connection), {"unpaired": "1"}).content.decode()
    assert "Ada Lovelace" in html
    assert "Acme Ltd" not in html


def test_a_label_from_the_server_is_escaped_and_never_a_link(client, user):
    Pairs.items = [card("x", '<a href="https://evil.example/">Click</a>', href="https://evil")]
    connection = a_connection(user)
    client.force_login(user)
    html = client.get(page(connection), {"q": "click"}).content.decode()
    assert "&lt;a href=" in html
    assert 'href="https://evil' not in html


def test_a_failure_is_the_plugins_own_words(client, user):
    Pairs.fail_with = RuntimeError("the server said no")
    connection = a_connection(user)
    client.force_login(user)
    html = client.get(page(connection), {"q": "x"}).content.decode()
    assert "the server said no" in html


def test_asking_has_an_allowance_of_its_own(client, user, settings):
    settings.POSTULO_CONNECTION_PAIRING_RATE = "1/h"
    connection = a_connection(user)
    client.force_login(user)
    client.get(page(connection), {"q": "a"})
    html = client.get(page(connection), {"q": "a"}).content.decode()
    assert "a lot of questions" in html
    assert len(Pairs.asked) == 1
    # *Test* is untouched by it.
    response = client.post(reverse("connections:test", args=[connection.pk]), follow=True)
    assert "a lot of tests" not in response.content.decode()


def test_the_item_page_asks_by_key_and_offers_the_persons_own_records(client, user, other_user):
    connection = a_connection(user)
    Contact.objects.create(owner=user, name="Ada mine")
    Contact.objects.create(owner=other_user, name="Ada theirs")
    client.force_login(user)
    response = client.get(
        reverse("connections:pairing_item", args=[connection.pk]),
        {"collection": BOOK, "uid": "ada", "kind": "contact", "q": "ada"},
    )
    html = response.content.decode()
    assert Pairs.asked[-1]["key"] == (BOOK, "ada")
    assert "Ada mine" in html and "Ada theirs" not in html
    assert 'value="new"' in html  # the plugin can fill a contact


# --------------------------------------------------- pair, unpair, pair again, create


def test_pairing_writes_the_link_from_the_plugins_answer_and_the_next_run_sees_it(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    client.force_login(user)
    response = apply(
        client,
        connection,
        collection=BOOK,
        uid="ada",
        kind="contact",
        record=ada.pk,
        action="pair",
        # Never read: the address comes from the plugin.
        href="https://attacker.example/steal",
        remote_href="https://attacker.example/steal",
    )
    assert response.status_code == 302
    link = SyncLink.for_record(connection, ada)
    assert link.remote_href == f"{BOOK}ada.vcf"
    assert (link.collection, link.uid, link.etag) == (BOOK, "ada", "")
    assert the_run(connection) == {"Ada": f"first comparison with {BOOK}ada.vcf"}


def test_unpairing_keeps_both_sides_and_the_next_run_leaves_them_alone(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    link = SyncLink.bind(connection, ada, collection=BOOK, uid="ada", etag='"1"')
    client.force_login(user)
    client.post(reverse("connections:pairing_unpair", args=[connection.pk]), {"link": link.pk})
    link.refresh_from_db()
    assert not link.paired
    assert Contact.objects.filter(pk=ada.pk).exists()
    assert the_run(connection) == {"Ada": "leave alone"}


def test_pairing_again_after_unpairing_pairs_again(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    link = SyncLink.bind(connection, ada, collection=BOOK, uid="ada", etag='"1"')
    pairing.unpair(link)
    client.force_login(user)
    apply(
        client, connection, collection=BOOK, uid="ada", kind="contact", record=ada.pk, action="pair"
    )
    link.refresh_from_db()
    assert link.paired and link.etag == ""
    assert the_run(connection) == {"Ada": f"first comparison with {BOOK}ada.vcf"}


def test_a_twin_gone_can_be_made_again_by_the_plugin(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    link = SyncLink.bind(connection, ada, collection=BOOK, uid="gone", remote_gone=True)
    client.force_login(user)
    client.post(reverse("connections:pairing_renew", args=[connection.pk]), {"link": link.pk})
    assert not SyncLink.objects.filter(pk=link.pk).exists()
    assert the_run(connection) == {"Ada": "push as new"}


def test_a_pairing_still_standing_is_not_forgotten_by_make_a_new_one(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    link = SyncLink.bind(connection, ada, collection=BOOK, uid="ada")
    client.force_login(user)
    client.post(reverse("connections:pairing_renew", args=[connection.pk]), {"link": link.pk})
    assert SyncLink.objects.filter(pk=link.pk).exists()


def test_an_item_another_record_holds_is_refused_in_words(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    other = Contact.objects.create(owner=user, name="Other")
    SyncLink.bind(connection, ada, collection=BOOK, uid="ada")
    client.force_login(user)
    response = apply(
        client,
        connection,
        collection=BOOK,
        uid="ada",
        kind="contact",
        record=other.pk,
        action="pair",
        next=page(connection),
    )
    assert "already paired with Ada" in client.get(response.url).content.decode()
    assert SyncLink.for_record(connection, other) is None


def test_a_kind_the_item_does_not_offer_is_refused(client, user):
    connection = a_connection(user)
    acme = Company.objects.create(owner=user, name="Acme")
    client.force_login(user)
    apply(
        client, connection, collection=BOOK, uid="me", kind="company", record=acme.pk, action="pair"
    )
    assert SyncLink.for_record(connection, acme) is None


def test_an_item_no_longer_there_is_refused(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    client.force_login(user)
    apply(
        client,
        connection,
        collection=BOOK,
        uid="vanished",
        kind="contact",
        record=ada.pk,
        action="pair",
    )
    assert SyncLink.for_record(connection, ada) is None


def test_creating_from_an_item_names_the_plugin_and_pairs(client, user):
    connection = a_connection(user)
    client.force_login(user)
    apply(
        client, connection, collection=BOOK, uid="ada", kind="contact", record="new", action="fill"
    )
    made = Contact.objects.get(owner=user, name="Ada Lovelace")
    assert made.role == "Analyst"
    assert SyncLink.for_record(connection, made).uid == "ada"
    assert the_run(connection) == {"Ada Lovelace": f"first comparison with {BOOK}ada.vcf"}


def test_a_one_off_import_makes_the_record_and_pairs_nothing(client, user):
    connection = a_connection(user)
    client.force_login(user)
    apply(
        client,
        connection,
        collection=BOOK,
        uid="ada",
        kind="contact",
        record="new",
        action="import",
    )
    made = Contact.objects.get(owner=user, name="Ada Lovelace")
    assert SyncLink.for_record(connection, made) is None
    assert the_run(connection) == {"Ada Lovelace": "push as new"}


def test_filling_an_existing_record_fills_that_one(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    client.force_login(user)
    apply(
        client, connection, collection=BOOK, uid="ada", kind="contact", record=ada.pk, action="fill"
    )
    ada.refresh_from_db()
    assert ada.role == "Analyst"
    assert SyncLink.for_record(connection, ada).uid == "ada"


def test_you_are_paired_and_never_made(client, user):
    connection = a_connection(user)
    client.force_login(user)
    apply(client, connection, collection=BOOK, uid="me", kind="you", record="new", action="fill")
    assert not SyncLink.objects.filter(connection=connection).exists()
    apply(client, connection, collection=BOOK, uid="me", kind="you", record=user.pk, action="pair")
    assert SyncLink.for_record(connection, user).uid == "me"


# ----------------------------------------------------------------------- the isolation


def test_somebody_elses_record_is_a_404(client, user, other_user):
    connection = a_connection(user)
    theirs = Contact.objects.create(owner=other_user, name="Theirs")
    client.force_login(user)
    response = apply(
        client,
        connection,
        collection=BOOK,
        uid="ada",
        kind="contact",
        record=theirs.pk,
        action="pair",
    )
    assert response.status_code == 404
    assert client.get(page(connection), {"for": f"contact-{theirs.pk}"}).status_code == 404
    response = apply(
        client,
        connection,
        collection=BOOK,
        uid="me",
        kind="you",
        record=other_user.pk,
        action="pair",
    )
    assert response.status_code == 404


def test_somebody_elses_link_is_a_404(client, user, other_user):
    mine = a_connection(user)
    theirs = a_connection(other_user)
    contact = Contact.objects.create(owner=other_user, name="Theirs")
    link = SyncLink.bind(theirs, contact, collection=BOOK, uid="x")
    client.force_login(user)
    for name in ("connections:pairing_unpair", "connections:pairing_renew"):
        response = client.post(reverse(name, args=[mine.pk]), {"link": link.pk})
        assert response.status_code == 404, name
    link.refresh_from_db()
    assert link.paired


# ------------------------------------------------------------------- the record's line


def test_a_record_page_draws_its_pairing_without_asking(client, user):
    connection = a_connection(user)
    acme = Company.objects.create(owner=user, name="Acme")
    SyncLink.bind(connection, acme, collection=BOOK, uid="acme", last_synced_at=timezone.now())
    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[acme.pk])).content.decode()
    assert Pairs.asked == []
    assert "data-pairing-line" in html and "Paired through" in html
    assert reverse("connections:pairing_unpair", args=[connection.pk]) in html
    assert f"?for=company-{acme.pk}" in html


def test_a_gone_twin_says_so_on_the_contact_page(client, user):
    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    SyncLink.bind(connection, ada, collection=BOOK, uid="ada", remote_gone=True)
    client.force_login(user)
    html = client.get(reverse("jobs:contact_update", args=[ada.pk])).content.decode()
    assert "Removed on the other side" in html


def test_your_details_offer_to_pair_you(client, user):
    a_connection(user)
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert f"?for=you-{user.pk}" in html


def test_no_line_without_a_connection_that_pairs(client, user):
    a_connection(user, plugin="plain")
    acme = Company.objects.create(owner=user, name="Acme")
    client.force_login(user)
    html = client.get(reverse("jobs:company_detail", args=[acme.pk])).content.decode()
    assert "data-pairing-line" not in html


# --------------------------------------------------------------- the published helper


def test_the_helper_pairs_unpairs_and_creates_as_the_page_does(user):
    from postulo.plugins.testing import create_by_hand, pair_by_hand, unpair_by_hand

    connection = a_connection(user)
    ada = Contact.objects.create(owner=user, name="Ada")
    link = pair_by_hand(connection, ada, collection=BOOK, uid="ada")
    assert link.remote_href == f"{BOOK}ada.vcf" and link.etag == ""
    assert the_run(connection)["Ada"].startswith("first comparison")
    unpair_by_hand(connection, ada)
    assert the_run(connection)["Ada"] == "leave alone"
    made = create_by_hand(connection, collection=BOOK, uid="ada", kind="contact")
    assert made.name == "Ada Lovelace"
    assert SyncLink.for_record(connection, made).paired
    once = create_by_hand(connection, collection=BOOK, uid="ada", kind="contact", pair=False)
    assert SyncLink.for_record(connection, once) is None
    with pytest.raises(pairing.Refusal):
        pair_by_hand(connection, ada, collection=BOOK, uid="vanished")


def test_somebody_elses_connection_is_a_404_at_every_address(client, user, other_user):
    theirs = a_connection(other_user)
    client.force_login(user)
    for name in ("connections:pairing", "connections:pairing_item"):
        url = reverse(name, args=[theirs.pk])
        assert client.get(url, {"q": "a", "collection": BOOK, "uid": "ada"}).status_code == 404
    for name in (
        "connections:pairing_apply",
        "connections:pairing_unpair",
        "connections:pairing_renew",
    ):
        assert client.post(reverse(name, args=[theirs.pk]), {}).status_code == 404
    assert Pairs.asked == []
