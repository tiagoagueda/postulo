"""A card file is hostile until it is read, and a card is one account's (#660).

The upload is bounded in size, in cards, in properties and in the length of a line; a
`PHOTO`, a `LOGO`, a `KEY` or a `SOURCE` address makes no request, ever; and what is shown
and written is the signed-in account's own, never another's.
"""

from __future__ import annotations

import socket
import time

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.core import vcard
from postulo.core.models import PhoneNumber
from postulo.jobs import vcards
from postulo.jobs.models import Company, Contact

pytestmark = pytest.mark.django_db


def _card(body: str) -> str:
    return f"BEGIN:VCARD\r\nVERSION:4.0\r\n{body}END:VCARD\r\n"


# ------------------------------------------------------------------------- bounded


def test_a_file_over_the_size_limit_is_not_read():
    with pytest.raises(vcard.VCardRefused):
        vcard.read(b"x" * (vcard.MAX_BYTES + 1))


def test_the_upload_view_refuses_a_file_over_the_limit_before_reading_it(client, user):
    client.force_login(user)
    big = SimpleUploadedFile("big.vcf", b"x" * (vcard.MAX_BYTES + 10), "text/vcard")
    response = client.post(reverse("jobs:contact_vcards"), {"file": big}, follow=True)
    assert "larger than 2 MB" in response.content.decode()
    assert response.context["entries"] is None


def test_only_the_first_cards_are_read_and_the_rest_are_counted():
    text = "".join(_card(f"FN:Person {n}\r\n") for n in range(vcard.MAX_CARDS + 25))
    read = vcard.read(text)
    assert len(read.cards) == vcard.MAX_CARDS and read.skipped == 25
    assert read.cards[-1].name == f"Person {vcard.MAX_CARDS - 1}"


def test_a_cards_properties_are_bounded():
    body = "FN:A\r\n" + "".join(f"X-N{n}:v\r\n" for n in range(vcard.MAX_PROPERTIES * 3))
    (card,) = vcard.read(_card(body)).cards
    assert card.name == "A" and len(card.dropped) <= vcard.MAX_PROPERTIES * 3


def test_a_card_with_thousands_of_numbers_keeps_a_bounded_few():
    body = "FN:A\r\n" + "".join(f"TEL:+3519{n:08d}\r\n" for n in range(1000))
    (card,) = vcard.read(_card(body)).cards
    assert len(card.phones) == vcard.MAX_PER_KIND


def test_a_very_long_line_is_skipped_and_named_not_read():
    huge = "A" * (vcard.MAX_LINE * 50)
    body = f"FN:Alex\r\nNOTE:{huge}\r\nPHOTO;ENCODING=b:{huge}\r\n"
    folded = body + "X-FOLDED:" + "".join("\r\n " + "B" * 70 for _ in range(5000)) + "\r\n"
    (card,) = vcard.read(_card(folded)).cards
    assert card.name == "Alex" and card.notes == ""
    assert {"NOTE", "PHOTO", "X-FOLDED"} <= set(card.dropped)


def test_a_hostile_file_is_read_in_a_blink():
    """Nothing nests and nothing backtracks: a file of the worst shapes costs linear time."""
    nasty = (
        "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:" + "\\" * 50000 + "\r\n"
        "ADR:" + ";" * 6000 + "\r\n" + 'TEL;TYPE="' + "," * 6000 + "\r\n"
        "NOTE:" + "\\n" * 6000 + "\r\nEND:VCARD\r\n"
    ) * 20
    started = time.monotonic()
    vcard.read(nasty)
    assert time.monotonic() - started < 5


def test_a_line_break_in_a_name_cannot_forge_a_property_in_the_written_card():
    card = vcard.Card(name="A\rTEL:+1 555 0100", role="B\nURL:https://evil.example")
    text = vcard.card_to_text(card)
    assert "\r\nTEL:" not in text and "\r\nURL:" not in text


# --------------------------------------------------------------------- never fetched


def test_a_photo_a_logo_a_key_or_a_source_makes_no_request(client, user, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a card made a request")

    from postulo.plugins import http

    for name in ("client", "public_only_client", "approve_host", "check_destination"):
        monkeypatch.setattr(http, name, refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    client.force_login(user)
    hostile = _card(
        "FN:Sam Rivera\r\nPHOTO;VALUE=uri:http://169.254.169.254/latest/meta-data/\r\n"
        "LOGO;VALUE=uri:http://localhost:8000/admin/\r\nKEY;VALUE=uri:http://10.0.0.1/key\r\n"
        "SOURCE:http://127.0.0.1/card.vcf\r\nURL:http://127.0.0.1/\r\n"
    )
    url = reverse("jobs:contact_vcards")
    client.post(url, {"file": SimpleUploadedFile("c.vcf", hostile.encode())})
    page = client.get(url).content.decode()
    assert "PHOTO" in page and "LOGO" in page and "KEY" in page and "SOURCE" in page
    client.post(url, {"action": "confirm", "chosen": ["0"]})
    assert Contact.objects.get(owner=user).name == "Sam Rivera"


def test_a_web_address_that_is_not_http_is_not_kept(user):
    text = _card(
        "FN:A\r\nURL:javascript:alert(1)\r\nURL:file:///etc/passwd\r\nURL:data:text/html,x\r\n"
    )
    report = vcards.apply(user, vcard.read(text).cards, {0})
    assert not Contact.objects.get(owner=user).web_links.exists()
    assert len(report.problems) == 3


# --------------------------------------------------------------------- one account's


def test_nothing_in_a_card_names_another_accounts_record(user, other_user):
    theirs = Company.objects.create(owner=other_user, name="Theirs Ltd")
    their_contact = Contact.objects.create(owner=other_user, company=theirs, name="Their Person")
    PhoneNumber.objects.create(owner=other_user, holder=their_contact, number="+351912000111")
    mine = vcards.card_for_contact(Contact.objects.create(owner=user, name="My Person"), notes=True)
    text = vcard.card_to_text(mine)
    assert "Their" not in text and "000111" not in text
    # An import for this account never reaches the other's records: a company or a contact
    # of the same name in another account is not "already here".
    cards = vcard.read(_card("FN:Their Person\r\nORG:Theirs Ltd\r\n")).cards
    (entry,) = vcards.review(user, cards)
    assert entry.outcome == "add"
    vcards.apply(user, cards, {0})
    assert Company.objects.filter(owner=user, name="Theirs Ltd").count() == 1
    assert Company.objects.get(owner=user, name="Theirs Ltd").pk != theirs.pk
    assert Contact.objects.filter(owner=other_user).count() == 1


def test_the_held_cards_of_one_session_are_not_another_accounts(client, user, other_user):
    client.force_login(user)
    url = reverse("jobs:contact_vcards")
    client.post(url, {"file": SimpleUploadedFile("c.vcf", _card("FN:Held Person\r\n").encode())})
    assert "Held Person" in client.get(url).content.decode()
    client.logout()
    client.force_login(other_user)
    assert "Held Person" not in client.get(url).content.decode()


def test_a_downloaded_card_file_is_never_cached_or_sniffed(client, user):
    client.force_login(user)
    response = client.get(reverse("jobs:own_vcard"))
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Type"] == "text/vcard; charset=utf-8"
    assert response["Content-Disposition"].startswith("attachment")


def test_an_anonymous_visitor_is_sent_to_sign_in(client, user):
    contact = Contact.objects.create(owner=user, name="X")
    for name, args in (
        ("jobs:contact_vcards", []),
        ("jobs:contacts_vcard", []),
        ("jobs:own_vcard", []),
        ("jobs:contact_vcard", [contact.pk]),
    ):
        response = client.get(reverse(name, args=args))
        assert response.status_code == 302 and "login" in response["Location"], name
