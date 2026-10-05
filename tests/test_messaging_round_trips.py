"""Where a messaging handle travels: the archive, the candidate file, the API, a merge, and
the contact's document and erasure (#682).

The handles are carried as the telephone numbers, the addresses and the links are: a
`messaging_handles` block on the profile and on each contact, written by one builder, read
back as a claim, and moved, counted and erased with the contact they belong to.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from postulo.core import export, gdpr, importer
from postulo.core.models import MessagingHandle
from postulo.jobs import merging
from postulo.jobs.models import Company, Contact
from postulo.resume import candidate

pytestmark = pytest.mark.django_db


@pytest.fixture
def contact(user):
    return Contact.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Aperture"), name="Cave"
    )


def add(holder, owner, handle, *, service="matrix", label="", primary=False):
    return MessagingHandle.objects.create(
        owner=owner, holder=holder, service=service, label=label, handle=handle, is_primary=primary
    )


# ------------------------------------------------------------------------ the archive


def test_the_archive_is_at_the_format_that_carries_them():
    assert export.FORMAT_VERSION >= 34
    assert export.CANDIDATE_FORMAT >= 5
    assert export.MESSAGING_FIELDS == ("service", "label", "handle", "is_primary")


def test_the_archive_carries_every_handle_of_the_person_and_of_each_contact(user, contact):
    add(user.profile, user, "@me:example.org", primary=True)
    add(contact, user, "cave.42", service="signal", primary=True)
    add(contact, user, "irc nick", service="", label="Our IRC")

    document = export.build_document(user)

    assert document["account"]["profile"]["messaging_handles"] == [
        {"service": "matrix", "label": "", "handle": "@me:example.org", "is_primary": True}
    ]
    [written] = document["companies"][0]["contacts"]
    assert [
        (row["service"], row["label"], row["handle"]) for row in written["messaging_handles"]
    ] == [
        ("signal", "", "cave.42"),
        ("", "Our IRC", "irc nick"),
    ]
    assert "verified_at" not in json.dumps(written["messaging_handles"])


def test_the_archive_brings_them_back_to_another_account(user, other_user, contact):
    add(user.profile, user, "@me:example.org", primary=True)
    add(user.profile, user, "me_again", service="telegram")
    add(contact, user, "cave.42", service="signal", primary=True)
    add(contact, user, "irc nick", service="", label="Our IRC")
    buffer = export.write_archive(user)

    report = importer.load(other_user, zipfile.ZipFile(buffer))

    assert not [line for line in report.skipped if "messaging" in line], report.skipped
    mine = {
        (row.service, row.label, row.handle, row.is_primary)
        for row in other_user.profile.messaging_handles.all()
    }
    assert mine == {
        ("matrix", "", "@me:example.org", True),
        ("telegram", "", "me_again", False),
    }
    [there] = Contact.objects.filter(owner=other_user, name="Cave")
    assert {
        (row.service, row.label, row.handle, row.is_primary, row.owner)
        for row in there.messaging_handles.all()
    } == {
        ("signal", "", "cave.42", True, other_user),
        ("", "Our IRC", "irc nick", False, other_user),
    }


def test_a_service_this_instance_does_not_know_comes_back_as_other_named_by_its_key(user, contact):
    """A file is a claim, and none of it is refused: the word is lost, the handle is not."""
    rows = [
        {"service": "elsewhere", "label": "", "handle": "nick", "is_primary": True},
        {"service": "matrix", "label": "", "handle": "not a matrix id", "is_primary": False},
        {"service": "matrix", "label": "", "handle": "@A:Example.org", "is_primary": False},
    ]

    importer._restore_messaging_handles(
        contact, user, importer._messaging_rows({"messaging_handles": rows})
    )

    assert [
        (row.service, row.label, row.handle, row.is_primary)
        for row in contact.messaging_handles.all()
    ] == [
        ("", "elsewhere", "nick", True),
        ("", "matrix", "not a matrix id", False),
        ("matrix", "", "@a:example.org", False),
    ]


def test_an_older_archive_has_none_and_restores_none(user, other_user):
    add(user.profile, user, "@me:example.org", primary=True)
    buffer = export.write_archive(user)
    with zipfile.ZipFile(buffer) as archive:
        document = json.loads(archive.read(export.MANIFEST_NAME))
    document["postulo"]["format"] = export.FORMAT_VERSION - 1
    del document["account"]["profile"]["messaging_handles"]
    older = io.BytesIO()
    with zipfile.ZipFile(older, "w") as archive:
        archive.writestr(export.MANIFEST_NAME, json.dumps(document))
    older.seek(0)

    importer.load(other_user, zipfile.ZipFile(older))

    assert not other_user.profile.messaging_handles.exists()


def test_importing_into_a_profile_that_has_a_primary_keeps_it_and_adds_no_duplicates(user):
    add(user.profile, user, "@me:example.org", primary=True)
    rows = [
        {"service": "matrix", "handle": "@ME:example.org", "is_primary": True},
        {"service": "telegram", "handle": "me_again", "is_primary": True},
    ]

    importer._restore_messaging_handles(
        user.profile, user, importer._messaging_rows({"messaging_handles": rows})
    )

    held = {row.handle: row.is_primary for row in user.profile.messaging_handles.all()}
    assert held == {"@me:example.org": True, "me_again": False}


# --------------------------------------------------------------------- the candidate file


def test_the_candidate_file_round_trips_and_only_adds_what_is_new(user, other_user):
    add(user.profile, user, "@me:example.org", primary=True)
    add(user.profile, user, "alex.42", service="signal")
    data = json.dumps(export.build_candidate_document(user)).encode()
    held = candidate.read(data)

    first = candidate.plan(other_user, held)
    section = next(s for s in first.sections if s.key == "messaging_handles")
    assert [(row.label, row.sub, row.outcome) for row in section.rows] == [
        ("@me:example.org", "Matrix", candidate.ADD),
        ("alex.42", "Signal", candidate.ADD),
    ]
    candidate.apply(other_user, held)
    again = candidate.plan(other_user, held)
    section = next(s for s in again.sections if s.key == "messaging_handles")

    assert {row.outcome for row in section.rows} == {candidate.PRESENT}
    primaries = [row.handle for row in other_user.profile.messaging_handles.all() if row.is_primary]
    assert primaries == ["@me:example.org"]
    assert candidate.apply(other_user, held).total == 0, "a second confirmation adds nothing"


def test_a_candidate_file_keeps_the_primary_the_account_has(user):
    add(user.profile, user, "@mine:example.org", primary=True)
    held = candidate.read(
        json.dumps(
            {
                "postulo": {"candidate_format": export.CANDIDATE_FORMAT},
                "account": {
                    "profile": {
                        "messaging_handles": [
                            {"service": "telegram", "handle": "theirs_1", "is_primary": True}
                        ]
                    }
                },
            }
        ).encode()
    )

    candidate.apply(user, held)

    assert {row.handle: row.is_primary for row in user.profile.messaging_handles.all()} == {
        "@mine:example.org": True,
        "theirs_1": False,
    }


# ------------------------------------------------------------------------------ the API


def bearer(user):
    from postulo.api.models import ApiToken

    _record, raw = ApiToken.issue(user, "Agent", scopes=("write", "read"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_contact(client, user, contact, **payload):
    return client.post(
        f"/api/v1/companies/{contact.company_id}/contacts",
        data=json.dumps({"name": "Caroline", **payload}),
        content_type="application/json",
        **bearer(user),
    )


def test_the_api_takes_and_returns_handles_on_a_contact(client, user, contact):
    response = post_contact(
        client,
        user,
        contact,
        messaging_handles=[
            {"service": "matrix", "handle": "@Caroline:Example.org"},
            {"service": "signal", "handle": "caroline.42", "is_primary": True},
            {"service": "other", "label": "Our IRC", "handle": "carol"},
        ],
    )

    assert response.status_code == 201, response.content
    listed = {
        (row["service"], row["label"], row["handle"]): row["is_primary"]
        for row in response.json()["messaging_handles"]
    }
    assert listed == {
        ("signal", "", "caroline.42"): True,
        ("matrix", "", "@caroline:example.org"): False,
        ("", "Our IRC", "carol"): False,
    }
    detail = client.get(f"/api/v1/companies/{contact.company_id}", **bearer(user)).json()
    caroline = next(person for person in detail["contacts"] if person["name"] == "Caroline")
    assert len(caroline["messaging_handles"]) == 3


@pytest.mark.parametrize(
    ("row", "said"),
    [
        (
            {"service": "threema", "handle": "ABC"},
            "does not look like a handle on Threema",
        ),
        ({"service": "nope", "handle": "x"}, "not a service a handle can be on"),
        ({"service": "other", "handle": "x"}, "Name the service"),
        ({"service": "matrix", "handle": "@a:b.c"}, None),
    ],
)
def test_the_api_refuses_a_handle_that_is_not_its_services_and_writes_nothing(
    client, user, contact, row, said
):
    response = post_contact(client, user, contact, messaging_handles=[row])

    if said is None:
        assert response.status_code == 201
        return
    assert response.status_code == 422, response.content
    assert said in response.content.decode()
    assert not Contact.objects.filter(name="Caroline").exists(), "no contact left behind it"


def test_the_api_refuses_a_list_with_no_end_and_a_handle_listed_twice(client, user, contact):
    too_many = [{"service": "other", "label": "x", "handle": f"h{n}"} for n in range(51)]
    twice = [
        {"service": "matrix", "handle": "@a:b.c"},
        {"service": "matrix", "handle": "@A:b.c"},
    ]

    assert post_contact(client, user, contact, messaging_handles=too_many).status_code == 422
    assert post_contact(client, user, contact, messaging_handles=twice).status_code == 422
    assert not Contact.objects.filter(name="Caroline").exists()


def test_a_client_that_has_never_heard_of_handles_is_answered_with_an_empty_list(
    client, user, contact
):
    response = post_contact(client, user, contact)

    assert response.status_code == 201
    assert response.json()["messaging_handles"] == []


# ----------------------------------------------------------------------------- the merge


@pytest.fixture
def twin(user):
    return Contact.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Aperture Science"), name="C."
    )


def test_a_merge_says_it_moves_the_handles_and_moves_them(user, contact, twin):
    add(contact, user, "@cave:example.org", primary=True)
    add(twin, user, "@CAVE:example.org", primary=True)
    add(twin, user, "cave.42", service="signal")

    plan = merging.plan_contacts(contact, twin)
    moved = [move for move in plan.moves if move.label == "Messaging handles"]
    assert [(move.count, move.names) for move in moved] == [(1, ["Signal cave.42"])], (
        "the one both list is not moved, and not counted"
    )

    merging.merge_contacts(contact, twin)

    assert not Contact.objects.filter(pk=twin.pk).exists()
    assert {row.handle: row.is_primary for row in contact.messaging_handles.all()} == {
        "@cave:example.org": True,
        "cave.42": False,
    }
    assert not MessagingHandle.objects.filter(object_id=twin.pk).exists()


def test_a_merge_gives_the_kept_person_a_primary_where_they_had_none(user, contact, twin):
    add(twin, user, "cave.42", service="signal", primary=True)

    merging.merge_contacts(contact, twin)

    [row] = contact.messaging_handles.all()
    assert row.handle == "cave.42" and row.is_primary


def test_a_merge_leaves_somebody_elses_handles_alone(user, other_user, contact, twin):
    theirs = add(other_user.profile, other_user, "@them:example.org", primary=True)
    add(twin, user, "cave.42", service="signal")

    merging.merge_contacts(contact, twin)

    theirs.refresh_from_db()
    assert (theirs.owner, theirs.object_id) == (other_user, other_user.profile.pk)


# --------------------------------------------------------- the contact's document, the way out


def test_the_contacts_document_lists_the_handles(user, contact):
    add(contact, user, "@cave:example.org", primary=True)
    add(contact, user, "irc nick", service="", label="Our IRC")

    document = gdpr.contact_document(contact)

    assert gdpr.DOCUMENT_VERSION >= 4
    assert document["messaging_handles"] == [
        {"service": "matrix", "label": "", "handle": "@cave:example.org", "primary": True},
        {"service": "", "label": "Our IRC", "handle": "irc nick", "primary": False},
    ]


def test_erasure_counts_the_handles_and_removes_them(user, contact):
    add(contact, user, "@cave:example.org", primary=True)
    add(contact, user, "cave.42", service="signal")

    report = gdpr.erase_contact(contact)

    assert report.deleted["messaging_handles"] == 2
    assert "2 messaging handles" in report.summary()
    assert not MessagingHandle.objects.exists()


def test_the_summary_counts_one_handle_in_its_own_form(user, contact):
    add(contact, user, "@cave:example.org", primary=True)

    summary = gdpr.erase_contact(contact).summary()

    assert "1 messaging handle" in summary and "1 messaging handles" not in summary


def test_the_dry_run_counts_the_handles_and_the_sentence_names_them(user, contact, settings):
    from postulo.core.models import SiteSettings

    add(contact, user, "@cave:example.org", primary=True)
    add(contact, user, "cave.42", service="signal")
    site = SiteSettings.get()
    site.retention_days = 1
    site.save()
    Contact.objects.filter(pk=contact.pk).update(created_at="2020-01-01T00:00:00Z")

    report = gdpr.retention_dry_run()

    assert report["would_remove"]["messaging_handles"] == 2
    assert "2 messaging handles" in report["would_remove_line"]
    assert MessagingHandle.objects.count() == 2, "a dry run deletes nothing"
