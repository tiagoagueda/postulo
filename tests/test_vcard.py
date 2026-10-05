"""Contacts as vCard 4.0: one mapping, written and read by hand (#660).

`core.vcard` holds no model, so most of this needs no database: a card as plain values is
written, and the text is read back. The rows (a contact's numbers, addresses and links) are
turned into those values by `jobs.vcards`, and the last half of this file runs that against
the database.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from postulo.core import vcard
from postulo.core.models import MessagingHandle, PhoneNumber, PostalAddress, WebLink
from postulo.jobs import vcards
from postulo.jobs.models import Company, Contact, Department

CARD = vcard.Card(
    name="Alex Morgan",
    company="Acme",
    department="Legal",
    role="Counsel",
    email="alex@acme.example",
    phones=(
        vcard.Phone("+351 912 345 678", "mobile", True),
        vcard.Phone("+351 21 123 4567", "switchboard"),
        vcard.Phone("+351 21 123 4568", "fax"),
        vcard.Phone("+351 21 123 4569", "work"),
        vcard.Phone("+351 21 123 4560", "home"),
    ),
    addresses=(
        vcard.Address("Rua do Exemplo 1\n2.º", "1000-100", "Lisboa", "Lisboa", "PT", "work", True),
    ),
    links=(vcard.Link("https://acme.example/alex"),),
    notes="Met at a fair, likes tea.",
    uid="urn:uuid:5b0f3a8e-0000-4000-8000-000000000001",
)


def lines(text: str) -> list[str]:
    """The unfolded content lines of some vCard text."""
    return text.replace("\r\n ", "").split("\r\n")


# --------------------------------------------------------------- each row of the mapping


def test_a_card_is_version_4_with_its_identity():
    text = vcard.card_to_text(CARD, revised=dt.datetime(2026, 10, 5, 12, 30, tzinfo=dt.UTC))
    found = lines(text)
    assert found[0] == "BEGIN:VCARD" and found[1] == "VERSION:4.0"
    assert found[2].startswith("PRODID:-//Postulo//Postulo ")
    assert "UID:urn:uuid:5b0f3a8e-0000-4000-8000-000000000001" in found
    assert "REV:20261005T123000Z" in found
    assert found[-2] == "END:VCARD" and found[-1] == ""
    assert text.count("\r\n") == len(text.splitlines())


def test_the_name_is_fn_and_n_is_never_invented():
    found = lines(vcard.card_to_text(CARD))
    assert "FN:Alex Morgan" in found
    assert not [line for line in found if line.startswith("N:") or line.startswith("N;")]


def test_n_is_written_when_the_card_has_a_given_and_a_family_name():
    person = vcard.Card(name="Alex Morgan", given="Alex", family="Morgan")
    assert "N:Morgan;Alex;;;" in lines(vcard.card_to_text(person))


def test_a_company_and_a_department_are_one_org():
    assert "ORG:Acme;Legal" in lines(vcard.card_to_text(CARD))
    assert "ORG:Acme" in lines(vcard.card_to_text(vcard.Card(name="x", company="Acme")))


def test_the_role_is_the_title_and_the_email_is_work():
    found = lines(vcard.card_to_text(CARD))
    assert "TITLE:Counsel" in found
    assert "EMAIL;TYPE=work:alex@acme.example" in found


def test_a_number_is_a_tel_uri_with_a_type_and_the_primary_is_pref_1():
    found = lines(vcard.card_to_text(CARD))
    assert "TEL;VALUE=uri;TYPE=cell;PREF=1:tel:+351912345678" in found
    assert "TEL;VALUE=uri;TYPE=work,voice:tel:+351211234567" in found
    assert "TEL;VALUE=uri;TYPE=fax:tel:+351211234568" in found
    assert "TEL;VALUE=uri;TYPE=work:tel:+351211234569" in found
    assert "TEL;VALUE=uri;TYPE=home:tel:+351211234560" in found


def test_a_number_that_never_reached_international_form_is_written_as_typed():
    text = vcard.card_to_text(vcard.Card(name="x", phones=(vcard.Phone("912 345 678"),)))
    assert "TEL;VALUE=text:912 345 678" in lines(text)


def test_an_address_is_adr_in_rfc_order_with_cc_beside_the_country_name():
    found = lines(vcard.card_to_text(CARD))
    assert (
        "ADR;TYPE=work;PREF=1;CC=PT:;;Rua do Exemplo 1\\n2.º;Lisboa;Lisboa;1000-100;Portugal"
        in found
    )


def test_a_web_link_is_a_url_and_the_service_is_not_written():
    assert "URL:https://acme.example/alex" in lines(vcard.card_to_text(CARD))


def test_notes_are_written_only_when_the_card_carries_them():
    assert "NOTE:Met at a fair\\, likes tea." in lines(vcard.card_to_text(CARD))
    bare = vcard.Card(name="x")
    assert not [line for line in lines(vcard.card_to_text(bare)) if line.startswith("NOTE")]


def test_a_company_card_is_kind_org():
    company = vcard.Card(
        name="Acme",
        kind=vcard.KIND_ORG,
        company="Acme",
        links=(vcard.Link("https://acme.example"),),
    )
    found = lines(vcard.card_to_text(company))
    assert "KIND:org" in found and "FN:Acme" in found and "ORG:Acme" in found
    assert "URL:https://acme.example" in found


def test_messaging_handles_are_impp_for_the_services_with_a_uri():
    assert vcard.impp_for("xmpp", "alex@example.org") == "xmpp:alex@example.org"
    assert vcard.impp_for("matrix", "@alex:example.org") == "matrix:u/alex:example.org"
    assert vcard.impp_for("signal", "alex.42") == ""
    assert vcard.impp_read("xmpp:alex@example.org?message") == ("xmpp", "alex@example.org")
    assert vcard.impp_read("matrix:u/alex:example.org") == ("matrix", "@alex:example.org")
    assert vcard.impp_read("sip:alex@example.org") is None


def test_the_identifier_is_stable_and_names_the_record_and_the_instance():
    first = vcard.uid_for("https://a.example", "contact", 7)
    assert first == vcard.uid_for("https://a.example", "contact", 7)
    assert first.startswith("urn:uuid:")
    assert first != vcard.uid_for("https://b.example", "contact", 7)
    assert first != vcard.uid_for("https://a.example", "contact", 8)
    assert first != vcard.uid_for("https://a.example", "company", 7)


# ----------------------------------------------------------------------------- folding


def test_a_line_is_folded_at_75_octets_and_never_inside_a_character():
    note = "é" * 200 + " " + "日本語" * 40
    text = vcard.card_to_text(vcard.Card(name="x", notes=note))
    for physical in text.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
        physical.encode("utf-8").decode("utf-8")
    assert vcard.read(text).cards[0].notes == note


# ------------------------------------------------------------------------ injection


@pytest.mark.parametrize("breaker", ["\r", "\n", "\r\n", "\x85", " ", "\x00", "\x1b", "\x0b"])
def test_a_line_break_or_control_in_a_value_adds_no_property(breaker):
    """The test postulo-dav#27 asks for, at the source."""
    evil = f"Alex{breaker}TEL:+1 555 0100{breaker}X-EVIL:yes"
    card = vcard.Card(
        name=evil,
        role=evil,
        email=f"a@b.example{breaker}URL:https://evil.example",
        notes=evil,
        links=(vcard.Link(f"https://x.example/{breaker}URL:https://evil.example"),),
        company=evil,
    )
    found = lines(vcard.card_to_text(card))
    names = [line.split(":", 1)[0].split(";", 1)[0] for line in found]
    assert sorted(set(names)) == sorted(
        {"BEGIN", "VERSION", "PRODID", "FN", "ORG", "TITLE", "EMAIL", "URL", "NOTE", "END", ""}
    )
    assert names.count("URL") == 1 and names.count("TEL") == 0
    assert not any(line.startswith("X-EVIL") for line in found)
    for control in "\x00\x1b\x0b\x85 ":
        assert control not in vcard.card_to_text(card)


# ---------------------------------------------------------------------------- reading


def test_a_card_written_is_a_card_read_back_losing_nothing_the_table_keeps():
    read = vcard.read(vcard.card_to_text(CARD))
    (card,) = read.cards
    assert read.skipped == 0
    assert card.name == "Alex Morgan"
    assert (card.company, card.department, card.role) == ("Acme", "Legal", "Counsel")
    assert card.email == "alex@acme.example"
    assert [(p.number, p.kind, p.primary) for p in card.phones] == [
        ("+351912345678", "mobile", True),
        ("+351211234567", "switchboard", False),
        ("+351211234568", "fax", False),
        ("+351211234569", "work", False),
        ("+351211234560", "home", False),
    ]
    assert card.addresses == (CARD.addresses[0],)
    assert [link.url for link in card.links] == ["https://acme.example/alex"]
    assert card.notes == "Met at a fair, likes tea."
    assert card.uid == CARD.uid
    assert card.dropped == ()


def test_a_company_card_is_read_as_one():
    (card,) = vcard.read("BEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:Acme\nORG:Acme\nEND:VCARD\n").cards
    assert card.is_org and card.name == "Acme" and card.company == "Acme"


def test_version_3_is_read_with_its_own_habits():
    text = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Morgan;Alex;;;\r\nFN:Alex Morgan\r\n"
        "ORG:Acme;Legal\r\nTEL;TYPE=WORK,VOICE,pref:+351 21 123 4567\r\n"
        "TEL;TYPE=CELL:+351 912 345 678\r\nEMAIL;TYPE=INTERNET:a@acme.example\r\n"
        "ADR;TYPE=HOME,POSTAL:;;1 Main St;Springfield;IL;62701;United States\r\n"
        "item1.URL:https://acme.example\r\nitem1.X-ABLabel:site\r\nBDAY:1985-04-12\r\n"
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRg==\r\nEND:VCARD\r\n"
    )
    (card,) = vcard.read(text).cards
    assert card.version == "3.0" and card.name == "Alex Morgan"
    # WORK,VOICE on a 3.0 card is how every address book writes a plain work number.
    assert [(p.kind, p.primary) for p in card.phones] == [("work", True), ("mobile", False)]
    assert card.addresses[0].country == "US" and card.addresses[0].kind == "home"
    assert card.links[0].url == "https://acme.example"
    assert card.dropped == ("X-ABLabel", "BDAY", "PHOTO") or set(card.dropped) == {
        "X-ABLABEL",
        "BDAY",
        "PHOTO",
    }


def test_the_name_falls_back_to_n_then_the_company():
    only_n = "BEGIN:VCARD\nVERSION:4.0\nN:Morgan;Alex;;;\nEND:VCARD\n"
    assert vcard.read(only_n).cards[0].name == "Alex Morgan"
    only_org = "BEGIN:VCARD\nVERSION:4.0\nORG:Acme\nEND:VCARD\n"
    assert vcard.read(only_org).cards[0].name == "Acme"


def test_what_is_not_kept_is_named():
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:A\nPHOTO:https://x.example/p.jpg\nLOGO:https://x.example/l\n"
        "KEY:https://x.example/k\nBDAY:19850412\nX-FOO:bar\nSOURCE:https://x.example/s\n"
        "EMAIL:a@x.example\nEMAIL:b@x.example\nEND:VCARD\n"
    )
    (card,) = vcard.read(text).cards
    assert card.dropped == ("PHOTO", "LOGO", "KEY", "BDAY", "X-FOO", "SOURCE", "EMAIL")
    assert card.email == "a@x.example"


def test_socialprofile_is_read_as_a_social_link():
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:A\nSOCIALPROFILE;SERVICE-TYPE=Mastodon:https://m.example/@a\n"
        "END:VCARD\n"
    )
    assert vcard.read(text).cards[0].links == (vcard.Link("https://m.example/@a", social=True),)


def test_a_version_2_1_file_is_refused_with_a_sentence():
    with pytest.raises(vcard.VCardRefused) as refused:
        vcard.read("BEGIN:VCARD\nVERSION:2.1\nFN:A\nEND:VCARD\n")
    assert "2.1" in str(refused.value) and "3.0" in str(refused.value)


def test_a_card_with_no_version_is_refused():
    with pytest.raises(vcard.VCardRefused):
        vcard.read("BEGIN:VCARD\nFN:A\nEND:VCARD\n")


def test_an_extended_rev_is_read():
    """postulo-dav#25: both the basic and the extended forms of an instant."""
    assert vcard.parse_instant("20261005T123000Z") == dt.datetime(
        2026, 10, 5, 12, 30, tzinfo=dt.UTC
    )
    extended = vcard.parse_instant("2026-10-05T12:30:00+01:00")
    assert extended == dt.datetime(2026, 10, 5, 11, 30, tzinfo=dt.UTC)
    assert vcard.parse_instant("yesterday") is None
    text = "BEGIN:VCARD\nVERSION:4.0\nFN:A\nREV:2026-10-05T12:30:00Z\nEND:VCARD\n"
    assert vcard.read(text).cards[0].revised == dt.datetime(2026, 10, 5, 12, 30, tzinfo=dt.UTC)


def test_a_card_survives_a_trip_through_the_session():
    (card,) = vcard.read(vcard.card_to_text(CARD)).cards
    assert vcard.Card.from_dict(card.to_dict()) == vcard.Card(**{**card.__dict__, "revised": None})
    with pytest.raises(ValueError):
        vcard.Card.from_dict({"name": "x"})


# ----------------------------------------------------------------------- the rows


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Acme", website="https://acme.example")


@pytest.fixture
def contact(user, company):
    department = Department.objects.create(owner=user, company=company, name="Legal")
    contact = Contact.objects.create(
        owner=user,
        company=company,
        department=department,
        name="Alex Morgan",
        role="Counsel",
        email="alex@acme.example",
        notes="private",
    )
    PhoneNumber.objects.create(
        owner=user, holder=contact, number="+351912345678", kind="mobile", is_primary=True
    )
    PostalAddress.objects.create(
        owner=user,
        holder=contact,
        kind="work",
        street="Rua do Exemplo 1",
        postcode="1000-100",
        municipality="Lisboa",
        country="PT",
        is_primary=True,
    )
    WebLink.objects.create(
        owner=user, holder=contact, kind="website", url="https://acme.example/alex", is_primary=True
    )
    MessagingHandle.objects.create(
        owner=user, holder=contact, service="xmpp", handle="alex@example.org", is_primary=True
    )
    return contact


@pytest.mark.django_db
def test_a_contact_becomes_a_card_without_its_notes_unless_asked(contact):
    text = vcard.card_to_text(vcards.card_for_contact(vcards.contacts_of(Contact.objects).get()))
    found = lines(text)
    assert "FN:Alex Morgan" in found and "ORG:Acme;Legal" in found
    assert "TEL;VALUE=uri;TYPE=cell;PREF=1:tel:+351912345678" in found
    assert "IMPP:xmpp:alex@example.org" in found
    assert not [line for line in found if line.startswith("NOTE")]
    fetched = vcards.contacts_of(Contact.objects).get()
    with_notes = lines(vcard.card_to_text(vcards.card_for_contact(fetched, notes=True)))
    assert "NOTE:private" in with_notes
    uid = next(line for line in found if line.startswith("UID:"))
    assert uid == "UID:" + vcard.uid_for(vcards.instance_name(), "contact", contact.pk)


@pytest.mark.django_db
def test_a_company_becomes_a_kind_org_card(company):
    found = lines(vcard.card_to_text(vcards.card_for_company(company)))
    assert "KIND:org" in found and "FN:Acme" in found
    assert "URL:https://acme.example" in found


@pytest.mark.django_db
def test_the_persons_own_card_has_n_and_only_what_the_default_cv_prints(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    profile = user.profile
    profile.headline = "Counsel"
    profile.pronouns = "they/them"
    profile.birth_date = "1985-04-12"
    profile.save()
    PostalAddress.objects.create(
        owner=user,
        holder=profile,
        street="Rua do Exemplo 1",
        municipality="Lisboa",
        postcode="1000-100",
        country="PT",
        is_primary=True,
    )
    found = lines(vcard.card_to_text(vcards.card_for_person(user)))
    assert "N:Morgan;Alex;;;" in found and "FN:Alex Morgan" in found
    assert "TITLE:Counsel" in found and "PRONOUNS:they/them" in found
    assert f"EMAIL;TYPE=work:{user.email}" in found
    adr = next(line for line in found if line.startswith("ADR"))
    assert "Rua do Exemplo" not in adr and "Lisboa" in adr
    assert not [line for line in found if line.startswith(("BDAY", "GENDER"))]


# ------------------------------------------------------------------------- the import


def _read(text: str):
    return vcard.read(text).cards


@pytest.mark.django_db
def test_the_review_shows_every_card_and_chooses_none(user, company):
    Contact.objects.create(owner=user, company=company, name="Already Here")
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:New Person\nORG:Acme;Legal\nUID:u1\nBDAY:19850412\n"
        "END:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nFN:New Person Again\nUID:u1\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nFN:Already Here\nORG:Acme\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:Acme\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:Globex\nEND:VCARD\n"
    )
    entries = vcards.review(user, _read(text))
    assert [e.outcome for e in entries] == ["add", "repeat", "present", "present", "add"]
    assert [e.can_choose for e in entries] == [True, False, False, False, True]
    assert entries[0].not_kept == ["BDAY"]
    assert "Acme" in entries[0].becomes and "Legal" in entries[0].becomes
    assert Contact.objects.count() == 1, "reviewing adds nothing"


@pytest.mark.django_db
def test_chosen_cards_become_contacts_under_the_company_named(user, company):
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:Sam Rivera\nORG:Acme;Legal\nTITLE:Lawyer\n"
        "EMAIL:sam@acme.example\nTEL;TYPE=cell;PREF=1:+351 912 345 679\n"
        "ADR;TYPE=work;CC=PT:;;Rua do Exemplo 1;Lisboa;;1000-100;Portugal\n"
        "URL:https://www.linkedin.com/in/sam-rivera\nURL:https://sam.example\n"
        "IMPP:xmpp:sam@example.org\nNOTE:from a fair\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nFN:Not Chosen\nEND:VCARD\n"
    )
    cards = _read(text)
    report = vcards.apply(user, cards, {0})
    assert (report.contacts, report.companies, report.problems) == (1, 0, [])
    contact = Contact.objects.get(owner=user, name="Sam Rivera")
    assert contact.company == company and contact.department.name == "Legal"
    assert (contact.role, contact.email, contact.notes) == (
        "Lawyer",
        "sam@acme.example",
        "from a fair",
    )
    (number,) = contact.phone_numbers.all()
    assert (number.number, number.kind, number.is_primary) == ("+351912345679", "mobile", True)
    (address,) = contact.postal_addresses.all()
    assert (address.country, address.postcode, address.kind) == ("PT", "1000-100", "work")
    kinds = {link.url: (link.kind, link.service) for link in contact.web_links.all()}
    assert kinds["https://www.linkedin.com/in/sam-rivera"] == ("social", "linkedin")
    assert kinds["https://sam.example"] == ("website", "")
    (handle,) = contact.messaging_handles.all()
    assert (handle.service, handle.handle) == ("xmpp", "sam@example.org")
    assert not Contact.objects.filter(name="Not Chosen").exists()


@pytest.mark.django_db
def test_a_company_card_becomes_a_company_and_a_company_that_exists_is_left_alone(user, company):
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:Globex\nURL:https://globex.example\nNOTE:n\n"
        "END:VCARD\nBEGIN:VCARD\nVERSION:4.0\nKIND:org\nFN:ACME\nURL:https://other.example\n"
        "END:VCARD\n"
    )
    report = vcards.apply(user, _read(text), {0, 1})
    assert report.companies == 1
    assert Company.objects.get(owner=user, name="Globex").website == "https://globex.example"
    company.refresh_from_db()
    assert company.website == "https://acme.example", "never overwritten"


@pytest.mark.django_db
def test_an_existing_contact_is_never_overwritten_nor_added_twice(user, company):
    existing = Contact.objects.create(owner=user, company=company, name="Sam Rivera", role="Old")
    text = "BEGIN:VCARD\nVERSION:4.0\nFN:Sam Rivera\nORG:Acme\nTITLE:New\nEND:VCARD\n"
    report = vcards.apply(user, _read(text), {0})
    assert report.contacts == 0
    existing.refresh_from_db()
    assert existing.role == "Old" and Contact.objects.count() == 1


@pytest.mark.django_db
def test_a_value_that_is_refused_is_reported_and_the_rest_of_the_card_still_imports(user):
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:Sam Rivera\nEMAIL:not an email\nTEL:+351 12\n"
        "ADR;CC=PT:;;Rua 1;Lisboa;;12;Portugal\nURL:javascript:alert(1)\nURL:https://sam.example\n"
        "END:VCARD\n"
    )
    report = vcards.apply(user, _read(text), {0})
    contact = Contact.objects.get(owner=user, name="Sam Rivera")
    assert report.contacts == 1 and contact.email == ""
    assert not contact.phone_numbers.exists(), "too short for its country"
    assert not contact.postal_addresses.exists(), "a postcode Portugal never uses"
    assert [link.url for link in contact.web_links.all()] == ["https://sam.example"]
    joined = " ".join(report.problems)
    assert "not an email" in joined and "javascript:" in joined and "+351 12" in joined
    assert len(report.problems) == 4


@pytest.mark.django_db
def test_a_number_somebody_else_holds_is_refused_in_the_collision_words(user, other_user):
    holder = Contact.objects.create(owner=other_user, name="Theirs")
    PhoneNumber.objects.create(owner=other_user, holder=holder, number="+351912345678")
    text = "BEGIN:VCARD\nVERSION:4.0\nFN:Sam\nTEL:+351 912 345 678\nEND:VCARD\n"
    report = vcards.apply(user, _read(text), {0})
    assert report.contacts == 1
    assert not PhoneNumber.objects.filter(owner=user).exists()
    assert any("already recorded" in problem for problem in report.problems)


@pytest.mark.django_db
def test_a_number_with_no_country_is_read_in_the_country_of_the_cards_address(user):
    text = (
        "BEGIN:VCARD\nVERSION:4.0\nFN:Sam\nTEL:912 345 678\n"
        "ADR;CC=PT:;;;Lisboa;;;Portugal\nEND:VCARD\n"
    )
    vcards.apply(user, _read(text), {0})
    assert PhoneNumber.objects.get(owner=user).number == "+351912345678"


@pytest.mark.django_db
def test_a_card_a_session_holds_is_read_back_and_nothing_else_is(user):
    read = vcard.read("BEGIN:VCARD\nVERSION:4.0\nFN:A\nEND:VCARD\n")
    held = vcards.hold(read)
    assert [c.name for c in vcards.held_cards(held)] == ["A"]
    assert vcards.held_cards({"cards": [{"nonsense": 1}]}) == []
    assert vcards.held_cards("not a dict") == []


# -------------------------------------------------------------------------- the pages


@pytest.mark.django_db
def test_the_downloads_are_vcard_files(client, user, company, contact):
    client.force_login(user)
    for name, args in (
        ("jobs:contact_vcard", [contact.pk]),
        ("jobs:company_vcard", [company.pk]),
        ("jobs:company_contacts_vcard", [company.pk]),
        ("jobs:contacts_vcard", []),
        ("jobs:own_vcard", []),
    ):
        response = client.get(reverse(name, args=args))
        assert response.status_code == 200, name
        assert response["Content-Type"] == "text/vcard; charset=utf-8"
        assert ".vcf" in response["Content-Disposition"]
        assert response["Cache-Control"].startswith("private")
        assert response.content.startswith(b"BEGIN:VCARD\r\nVERSION:4.0\r\n")
    private = client.get(reverse("jobs:contact_vcard", args=[contact.pk]) + "?notes=1")
    assert b"NOTE:private" in private.content
    assert b"private" not in client.get(reverse("jobs:contact_vcard", args=[contact.pk])).content


@pytest.mark.django_db
def test_somebody_elses_record_is_a_404(client, other_user, company, contact):
    client.force_login(other_user)
    for name, args in (
        ("jobs:contact_vcard", [contact.pk]),
        ("jobs:company_vcard", [company.pk]),
        ("jobs:company_contacts_vcard", [company.pk]),
    ):
        assert client.get(reverse(name, args=args)).status_code == 404, name
    assert b"Alex Morgan" not in client.get(reverse("jobs:contacts_vcard")).content


@pytest.mark.django_db
def test_the_import_page_reads_a_file_shows_it_unticked_and_adds_what_is_ticked(client, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(user)
    url = reverse("jobs:contact_vcards")
    assert client.get(url).status_code == 200
    data = (
        b"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Sam Rivera\r\nORG:Globex\r\nEND:VCARD\r\n"
        b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Kim Lee\r\nEND:VCARD\r\n"
    )
    posted = client.post(url, {"file": SimpleUploadedFile("c.vcf", data, "text/vcard")})
    assert posted.status_code == 302
    page = client.get(url)
    html = page.content.decode()
    assert "Sam Rivera" in html and "Kim Lee" in html
    assert html.count('name="chosen"') == 2 and " checked" not in html.split("<table")[1]
    nothing = client.post(url, {"action": "confirm"})
    assert nothing.status_code == 302 and not Contact.objects.exists()
    done = client.post(url, {"action": "confirm", "chosen": ["1"]})
    assert done.status_code == 302
    assert list(Contact.objects.values_list("name", flat=True)) == ["Kim Lee"]
    assert client.get(url).context["entries"] is None, "what was held is gone"


@pytest.mark.django_db
def test_a_file_that_is_refused_says_why_and_holds_nothing(client, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(user)
    url = reverse("jobs:contact_vcards")
    old = b"BEGIN:VCARD\r\nVERSION:2.1\r\nFN:A\r\nEND:VCARD\r\n"
    response = client.post(url, {"file": SimpleUploadedFile("c.vcf", old)}, follow=True)
    assert "2.1" in response.content.decode()
    assert response.context["entries"] is None
    assert ContentType.objects.exists()
