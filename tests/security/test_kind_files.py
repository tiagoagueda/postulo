"""A file of one kind of record is a stranger's file, whoever uploads it (#659).

The four pages read a file a person chose, and the file may have been written by anybody.
None of the eight addresses takes an argument that names a record, so there is no record to
ask for in somebody else's name. What there is instead is a document, and these are its
boundaries:

- nobody reaches an address without an account, and a forged request writes nothing;
- the file is bounded before it is parsed, and its rows before any is validated;
- another account's records are never in a file, and what one person is reading is in
  nobody else's session;
- a crafted file cannot name a record, give a row to somebody else, or have a number
  confirmed;
- markup arrives on the page as the text it is.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.core import kind_files
from postulo.core.models import PhoneNumber
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db

KINDS = tuple(kind_files.FORMATS)
MARKUP = "<script>alert(1)</script>"


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield
    cache.clear()


def a_file(kind: str, *rows: dict) -> bytes:
    return json.dumps({"postulo": {f"{kind}_format": 1}, kind: list(rows)}).encode()


def upload(data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile("theirs.json", data, content_type="application/json")


def theirs(owner) -> Application:
    company = Company.objects.create(owner=owner, name="Their company")
    posting = JobPosting.objects.create(owner=owner, company=company, title="Their role")
    Contact.objects.create(owner=owner, company=company, name="Their contact", email="t@x.org")
    return Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)


# ------------------------------------------------------------------- who may ask at all


@pytest.mark.parametrize("kind", KINDS)
def test_no_address_answers_somebody_who_has_not_signed_in(client, user, kind):
    theirs(user)
    for response in (
        client.get(reverse(f"core:file_{kind}")),
        client.get(reverse(f"core:file_{kind}_download")),
        client.post(reverse(f"core:file_{kind}"), {"file": upload(a_file(kind, {"name": "X"}))}),
        client.post(reverse(f"core:file_{kind}"), {"action": "confirm"}),
    ):
        assert response.status_code == 302
        assert "login" in response["Location"]
        assert b"Their company" not in response.content


@pytest.mark.parametrize("kind", KINDS)
def test_a_forged_request_reads_nothing_and_adds_nothing(user, kind):
    honest = Client()
    honest.force_login(user)
    honest.post(reverse(f"core:file_{kind}"), {"file": upload(a_file(kind, {"name": "Real"}))})

    forged = Client(enforce_csrf_checks=True)
    forged.cookies = honest.cookies
    read = forged.post(reverse(f"core:file_{kind}"), {"file": upload(a_file(kind, {"name": "B"}))})
    added = forged.post(reverse(f"core:file_{kind}"), {"action": "confirm"})

    assert read.status_code == 403 and added.status_code == 403
    assert not Company.objects.for_user(user).exists()


@pytest.mark.parametrize("kind", KINDS)
def test_the_download_only_reads(client, user, kind):
    client.force_login(user)
    address = reverse(f"core:file_{kind}_download")
    assert client.post(address).status_code == 405
    assert client.delete(address).status_code == 405


@pytest.mark.parametrize("kind", KINDS)
def test_another_accounts_records_are_never_in_a_file(client, user, other_user, kind):
    theirs(other_user)
    client.force_login(user)
    response = client.get(
        reverse(f"core:file_{kind}_download"),
        {"user": other_user.pk, "owner": other_user.pk, "pk": 1, "id": 1},
    )
    assert json.loads(response.content)[kind] == []
    assert "Their" not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Disposition"].startswith("attachment;")


def test_the_cards_count_only_the_askers_own(client, user, other_user):
    theirs(other_user)
    client.force_login(user)
    assert kind_files.counts(user) == dict.fromkeys(KINDS, 0)
    page = client.get(reverse("core:export")).content.decode()
    assert "Their company" not in page


@pytest.mark.parametrize("kind", KINDS)
def test_what_one_person_is_reading_is_in_nobody_elses_session(user, other_user, kind):
    one, another = Client(), Client()
    one.force_login(user)
    another.force_login(other_user)
    one.post(reverse(f"core:file_{kind}"), {"file": upload(a_file(kind, {"name": "Initech"}))})

    page = another.get(reverse(f"core:file_{kind}"))
    confirmed = another.post(reverse(f"core:file_{kind}"), {"action": "confirm"}, follow=True)

    assert page.context["review"] is None
    assert b"Initech" not in page.content
    assert b"nothing waiting to be imported" in confirmed.content
    assert not Company.objects.exists()


def test_a_file_read_for_one_kind_is_not_confirmed_as_another(client, user):
    client.force_login(user)
    client.post(
        reverse("core:file_companies"), {"file": upload(a_file("companies", {"name": "Initech"}))}
    )
    confirmed = client.post(reverse("core:file_contacts"), {"action": "confirm"}, follow=True)
    assert b"nothing waiting to be imported" in confirmed.content
    assert not Company.objects.for_user(user).exists()


# ----------------------------------------------------------------- how much of it is read


def reached(*args, **kwargs):
    raise AssertionError("a file over the bound was read")


@pytest.mark.parametrize("kind", KINDS)
def test_a_file_over_the_bound_is_never_read(client, user, monkeypatch, kind):
    monkeypatch.setattr(kind_files, "read", reached)
    client.force_login(user)
    big = b" " * (kind_files.MAX_BYTES + 1)
    response = client.post(reverse(f"core:file_{kind}"), {"file": upload(big)}, follow=True)
    assert b"larger than 2 MB" in response.content


def test_the_reader_holds_itself_to_the_bound_as_well():
    with pytest.raises(kind_files.Refused):
        kind_files.read("companies", b" " * (kind_files.MAX_BYTES + 1))


def test_the_rows_are_cut_before_any_is_validated(monkeypatch, user):
    seen = []
    real = kind_files._ASSESS["companies"]
    monkeypatch.setitem(
        kind_files._ASSESS, "companies", lambda run, row: (seen.append(1), real(run, row))[1]
    )
    rows = [{"name": f"Company {n}"} for n in range(kind_files.MAX_ROWS + 50)]
    held = kind_files.read("companies", a_file("companies", *rows))
    assert len(held["rows"]) == kind_files.MAX_ROWS and held["cut"] == 50
    review = kind_files.plan(user, held)
    assert len(seen) == kind_files.MAX_ROWS
    assert any(str(kind_files.MAX_ROWS) in note for note in review["notes"])
    assert kind_files.apply(user, held).added == kind_files.MAX_ROWS


@pytest.mark.parametrize(
    "data",
    [
        b"[" * 100000 + b"]" * 100000,
        b'{"postulo": {"companies_format": NaN}}',
        b'{"postulo": {"companies_format": Infinity}, "companies": []}',
        b"\xff\xfe not text",
        b'{"postulo": {"companies_format": 1}, "companies": {"name": "not a list"}}',
    ],
    ids=["deep", "nan", "infinity", "not-text", "not-a-list"],
)
def test_a_file_made_to_be_expensive_is_refused_rather_than_read(data):
    with pytest.raises(kind_files.Refused):
        kind_files.read("companies", data)


def test_what_is_kept_of_a_file_is_what_is_read_and_nothing_else(user):
    row = {"name": "Initech", "extra": "x" * 100000, "identifiers": [{"scheme": "wikidata"}] * 1000}
    held = kind_files.read("companies", a_file("companies", row))
    kept = held["rows"][0]
    assert set(kept) == {"name", "identifiers"}
    assert len(kept["identifiers"]) <= kind_files.MAX_NESTED
    assert all(set(item) <= set(kind_files.IDENTIFIER_FIELDS) for item in kept["identifiers"])
    deep = {"company": "A", "listing": {"title": "T"}, "events": [{"summary": "s"}] * 10000}
    held = kind_files.read("applications", a_file("applications", deep))
    assert len(held["rows"][0]["events"]) == kind_files.MAX_NESTED


def test_a_contact_may_not_carry_a_thousand_ways_of_being_reached():
    row = {"name": "Ada", "phone_numbers": [{"number": "+351912345678"}] * 1000}
    held = kind_files.read("contacts", a_file("contacts", row))
    assert len(held["rows"][0]["phone_numbers"]) == kind_files.MAX_DETAILS


# ---------------------------------------------------------------- who a row belongs to


@pytest.mark.parametrize("kind", KINDS)
def test_a_crafted_file_cannot_name_a_record_or_give_a_row_away(user, other_user, kind):
    victim = theirs(other_user)
    named = {
        "owner": other_user.pk,
        "owner_id": other_user.pk,
        "pk": victim.pk,
        "id": victim.pk,
        "posting_id": victim.posting_id,
        "company_id": victim.posting.company_id,
        "contact_id": 1,
        "referred_by_id": 1,
    }
    row = {
        "listings": {"company": "C", "title": "T"},
        "applications": {"company": "C", "listing": {"title": "T", **named}},
        "companies": {"name": "C"},
        "contacts": {"name": "C"},
    }[kind]
    row.update(named)
    held = kind_files.read(kind, a_file(kind, row))
    kind_files.apply(user, held)
    for model in (Company, Contact, JobPosting, Application):
        assert not model.objects.filter(owner=user).filter(pk=victim.pk).exists() or (
            model.objects.get(pk=victim.pk).owner_id == other_user.pk
        )
        assert model.objects.for_user(other_user).count() == 1
    assert Application.objects.for_user(other_user).get().posting_id == victim.posting_id
    assert not Application.objects.for_user(user).filter(posting__owner=other_user).exists()


def test_somebody_elses_record_is_not_a_match_for_mine(user, other_user):
    theirs(other_user)
    for kind, row in (
        ("companies", {"name": "Their company"}),
        ("contacts", {"name": "x", "email": "t@x.org"}),
        ("listings", {"company": "Their company", "title": "Their role"}),
    ):
        held = kind_files.read(kind, a_file(kind, row))
        outcomes = [
            r["outcome"] for s in kind_files.plan(user, held)["sections"] for r in s["rows"]
        ]
        assert outcomes == ["add"], kind


# ----------------------------------------------------------------- what a value may be


@pytest.mark.parametrize(
    "address", ["javascript:alert(document.cookie)", "data:text/html,<b>x</b>", "ftp://x"]
)
def test_an_address_that_is_not_one_is_refused_wherever_it_is_put(user, address):
    for kind, row in (
        ("companies", {"name": "A", "website": address}),
        ("companies", {"name": "A", "careers_url": address}),
        ("listings", {"company": "A", "title": "T", "url": address}),
        ("applications", {"company": "A", "listing": {"title": "T", "url": address}}),
    ):
        held = kind_files.read(kind, a_file(kind, row))
        review = kind_files.plan(user, held)
        assert [r["outcome"] for s in review["sections"] for r in s["rows"]] == ["refused"], row
        assert kind_files.apply(user, held).added == 0
    assert not Company.objects.for_user(user).exists()


def test_markup_in_a_file_reaches_the_page_as_the_text_it_is(client, user):
    client.force_login(user)
    client.post(
        reverse("core:file_companies"),
        {"file": upload(a_file("companies", {"name": MARKUP, "location": MARKUP}))},
    )
    page = client.get(reverse("core:file_companies")).content.decode()
    assert MARKUP not in page
    assert "&lt;script&gt;" in page


def test_the_status_and_the_dates_of_an_application_are_the_forms_to_judge(user):
    row = {
        "company": "A",
        "listing": {"title": "T"},
        "status": "tenured",
        "applied_at": "yesterday",
    }
    held = kind_files.read("applications", a_file("applications", row))
    review = kind_files.plan(user, held)
    assert [r["outcome"] for s in review["sections"] for r in s["rows"]] == ["refused"]
    assert not Application.objects.for_user(user).exists()


def test_a_date_of_the_readers_language_is_not_a_date_of_the_file():
    row = {"company": "A", "title": "T", "posted_at": "03/04/2026"}
    held = kind_files.read("listings", a_file("listings", row))
    from django.contrib.auth import get_user_model

    plan = kind_files.plan(get_user_model()(), held)
    assert [r["outcome"] for s in plan["sections"] for r in s["rows"]] == ["refused"]


# ------------------------------------------------------------------- numbers and people


def test_a_file_cannot_say_that_a_number_was_confirmed_or_is_a_way_back_in(user):
    row = {
        "name": "Ada",
        "phone_numbers": [
            {
                "number": "+351912345678",
                "kind": "mobile",
                "verified_at": "2020-01-01T00:00:00Z",
                "is_recovery": True,
                "is_primary": True,
            }
        ],
    }
    held = kind_files.read("contacts", a_file("contacts", row))
    assert set(held["rows"][0]["phone_numbers"][0]) <= set(
        kind_files.CONTACT_DETAILS["phone_numbers"]
    )
    kind_files.apply(user, held)
    number = PhoneNumber.objects.get(owner=user)
    assert number.verified_at is None and not number.is_recovery


def test_a_number_another_account_holds_is_left_off_and_says_nothing_of_whose(user, other_user):
    from django.contrib.contenttypes.models import ContentType

    friend = Contact.objects.create(owner=other_user, name="Theirs")
    PhoneNumber.objects.create(owner=other_user, holder=friend, number="+351912345678")
    row = {"name": "Ada", "phone_numbers": [{"number": "+351912345678"}]}
    held = kind_files.read("contacts", a_file("contacts", row))

    review = kind_files.plan(user, held)
    kind_files.apply(user, held)

    assert not PhoneNumber.objects.filter(owner=user).exists()
    assert "351912345678" not in json.dumps(review, default=str)
    assert ContentType.objects.get_for_model(Contact)


def test_the_number_allowance_is_spent_like_the_forms_and_then_every_number_is_left_off(
    user, other_user
):
    """A file is a way of asking about a great many numbers at once (#142): it is asked
    through the form's own allowance, so after it runs out nothing is told apart."""
    from postulo.core import phone_numbers, throttle

    friend = Contact.objects.create(owner=other_user, name="Theirs")
    PhoneNumber.objects.create(owner=other_user, holder=friend, number="+351912345678")
    rows = [{"name": f"P{n}", "phone_numbers": [{"number": "+351912345678"}]} for n in range(40)]
    held = kind_files.read("contacts", a_file("contacts", *rows))
    kind_files.apply(user, held)
    with pytest.raises(throttle.TooOften):
        phone_numbers.ensure_allowance(user)
    assert Contact.objects.for_user(user).count() == 40
    assert not PhoneNumber.objects.filter(owner=user).exists()


@pytest.mark.parametrize("bad", [5, True])
def test_a_contacts_ways_of_being_reached_that_are_not_text_do_not_break_the_import(user, bad):
    """A number or a flag where text belongs is held as nothing: it is not a 500."""
    row = {
        "name": "Ada",
        "phone_numbers": [{"number": bad, "kind": bad, "label": bad, "is_primary": bad}],
        "postal_addresses": [{"street": bad, "municipality": bad}],
        "web_links": [{"url": bad, "label": bad}],
        "messaging_handles": [{"service": bad, "handle": bad}],
    }
    data = json.dumps({"postulo": {"contacts_format": 1}, "contacts": [row]}).encode()
    held = kind_files.read("contacts", data, "x.json")
    assert kind_files.apply(user, held).added == 1
    assert Contact.objects.for_user(user).filter(name="Ada").exists()
    assert not PhoneNumber.objects.filter(owner=user).exists()
