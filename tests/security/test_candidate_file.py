"""A candidate file is a stranger's file, whoever it is that uploads it (#181).

The page reads a file a person chose, and the file may have been written by anybody: by
another Postulo, by a script, by hand, or by somebody who would like this instance to do
something it should not. Neither of its two addresses takes an argument, so there is no
record to ask for in somebody else's name and the sweep next door has nothing to sweep.
What there is instead is a document, and these are its boundaries:

- nobody reaches either address without an account, and nothing is changed by a request
  that was forged;
- the file is bounded before it is parsed, and what is kept of it is bounded too;
- nothing in it decides whose a row is, and no id in it reaches a record;
- an address that is a script is refused wherever in the file it is put, and markup
  arrives on the page as the text it is;
- what a file is told about a telephone number is what the form would have told it, and
  no more often.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier
from postulo.core import export
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.resume import candidate
from postulo.resume.models import (
    Certification,
    Experience,
    LanguageSkill,
    Link,
    Project,
    Publication,
    Skill,
    SkillGroup,
    Translation,
)

pytestmark = pytest.mark.django_db

PAGE = "resume:candidate_file"
DOWNLOAD = "resume:candidate_download"

SCRIPT = "javascript:alert(document.cookie)"
MARKUP = "<script>alert(1)</script>"


@pytest.fixture(autouse=True)
def _empty_cache():
    """A counter left by one test must not spend another's allowance."""
    cache.clear()
    yield
    cache.clear()


def a_file(account=None, resume=None, **more) -> bytes:
    document = {
        "postulo": {"candidate_format": export.CANDIDATE_FORMAT, "version": "0.5.0"},
        **more,
    }
    if account is not None:
        document["account"] = account
    if resume is not None:
        document["resume"] = resume
    return json.dumps(document).encode()


def a_role(**changes) -> dict:
    return {
        "id": 1,
        "organisation": "Initech",
        "role": "Developer",
        "start_date": "2017-01-09",
        **changes,
    }


def upload(data: bytes):
    return SimpleUploadedFile("postulo-candidate.json", data, content_type="application/json")


def outcomes(plan: candidate.Plan, key: str) -> list[str]:
    return [row.outcome for section in plan.sections if section.key == key for row in section.rows]


def notes(plan: candidate.Plan, key: str) -> list[str]:
    return [
        " ".join(row.notes)
        for section in plan.sections
        if section.key == key
        for row in section.rows
    ]


def theirs(owner) -> Experience:
    return Experience.objects.create(
        owner=owner,
        organisation="Their employer",
        role="Their role",
        start_date=dt.date(2020, 1, 1),
    )


# ------------------------------------------------------------------- who may ask at all


def test_neither_address_answers_somebody_who_has_not_signed_in(client, user):
    theirs(user)

    for response in (
        client.get(reverse(PAGE)),
        client.get(reverse(DOWNLOAD)),
        client.post(reverse(PAGE), {"file": upload(a_file(resume={"experience": [a_role()]}))}),
        client.post(reverse(PAGE), {"action": "confirm"}),
    ):
        assert response.status_code == 302
        assert "login" in response["Location"]
        assert b"Their employer" not in response.content
    assert "candidate_file" not in client.session


def test_a_forged_request_reads_nothing_and_adds_nothing(user):
    """Both halves are a POST, and a POST without the token is refused before the view."""
    honest = Client()
    honest.force_login(user)
    honest.post(reverse(PAGE), {"file": upload(a_file(resume={"experience": [a_role()]}))})
    assert "candidate_file" in honest.session

    forged = Client(enforce_csrf_checks=True)
    forged.cookies = honest.cookies  # the person's own session, as a forged request rides it

    read = forged.post(
        reverse(PAGE), {"file": upload(a_file(resume={"experience": [a_role(role="Other")]}))}
    )
    added = forged.post(reverse(PAGE), {"action": "confirm"})

    assert read.status_code == 403 and added.status_code == 403
    assert not Experience.objects.for_user(user).exists()
    assert honest.session["candidate_file"]["experience"][0]["role"] == "Developer"


def test_the_download_only_reads(client, user):
    client.force_login(user)

    assert client.post(reverse(DOWNLOAD)).status_code == 405
    assert client.delete(reverse(DOWNLOAD)).status_code == 405


def test_the_download_is_the_askers_own_whatever_is_asked_for(client, user, other_user):
    """There is no argument to change, and one that is added is not read."""
    theirs(other_user)
    mine = Experience.objects.create(
        owner=user, organisation="My employer", role="My role", start_date=dt.date(2021, 1, 1)
    )
    client.force_login(user)

    response = client.get(
        reverse(DOWNLOAD),
        {"user": other_user.pk, "owner": other_user.pk, "pk": other_user.pk, "id": 1},
    )

    document = json.loads(response.content)
    assert [row["organisation"] for row in document["resume"]["experience"]] == [mine.organisation]
    assert "Their employer" not in response.content.decode()
    assert "no-store" in response["Cache-Control"], "and nothing between keeps a copy"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Content-Type"] == "application/json"


def test_what_one_person_is_looking_at_is_in_nobody_elses_session(user, other_user):
    one, another = Client(), Client()
    one.force_login(user)
    another.force_login(other_user)
    one.post(reverse(PAGE), {"file": upload(a_file(resume={"experience": [a_role()]}))})

    page = another.get(reverse(PAGE))
    confirmed = another.post(reverse(PAGE), {"action": "confirm"}, follow=True)

    assert page.context["review"] is None
    assert b"Initech" not in page.content
    assert b"nothing waiting to be imported" in confirmed.content
    assert not Experience.objects.exists()


# ----------------------------------------------------------------- how much of it is read


def reached(*args, **kwargs):
    raise AssertionError("a file over the bound was read")


def test_a_file_over_the_bound_is_never_read(client, user, monkeypatch):
    """Refused on its size, which the upload says before a byte of it is looked at."""
    monkeypatch.setattr(candidate, "read", reached)
    client.force_login(user)
    too_much = b'{"postulo": {"candidate_format": 1}, "x": "' + b"x" * candidate.MAX_BYTES + b'"}'

    response = client.post(reverse(PAGE), {"file": upload(too_much)}, follow=True)

    assert "larger than 1 MB" in response.content.decode()
    assert "candidate_file" not in client.session


def test_the_reader_holds_itself_to_the_bound_as_well(monkeypatch):
    """Whatever calls it: the page asks first, and a command that reads a file would not.
    The parser is never handed more than the bound, by either road."""
    from types import SimpleNamespace

    # The name as the module knows it, and not the library's own function: sessions and
    # messages are written with that one.
    monkeypatch.setattr(candidate, "json", SimpleNamespace(loads=reached))

    with pytest.raises(candidate.Refused, match="larger than"):
        candidate.read(b"{" + b" " * candidate.MAX_BYTES)
    # And it is the parser that was stood in for: a file within the bound reaches it.
    with pytest.raises(AssertionError, match="over the bound"):
        candidate.read(b"{}")


@pytest.mark.parametrize(
    "data",
    [
        ("[" * 200_000).encode(),
        ('{"postulo": {"candidate_format": 1}, "resume": ' + "[" * 200_000).encode(),
        b'{"postulo": {"candidate_format": ' + b"9" * 5000 + b"}}",
        b'{"postulo": {"candidate_format": 1, "weight": Infinity}}',
        b'{"postulo": {"candidate_format": 1e400}}',
        b"\xff\xfe{\x00}\x00",
    ],
    ids=[
        "nothing but depth",
        "depth inside a block",
        "a number of five thousand digits",
        "a number that is not one",
        "a number past the end of numbers",
        "another encoding",
    ],
)
def test_a_file_made_to_be_expensive_is_refused_rather_than_read(data):
    with pytest.raises(candidate.Refused):
        candidate.read(data)


def test_what_is_kept_of_a_file_is_what_is_read_and_nothing_else(client, user):
    """Most of a megabyte of something this page does not read is not put in a session."""
    ballast = "x" * (candidate.MAX_BYTES // 3 - 4096)
    data = a_file(
        resume={"experience": [a_role(ballast=ballast, role={"nested": [ballast]})]},
        ballast=[ballast],
    )
    assert candidate.MAX_BYTES * 0.9 < len(data) < candidate.MAX_BYTES
    client.force_login(user)

    client.post(reverse(PAGE), {"file": upload(data)})

    assert len(json.dumps(client.session["candidate_file"])) < 1024


def test_a_block_is_read_so_far_and_no_further():
    rows = [{"kind": "mobile", "number": f"+3519{n:08d}"} for n in range(200)]
    roles = [a_role(id=n, organisation=f"Employer {n}") for n in range(candidate.MAX_ROWS * 2)]

    held = candidate.read(
        a_file(account={"profile": {"phone_numbers": rows}}, resume={"experience": roles})
    )

    assert len(held["phone_numbers"]) == candidate.MAX_CONTACT_ROWS
    assert len(held["experience"]) == candidate.MAX_ROWS
    assert held["cut"] == {"phone_numbers": 200, "experience": candidate.MAX_ROWS * 2}


# --------------------------------------------------------------------- whose a row is


def test_whose_a_row_is_is_never_the_files_to_say(user, other_user):
    """Every name a row's owner goes by, pointed at somebody else. None of them is read."""
    somebody_else = {
        "owner": other_user.pk,
        "owner_id": other_user.pk,
        "user": other_user.pk,
        "user_id": other_user.pk,
        "profile": other_user.profile.pk,
        "profile_id": other_user.profile.pk,
        "object_id": other_user.profile.pk,
        "content_type": 1,
        "content_type_id": 1,
        "pk": 1,
    }
    data = a_file(
        account={
            "user": other_user.pk,
            "username": other_user.username,
            "email": other_user.email,
            "profile": {
                "user_id": other_user.pk,
                "phone_numbers": [{"number": "+351912345678", **somebody_else}],
                "postal_addresses": [{"street": "1 Example Street", **somebody_else}],
                "web_links": [{"kind": "website", "url": "https://example.org", **somebody_else}],
            },
            "identifiers": [{"scheme": "wikidata", "value": "Q42", **somebody_else}],
        },
        resume={
            "experience": [a_role(**somebody_else)],
            "skill_groups": [{"id": 1, "name": "Languages", **somebody_else}],
            "skills": [{"id": 1, "name": "Python", "group_id": 1, **somebody_else}],
            "translations": [
                {
                    "section": "experience",
                    "ref": 1,
                    "language": "fr-FR",
                    "field": "role",
                    "text": "Développeur",
                    **somebody_else,
                }
            ],
        },
    )
    before = (other_user.username, other_user.email)

    report = candidate.apply(user, candidate.read(data))

    assert report.total == 8
    for model in (PhoneNumber, PostalAddress, WebLink, Experience, SkillGroup, Skill, Translation):
        assert model.objects.for_user(user).count() == 1, model.__name__
        assert not model.objects.for_user(other_user).exists(), model.__name__
    for model in (PhoneNumber, PostalAddress, WebLink):
        assert model.objects.get().holder == user.profile
    assert PersonIdentifier.objects.get().profile == user.profile
    other_user.refresh_from_db()
    user.refresh_from_db()
    assert (other_user.username, other_user.email) == before
    assert user.username != other_user.username and user.email != other_user.email


def test_an_id_in_the_file_reaches_nobodys_record(user, other_user):
    """The numbers the other person's rows have, written into a file as its own."""
    role = theirs(other_user)
    group = SkillGroup.objects.create(owner=other_user, name="Their group")
    data = a_file(
        resume={
            "skills": [{"id": 1, "name": "Python", "group_id": group.pk}],
            "translations": [
                {
                    "section": "experience",
                    "ref": role.pk,
                    "language": "fr-FR",
                    "field": "role",
                    "text": "Le leur",
                }
            ],
        }
    )

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "skills") == [candidate.REFUSED]
    assert outcomes(plan, "translations") == [candidate.REFUSED]
    assert "not in the file" in notes(plan, "skills")[0]
    assert "not in the file" in notes(plan, "translations")[0]
    assert not Translation.objects.exists() and not Skill.objects.exists()
    assert not group.skills.exists()
    role.refresh_from_db()
    assert role.role == "Their role"


def test_somebody_elses_entry_is_not_a_match_for_mine(user, other_user):
    """What is already there is asked of the account importing, and of nobody else's."""
    theirs(other_user)
    data = a_file(
        resume={
            "experience": [
                {
                    "organisation": "Their employer",
                    "role": "Their role",
                    "start_date": "2020-01-01",
                }
            ]
        }
    )

    plan = candidate.plan(user, candidate.read(data))

    assert outcomes(plan, "experience") == [candidate.ADD]


# ---------------------------------------------------------- what an address may be


@pytest.mark.parametrize("address", [SCRIPT, "data:text/html,<script>alert(1)</script>", "//x"])
def test_an_address_that_is_not_one_is_refused_wherever_it_is_put(user, address):
    """Each of these is drawn as a link somewhere: on the career page, on a CV."""
    data = a_file(
        account={"profile": {"web_links": [{"kind": "website", "url": address}]}},
        resume={
            "projects": [{"name": "A project", "url": address}],
            "links": [{"title": "A link", "url": address, "kind": "portfolio"}],
            "certifications": [{"name": "A certificate", "credential_url": address}],
        },
    )

    plan = candidate.plan(user, candidate.read(data))
    report = candidate.apply(user, candidate.read(data))

    assert {row.outcome for row in plan.rows()} == {candidate.REFUSED}
    assert report.total == 0
    for model in (WebLink, Project, Link, Certification):
        assert not model.objects.exists(), model.__name__


def test_markup_in_a_file_reaches_the_page_as_the_text_it_is(client, user):
    data = a_file(
        account={
            "first_name": MARKUP,
            "profile": {"phone_numbers": [{"kind": "other", "label": MARKUP, "number": MARKUP}]},
        },
        resume={
            "experience": [a_role(role=MARKUP, organisation=MARKUP, start_date=MARKUP)],
            "translations": [
                {
                    "section": MARKUP,
                    "ref": 1,
                    "language": MARKUP,
                    "field": MARKUP,
                    "text": MARKUP,
                }
            ],
        },
        **{MARKUP: MARKUP},
    )
    document = json.loads(data)
    document["postulo"]["version"] = MARKUP
    document["postulo"]["exported_at"] = "2026-09-28T10:00:00+00:00"
    client.force_login(user)

    response = client.post(
        reverse(PAGE), {"file": upload(json.dumps(document).encode())}, follow=True
    )

    body = response.content.decode()
    assert response.context["review"] is not None
    assert MARKUP not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


# ------------------------------------------------------- what a number may be told


def held_by_somebody_else(other_user, *numbers: str) -> None:
    for index, number in enumerate(numbers):
        PhoneNumber.objects.create(
            owner=other_user, holder=other_user.profile, number=number, is_primary=not index
        )


def numbers_in_a_file(*numbers: str) -> bytes:
    rows = [{"kind": "mobile", "number": number} for number in numbers]
    return a_file(account={"profile": {"phone_numbers": rows}})


def test_a_number_somebody_else_holds_is_refused_in_the_forms_own_words(user, other_user):
    """Numbers are unique across the instance, and saying so is the disclosure #90 decided
    on. A file is told what the form would have told whoever typed the number."""
    held_by_somebody_else(other_user, "+351912345678")
    data = numbers_in_a_file("+351 912 345 678", "+351912345000")

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "phone_numbers") == [candidate.REFUSED, candidate.ADD]
    assert "already recorded on this instance" in notes(plan, "phone_numbers")[0]
    assert [row.number for row in user.profile.phone_numbers.all()] == ["+351912345000"]
    assert other_user.profile.phone_numbers.get().number == "+351912345678"


@override_settings(POSTULO_NUMBER_RATE="2/h")
def test_a_file_cannot_ask_about_more_numbers_than_the_form_could(user, other_user):
    """Twenty honest answers is a person; a file of them every minute is a list of which
    numbers have accounts here (#142). Once the allowance is spent **no** number is
    answered for: one that could have been added is refused in the same words as one that
    could not, because telling the two apart is the answer."""
    held_by_somebody_else(other_user, "+351912345001", "+351912345002", "+351912345003")
    data = numbers_in_a_file(
        "+351912345001", "+351912345002", "+351912345003", "+351912345999", "+351912345004"
    )

    plan = candidate.plan(user, candidate.read(data))

    told = notes(plan, "phone_numbers")
    assert outcomes(plan, "phone_numbers") == [candidate.REFUSED] * 5
    assert "already recorded on this instance" in told[0]
    assert "already recorded on this instance" in told[1]
    assert told[2] == told[3] == told[4], "held by somebody, free, and free: one sentence"
    assert "available again" in told[2] and "somebody else" not in told[2]
    assert plan.silenced == told[2]


@override_settings(POSTULO_NUMBER_RATE="2/h")
def test_the_button_is_bounded_with_the_page(client, user, other_user):
    """Adding without looking would otherwise be the way round: what was added says which
    numbers were free. The button spends the same allowance, and says when it ran out."""
    held_by_somebody_else(other_user, "+351912345001")
    client.force_login(user)
    data = numbers_in_a_file("+351912345001", "+351912345999")

    client.post(reverse(PAGE), {"file": upload(data)})
    looked = client.get(reverse(PAGE)).context["review"]
    assert outcomes(looked, "phone_numbers") == [candidate.REFUSED, candidate.ADD]
    client.get(reverse(PAGE))
    response = client.post(reverse(PAGE), {"action": "confirm"}, follow=True)

    body = response.content.decode()
    assert not user.profile.phone_numbers.exists()
    assert "Some telephone numbers were not added." in body
    assert "available again" in body


@override_settings(POSTULO_NUMBER_RATE="1/h")
def test_somebodys_own_numbers_cost_them_nothing(user):
    """Only the informative answer is charged for, so a person putting their own record
    back never meets the limit however many numbers it holds."""
    for number in ("+351912345001", "+351912345002", "+351912345003"):
        PhoneNumber.objects.create(owner=user, holder=user.profile, number=number)
    data = numbers_in_a_file(
        "+351912345001", "+351912345002", "+351912345003", "+351912345004", "+351912345005"
    )

    for _again in range(3):
        plan = candidate.plan(user, candidate.read(data))

    assert outcomes(plan, "phone_numbers") == [candidate.PRESENT] * 3 + [candidate.ADD] * 2
    assert not plan.silenced


def test_a_file_cannot_say_that_a_number_was_confirmed(user):
    """Nor that it is the way back into the account: an archive is a claim, and a claim of
    verification made somewhere else is one this instance never checked (#142, #144)."""
    data = a_file(
        account={
            "profile": {
                "phone_numbers": [
                    {
                        "kind": "mobile",
                        "number": "+351912345678",
                        "is_primary": True,
                        "verified_at": "2026-09-01T10:00:00+00:00",
                        "is_recovery": True,
                        "normalised": "+33612345678",
                    }
                ]
            }
        }
    )

    held = candidate.read(data)
    candidate.apply(user, held)

    assert set(held["phone_numbers"][0]) == {"kind", "number", "is_primary"}
    row = user.profile.phone_numbers.get()
    assert row.verified_at is None and not row.is_recovery
    assert row.normalised == "+351912345678", "worked out from the number, not read"


def test_what_is_not_unique_across_the_instance_says_nothing_about_anybody(user, other_user):
    """An address, a link and an identifier may be two people's, so whether one can be
    added is not a question about anybody else, and the answer says nothing of them."""
    PostalAddress.objects.create(
        owner=other_user, holder=other_user.profile, street="1 Example Street", is_primary=True
    )
    WebLink.objects.create(
        owner=other_user,
        holder=other_user.profile,
        kind="website",
        url="https://example.org",
        is_primary=True,
    )
    PersonIdentifier.objects.create(profile=other_user.profile, scheme="wikidata", value="Q42")
    data = a_file(
        account={
            "profile": {
                "postal_addresses": [{"street": "1 Example Street"}],
                "web_links": [{"kind": "website", "url": "https://example.org"}],
            },
            "identifiers": [{"scheme": "wikidata", "value": "Q42"}],
        }
    )

    plan = candidate.plan(user, candidate.read(data))

    assert [row.outcome for row in plan.rows()] == [candidate.ADD] * 3
    assert not any(row.notes for row in plan.rows())


@pytest.mark.parametrize("bad", ["1987-02-30", "3000", "1899", "soon", 1987, ["1987"], "1987\x00"])
def test_a_malformed_date_of_birth_is_a_refused_row_and_nothing_is_stored(user, bad):
    """A date of birth is the most identifying thing a file can carry, so it is read through
    the column's own rule and a failure is a row on the review page (#679)."""
    data = a_file(account={"profile": {"birth_date": bad, "headline": "Engineer"}})
    held = candidate.read(data)
    drawn = candidate.plan(user, held)
    assert "refused" in outcomes(drawn, "details")
    assert any(
        "Date of birth" == row.label
        for s in drawn.sections
        for row in s.rows
        if row.outcome == "refused"
    )

    candidate.apply(user, held)
    user.profile.refresh_from_db()
    assert user.profile.birth_date == ""
    assert user.profile.headline == "Engineer", "and the rest of the file is still read"


@pytest.mark.parametrize(
    "bad",
    [
        "PT",
        {"PT": 1},
        7,
        [1, 2],
        [["PT"]],
        ["ZZ", "QQ", "XX"],
        ["PT"] * 11,
        [MARKUP],
    ],
)
def test_a_list_of_nationalities_of_the_wrong_type_too_long_or_unknown_stores_nothing(user, bad):
    """Read code by code through the one function the page is held to (#680): what it will
    not take is a refused row, and nothing the file says about countries is stored."""
    data = a_file(account={"profile": {"nationalities": bad, "headline": "Engineer"}})
    held = candidate.read(data)
    drawn = candidate.plan(user, held)
    assert "refused" in outcomes(drawn, "details")
    assert "add" not in [
        row.outcome for s in drawn.sections for row in s.rows if row.label == "Nationality"
    ]
    candidate.apply(user, held)
    user.profile.refresh_from_db()
    assert user.profile.nationalities == []
    assert user.profile.headline == "Engineer"
    assert MARKUP not in " ".join(notes(drawn, "details")), "what a row says is never the file's"


def test_a_file_cannot_hold_a_thousand_nationalities_in_a_session(user):
    held = candidate.read(a_file(account={"profile": {"nationalities": ["PT"] * 5000}}))
    assert len(held["details"]["nationalities"]) <= 11


# ------------------------------------------------------------ messaging handles (#682)


def test_too_many_handles_are_read_so_far_and_no_further():
    rows = [{"service": "telegram", "handle": f"person_{n:04d}"} for n in range(200)]

    held = candidate.read(a_file(account={"profile": {"messaging_handles": rows}}))

    assert len(held["messaging_handles"]) == candidate.MAX_CONTACT_ROWS
    assert held["cut"] == {"messaging_handles": 200}


def test_a_handle_that_is_malformed_is_refused_in_the_plan_and_never_raises(user):
    rows = [
        "a string",
        {"service": "matrix", "handle": 7},
        {"service": ["matrix"], "handle": "@a:example.org"},
        {"service": "matrix", "handle": None},
        {"service": "matrix", "handle": "x" * 5000},
        {"service": "matrix", "handle": "@ok:example.org"},
    ]
    held = candidate.read(a_file(account={"profile": {"messaging_handles": rows}}))

    drawn = candidate.plan(user, held)
    candidate.apply(user, held)

    outcomes = [row.outcome for section in drawn.sections for row in section.rows]
    assert candidate.ADD in outcomes and candidate.REFUSED in outcomes
    kept = [row.handle for row in user.profile.messaging_handles.all()]
    assert "@ok:example.org" in kept and all(len(handle) <= 255 for handle in kept)


def test_whose_a_handle_is_is_never_the_files_to_say(user, other_user):
    from postulo.core.models import MessagingHandle

    somebody_else = {
        "owner": other_user.pk,
        "owner_id": other_user.pk,
        "user_id": other_user.pk,
        "object_id": other_user.profile.pk,
        "content_type": 1,
        "is_recovery": True,
        "verified_at": "2026-01-01T00:00:00Z",
        "comparable": "taken",
    }
    data = a_file(
        account={
            "profile": {
                "messaging_handles": [{"service": "matrix", "handle": "@a:b.c", **somebody_else}]
            }
        }
    )

    candidate.apply(user, candidate.read(data))

    row = MessagingHandle.objects.get()
    assert (row.owner, row.holder) == (user, user.profile)
    assert row.comparable == "@a:b.c"
    assert not MessagingHandle.objects.for_user(other_user).exists()


# ------------------------------------------------------------- the company an entry names


def test_a_file_never_adds_a_company_whatever_its_entries_say(user, other_user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=other_user, name="Initech")
    data = a_file(resume={"experience": [a_role(company="Initech"), a_role(id=2, company="New")]})
    candidate.apply(user, candidate.read(data))
    assert not Company.objects.filter(owner=user).exists()
    assert Experience.objects.filter(owner=user, company__isnull=False).count() == 0


def test_a_hostile_company_is_a_name_to_look_for_and_nothing_else(user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=user, name="Initech")
    for hostile in (MARKUP, SCRIPT, ["Initech"], {"id": 1}, 1, "x" * 100_000, None):
        held = candidate.read(a_file(resume={"experience": [a_role(company=hostile)]}))
        plan = candidate.plan(user, held)
        assert outcomes(plan, "experience") == ["add"]
        assert all(row.company is None for row in plan.rows())
    assert Company.objects.filter(owner=user).count() == 1


def test_a_name_that_is_one_of_the_accounts_companies_is_offered_as_the_link(user):
    from postulo.jobs.models import Company

    mine = Company.objects.create(owner=user, name="Initech")
    held = candidate.read(a_file(resume={"experience": [a_role(company="initech")]}))
    assert [row.company for row in candidate.plan(user, held).rows()] == [mine]
    candidate.apply(user, held)
    assert Experience.objects.get(owner=user).company == mine


def a_degree(**changes) -> dict:
    return {"id": 1, "institution": "Initech U", "qualification": "BSc", **changes}


def test_an_education_file_never_adds_a_company_whatever_its_entries_say(user, other_user):
    from postulo.jobs.models import Company
    from postulo.resume.models import Education

    Company.objects.create(owner=other_user, name="Initech U")
    data = a_file(
        resume={"education": [a_degree(company="Initech U"), a_degree(id=2, company="New")]}
    )
    candidate.apply(user, candidate.read(data))
    assert not Company.objects.filter(owner=user).exists()
    assert Education.objects.filter(owner=user, company__isnull=False).count() == 0


def test_a_hostile_education_company_is_a_name_to_look_for_and_nothing_else(user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=user, name="Initech U")
    for hostile in (MARKUP, SCRIPT, ["Initech U"], {"id": 1}, 1, "x" * 100_000, None):
        held = candidate.read(a_file(resume={"education": [a_degree(company=hostile)]}))
        plan = candidate.plan(user, held)
        assert outcomes(plan, "education") == ["add"]
        assert all(row.company is None for row in plan.rows())
    assert Company.objects.filter(owner=user).count() == 1


@pytest.mark.parametrize("level", [9, -1, 10**200, MARKUP, SCRIPT, "6; DROP TABLE", [6], {"n": 6}])
def test_an_eqf_level_the_form_would_not_take_is_a_refused_row_and_stores_nothing(user, level):
    from postulo.resume.models import Education

    data = a_file(
        resume={
            "education": [{"id": 1, "qualification": "BSc", "institution": "U", "eqf_level": level}]
        }
    )

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "education") == [candidate.REFUSED]
    assert not Education.objects.for_user(user).exists()


def test_a_hostile_publication_is_text_on_the_page_and_a_refused_row_where_it_is_an_address(
    client, user
):
    """A DOI is a shape and a link is an address: neither carries a script into the page or
    into a CV, and markup in a title arrives as the text it is (#687)."""
    hostile = {
        "entry_type": "article",
        "title": "<script>alert(1)</script>",
        "authors": "<img src=x onerror=alert(1)>",
        "doi": "10.1000/<script>",
        "url": "javascript:alert(1)",
        "cite_key": "a{b}",
    }
    safe = {**hostile, "doi": "", "url": "", "title": "T"}
    data = a_file(resume={"publications": [hostile, safe]})

    plan = candidate.plan(user, candidate.read(data))

    assert outcomes(plan, "publications") == [candidate.REFUSED, candidate.ADD]
    candidate.apply(user, candidate.read(data))
    stored = Publication.objects.for_user(user).get()
    assert stored.url == "" and stored.doi == ""
    assert "{" not in stored.cite_key

    client.force_login(user)
    page = client.get(reverse("resume:overview")).content.decode()
    assert "<img src=x" not in page and "<script>alert(1)" not in page


# ------------------------------------------------- the code of a spoken language (#689)


@pytest.mark.parametrize(
    "bad",
    [
        "not a code",
        "e" * 80,
        "en;DROP",
        "<script>",
        "e\x00n",
        7,
        ["en"],
        {"en": 1},
        "en-" + "x" * 40,
    ],
)
def test_a_malformed_or_over_long_language_code_is_a_refused_row_and_stores_nothing(user, bad):
    data = a_file(resume={"languages": [{"name": "English", "code": bad, "proficiency": "c1"}]})

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "languages") == [candidate.REFUSED]
    assert not LanguageSkill.objects.for_user(user).exists()


def test_a_code_in_a_file_is_stored_for_the_person_who_added_it_and_nobody_else(user, other_user):
    data = a_file(resume={"languages": [{"name": "English", "code": "en", "proficiency": "c1"}]})

    candidate.apply(user, candidate.read(data))

    assert LanguageSkill.objects.for_user(user).get().code == "en"
    assert not LanguageSkill.objects.for_user(other_user).exists()


def test_the_word_other_in_a_file_is_not_a_code(user):
    data = a_file(
        resume={"languages": [{"name": "Mirandês", "code": "other", "proficiency": "c1"}]}
    )

    candidate.apply(user, candidate.read(data))

    assert LanguageSkill.objects.for_user(user).get().code == ""


def a_certification(**changes) -> dict:
    return {"id": 1, "name": "CKA", "issuer": "Initech", **changes}


def test_a_file_never_adds_a_company_whatever_its_certifications_say(user, other_user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=other_user, name="Initech")
    data = a_file(
        resume={
            "certifications": [
                a_certification(company="Initech"),
                a_certification(id=2, name="Other", company="New"),
            ]
        }
    )
    candidate.apply(user, candidate.read(data))
    assert not Company.objects.filter(owner=user).exists()
    assert Certification.objects.filter(owner=user, company__isnull=False).count() == 0


def test_a_hostile_certification_company_is_a_name_to_look_for_and_nothing_else(user):
    from postulo.jobs.models import Company

    Company.objects.create(owner=user, name="Initech")
    for hostile in (MARKUP, SCRIPT, ["Initech"], {"id": 1}, 1, "x" * 100_000, None):
        held = candidate.read(a_file(resume={"certifications": [a_certification(company=hostile)]}))
        plan = candidate.plan(user, held)
        assert outcomes(plan, "certifications") == ["add"]
        assert all(row.company is None for row in plan.rows())
    assert Company.objects.filter(owner=user).count() == 1


# ---------------------------------------------------------------- driving licences (#691)


@pytest.mark.parametrize(
    "bad",
    [
        MARKUP,
        SCRIPT,
        "B" * 5000,
        ["B", MARKUP],
        [f"X{n}" for n in range(5000)],
        {"B": 1},
        ["e\x00"],
        [["B"]],
    ],
)
def test_a_hostile_or_over_long_licence_category_is_a_refused_row_and_stores_nothing(user, bad):
    from postulo.resume.models import DrivingLicence

    data = a_file(resume={"driving_licences": [{"country": "PT", "categories": bad}]})

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "driving_licences") == [candidate.REFUSED]
    assert not DrivingLicence.objects.for_user(user).exists()


def test_an_over_long_note_or_a_hostile_country_in_a_licence_is_refused(user):
    from postulo.resume.models import DrivingLicence

    data = a_file(
        resume={
            "driving_licences": [
                {"country": "PT", "categories": ["B"], "other_categories": "x" * 5000},
                {"country": MARKUP, "categories": ["B"]},
            ]
        }
    )

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    assert outcomes(plan, "driving_licences") == [candidate.REFUSED, candidate.REFUSED]
    assert not DrivingLicence.objects.for_user(user).exists()


# ------------------------------------------------------- honours and awards (#693)


@pytest.mark.parametrize(
    "bad",
    [
        {"title": MARKUP},
        {"title": SCRIPT},
        {"title": "T" * 5000},
        {"title": "T", "awarded_by": "G" * 5000},
        {"title": "T", "summary": "S" * 200_000},
        {"title": "T", "url": "javascript:alert(1)"},
        {"title": "T", "url": "https://example.org/" + "a" * 5000},
        {"title": "T", "awarded_on": MARKUP},
        {"title": "e\x00"},
    ],
)
def test_a_hostile_or_over_long_honour_is_a_refused_row_and_stores_nothing(user, bad):
    from postulo.resume.models import Honour

    data = a_file(resume={"honours": [bad]})

    plan = candidate.plan(user, candidate.read(data))
    candidate.apply(user, candidate.read(data))

    for honour in Honour.objects.for_user(user):
        # Whatever was kept is what the form keeps: bounded, and never a script address.
        assert len(honour.title) <= 200 and len(honour.awarded_by) <= 200
        assert not honour.url.startswith("javascript:")
    assert outcomes(plan, "honours") in ([candidate.REFUSED], [candidate.ADD])
