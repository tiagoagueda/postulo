"""A CV cannot be made to print another account's number, link, identifier or email (#308).

A CV now points at rows: the number it prints, the link, the identifiers, the address. A
pointer is only a number until somebody has checked whose row it is, and there are four
ways one can arrive -- the page, the API, an archive, and whatever writes the column
directly. Each is tried here with somebody else's row, and with a row the account owns but
that is not *theirs*: a recruiter's number is a row this account recorded, never a number
this account is.

Two lines of defence, and both are held. The row is refused where it is written, in the
same words as one that does not exist, so the refusal confirms nothing. And it is looked
up among the owner's own rows where it is read, so an id that reached the column some
other way prints nothing.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from postulo.accounts.models import PersonIdentifier
from postulo.api.models import ApiToken
from postulo.core import export as export_module
from postulo.core import importer
from postulo.core.models import PhoneNumber, WebLink
from postulo.documents import printing, rendering
from postulo.documents.forms import CVForm
from postulo.documents.models import CV, Prints

pytestmark = pytest.mark.django_db

THEIR_NUMBER = "+351930000001"
THEIR_LINK = "https://www.linkedin.com/in/sam-rivera"
THEIR_ADDRESS = "sam@private.example"
THEIR_ORCID = "0000-0001-5109-3700"
RECRUITER = "+351210000002"


@pytest.fixture
def theirs(other_user):
    """Everything another account holds that a contact block could print."""
    profile = other_user.profile
    return {
        "phone": PhoneNumber.objects.create(
            owner=other_user, holder=profile, number=THEIR_NUMBER, is_primary=True
        ),
        "social": WebLink.objects.create(
            owner=other_user, holder=profile, kind="social", url=THEIR_LINK, is_primary=True
        ),
        "email": EmailAddress.objects.create(user=other_user, email=THEIR_ADDRESS, verified=True),
        "identifier": PersonIdentifier.objects.create(
            profile=profile, scheme="orcid", value=THEIR_ORCID
        ),
    }


@pytest.fixture
def cv(user):
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    return CV.objects.create(owner=user, name="Main")


@pytest.fixture
def recruiters_number(user):
    """A number this account recorded for somebody else: owned by it, and not its own."""
    from postulo.jobs.models import Company, Contact

    company = Company.objects.create(owner=user, name="Aperture")
    contact = Contact.objects.create(owner=user, company=company, name="Cave Johnson")
    return PhoneNumber.objects.create(owner=user, holder=contact, number=RECRUITER, is_primary=True)


def nothing_of_theirs_is_printed(cv) -> None:
    cv = CV.objects.get(pk=cv.pk)
    html = rendering.render_cv_html(cv) + rendering.cv_text(cv)
    for printed in (THEIR_NUMBER, THEIR_LINK, THEIR_ADDRESS, THEIR_ORCID, RECRUITER):
        assert printed not in html, printed


def untouched(cv) -> None:
    cv = CV.objects.get(pk=cv.pk)
    assert printing.is_default(cv)
    for detail in printing.DETAILS:
        assert getattr(cv, f"{detail.pin_field}_id") is None, detail.key
    assert not cv.pinned_identifiers.exists()


# --------------------------------------------------------------------------- the page


def posted(**changes) -> dict:
    data = {
        "name": "Main",
        "kind": "cv",
        "theme": "plain",
        "language": "",
        "show_contact_details": "on",
        "show_location": "on",
    }
    data.update(changes)
    return data


def test_the_page_offers_only_the_owners_own_rows(client, user, cv, theirs, recruiters_number):
    """A list populated from the whole table would be a disclosure even if the save were
    refused: the menus are built from the person's own profile and nothing else."""
    client.force_login(user)
    html = client.get(reverse("documents:cv_update", args=[cv.pk])).content.decode()
    for hidden in (THEIR_NUMBER, THEIR_LINK, THEIR_ADDRESS, THEIR_ORCID, RECRUITER):
        assert hidden not in html, hidden
    form = CVForm(user=user, instance=cv)
    for detail in printing.DETAILS:
        offered = [value for value, _label in form.fields[f"prints_{detail.key}"].choices]
        # A kind whose default prints nothing has no second way of saying none (#682).
        assert offered == (["default", "none"] if detail.follows else ["none"]), detail.key
    assert form.fields["identifier_rows"].choices == []


@pytest.mark.parametrize(
    ("field", "row"),
    [
        ("prints_phone", "phone"),
        ("prints_social", "social"),
        ("prints_email", "email"),
        ("prints_repository", "social"),
    ],
)
def test_the_page_refuses_another_accounts_row(client, user, cv, theirs, field, row):
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]), posted(**{field: str(theirs[row].pk)})
    )
    assert response.status_code == 200, "the form again, with the error under the menu"
    assert field in response.context["form"].errors
    untouched(cv)
    nothing_of_theirs_is_printed(cv)


def test_the_page_refuses_another_accounts_identifier(client, user, cv, theirs):
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        posted(prints_identifiers="chosen", identifier_rows=[str(theirs["identifier"].pk)]),
    )
    assert response.status_code == 200
    assert "identifier_rows" in response.context["form"].errors
    untouched(cv)
    nothing_of_theirs_is_printed(cv)


def test_a_contacts_number_is_not_one_of_the_owners_own(client, user, cv, recruiters_number):
    """The account owns the row and the row is not the person's: `OwnedModel.owner` alone
    would have let it through, which is why the list is the profile's and not the owner's."""
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        posted(prints_phone=str(recruiters_number.pk)),
    )
    assert response.status_code == 200 and "prints_phone" in response.context["form"].errors
    untouched(cv)
    with pytest.raises(printing.NotOffered):
        printing.choose(cv, "phone", Prints.CHOSEN, recruiters_number.pk)


def test_nobody_else_can_open_or_save_the_choice_on_a_cv(client, other_user, cv):
    client.force_login(other_user)
    address = reverse("documents:cv_update", args=[cv.pk])
    assert client.get(address).status_code == 404
    assert client.post(address, posted(prints_phone="none")).status_code == 404
    untouched(cv)


# ---------------------------------------------------------------------------- the API


def bearer(user, *scopes):
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read",))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def patch(client, cv, payload, **headers):
    return client.patch(
        f"/api/v1/cvs/{cv.pk}", data=json.dumps(payload), content_type="application/json", **headers
    )


@pytest.mark.parametrize(
    ("kind", "row"),
    [("phone", "phone"), ("social", "social"), ("email", "email"), ("website", "social")],
)
def test_the_api_refuses_another_accounts_row_as_it_refuses_one_that_does_not_exist(
    client, user, cv, theirs, kind, row
):
    token = bearer(user, "write")
    refused = patch(
        client, cv, {"prints": {kind: {"choice": "chosen", "id": theirs[row].pk}}}, **token
    )
    missing = patch(client, cv, {"prints": {kind: {"choice": "chosen", "id": 987654321}}}, **token)
    assert refused.status_code == missing.status_code == 422
    assert refused.json()["detail"] == missing.json()["detail"], "one answer for both"
    assert f"prints.{kind}.id" in refused.json()["detail"]
    for hidden in (THEIR_NUMBER, THEIR_LINK, THEIR_ADDRESS):
        assert hidden not in refused.content.decode()
    untouched(cv)
    nothing_of_theirs_is_printed(cv)


def test_the_api_refuses_another_accounts_identifier(client, user, cv, theirs):
    own = PersonIdentifier.objects.create(profile=user.profile, scheme="orcid", value="0000-1")
    response = patch(
        client,
        cv,
        {"prints": {"identifiers": {"choice": "chosen", "ids": [own.pk, theirs["identifier"].pk]}}},
        **bearer(user, "write"),
    )
    assert response.status_code == 422
    assert THEIR_ORCID not in response.content.decode()
    untouched(cv)


def test_the_api_refuses_a_contacts_number(client, user, cv, recruiters_number):
    response = patch(
        client,
        cv,
        {"prints": {"phone": {"choice": "chosen", "id": recruiters_number.pk}}},
        **bearer(user, "write"),
    )
    assert response.status_code == 422
    untouched(cv)


def test_the_api_lists_only_the_callers_rows_to_choose_from(
    client, user, cv, theirs, recruiters_number
):
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).content.decode()
    for hidden in (THEIR_NUMBER, THEIR_LINK, THEIR_ADDRESS, THEIR_ORCID, RECRUITER):
        assert hidden not in body, hidden


OWN_NUMBER = "+351912345678"
OWN_OTHER_NUMBER = "+351211111111"
OWN_OTHER_ADDRESS = "alex@work.example"
OWN_LINK = "https://mastodon.example/@alex"
OWN_OTHER_LINK = "https://www.linkedin.com/in/alex-morgan"
OWN_ORCID = "0000-0002-1825-0097"


@pytest.fixture
def own(user):
    """The caller's own details: two of each kind that prints one, so that there is a row
    the CV does not print."""
    profile = user.profile
    PhoneNumber.objects.create(owner=user, holder=profile, number=OWN_NUMBER, is_primary=True)
    PhoneNumber.objects.create(owner=user, holder=profile, number=OWN_OTHER_NUMBER, kind="work")
    WebLink.objects.create(owner=user, holder=profile, kind="social", url=OWN_LINK, is_primary=True)
    WebLink.objects.create(owner=user, holder=profile, kind="social", url=OWN_OTHER_LINK)
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    EmailAddress.objects.create(user=user, email=OWN_OTHER_ADDRESS, verified=True)
    PersonIdentifier.objects.create(profile=profile, scheme="orcid", value=OWN_ORCID)
    return user


KINDS = ("phone", "email", "social", "repository", "website", "identifiers")


def test_a_token_that_may_only_write_is_not_handed_the_rows_it_did_not_change(client, own, cv):
    """`write` sees what it changes and `read` reads everything the owner has. `offered` is
    the second: every number, confirmed address, link and identifier in the caller's
    details, which a call that changed a CV did not change. A token holding `write` alone
    was answered with all of them -- by a `PATCH` with nothing in it -- while the same
    token is refused `GET /cvs/{id}` and `GET /profile`."""
    token = bearer(own, "write")
    assert client.get(f"/api/v1/cvs/{cv.pk}", **token).status_code == 403
    assert client.get("/api/v1/profile", **token).status_code == 403

    for payload in ({}, {"prints": {"website": {"choice": "none"}}}):
        response = patch(client, cv, payload, **token)
        assert response.status_code == 200, response.content
        body = response.json()
        for kind in KINDS:
            assert "offered" not in body["prints"][kind], kind
        said = response.content.decode()
        for unprinted in (OWN_OTHER_NUMBER, OWN_OTHER_ADDRESS, OWN_OTHER_LINK):
            assert unprinted not in said, unprinted

    # The rest of the answer is the CV as it stands: what it changed, and what that prints.
    assert body["name"] == "Main" and body["show_contact_details"] is True
    assert body["prints"]["website"] == {"choice": "none", "id": None, "printed": ""}
    assert body["prints"]["phone"] == {"choice": "default", "id": None, "printed": OWN_NUMBER}
    assert body["prints"]["identifiers"] == {
        "choice": "default",
        "ids": [],
        "printed": [f"ORCID {OWN_ORCID}"],
    }
    assert body["prints"]["location"] is True


def test_a_token_that_may_also_read_is_handed_them_as_reading_the_cv_hands_them(client, own, cv):
    both = patch(client, cv, {}, **bearer(own, "write", "read"))
    read = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(own, "read"))
    assert both.status_code == read.status_code == 200
    assert both.json() == read.json()
    prints = both.json()["prints"]
    assert [row["value"] for row in prints["phone"]["offered"]] == [OWN_NUMBER, OWN_OTHER_NUMBER]
    assert [row["value"] for row in prints["email"]["offered"]] == [own.email, OWN_OTHER_ADDRESS]
    assert {row["value"] for row in prints["social"]["offered"]} == {OWN_LINK, OWN_OTHER_LINK}
    assert [row["value"] for row in prints["identifiers"]["offered"]] == [OWN_ORCID]
    assert prints["website"]["offered"] == [], "none of a kind is an empty list, not a missing one"


def test_a_token_that_may_only_write_can_still_pin_a_row_it_was_told_of(client, own, cv):
    """The id is one it learnt from a token that reads, or from the person: it is accepted
    on the same terms, and the answer still lists nothing else."""
    desk = PhoneNumber.objects.get(number=OWN_OTHER_NUMBER)
    response = patch(
        client,
        cv,
        {"prints": {"phone": {"choice": "chosen", "id": desk.pk}}},
        **bearer(own, "write"),
    )
    assert response.status_code == 200
    assert response.json()["prints"]["phone"] == {
        "choice": "chosen",
        "id": desk.pk,
        "printed": OWN_OTHER_NUMBER,
    }
    assert OWN_OTHER_ADDRESS not in response.content.decode()


def test_the_api_changes_nobody_elses_cv(client, user, other_user, cv):
    response = patch(
        client, cv, {"prints": {"phone": {"choice": "none"}}}, **bearer(other_user, "write")
    )
    assert response.status_code == 404, "not a 403: that would say the CV exists"
    untouched(cv)


def test_unknown_fields_change_nothing_they_name(client, user, cv, theirs):
    """Only what the schema lists is written: not the columns, and not whose CV it is."""
    response = patch(
        client,
        cv,
        {
            "pinned_phone_id": theirs["phone"].pk,
            "phone_choice": "chosen",
            "owner_id": 999,
            "prints": {"pinned_social_id": theirs["social"].pk},
        },
        **bearer(user, "write"),
    )
    assert response.status_code == 200
    untouched(cv)
    assert CV.objects.get(pk=cv.pk).owner == user


# ------------------------------------------------------------------------ the archive


def archive_of(user, edit) -> zipfile.ZipFile:
    with zipfile.ZipFile(export_module.write_archive(user)) as written:
        document = json.loads(written.read(export_module.MANIFEST_NAME))
    edit(document)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_an_archive_cannot_name_a_row_by_its_id(user, other_user, cv, theirs):
    """Everything in a CV's entry used to be handed to the model as it stood. A file that
    names the columns -- with the id of a row on the instance it is being imported into --
    has them left out, and says so."""

    def name_the_columns(document):
        (entry,) = document["documents"]["cvs"]
        entry["phone_choice"] = "chosen"
        entry["pinned_phone_id"] = theirs["phone"].pk
        entry["pinned_social_id"] = theirs["social"].pk
        entry["pinned_email_id"] = theirs["email"].pk
        entry["prints"]["phone"] = {"choice": "chosen", "id": theirs["phone"].pk}

    report = importer.load(user, archive_of(user, name_the_columns), force=True)
    imported = CV.objects.filter(owner=user).exclude(pk=cv.pk).get()
    untouched(imported)
    nothing_of_theirs_is_printed(imported)
    assert any("pinned_phone_id" in line and "left out" in line for line in report.skipped)


def test_an_archive_cannot_name_another_accounts_row_by_what_it_says(user, other_user, cv, theirs):
    """A reference is looked up among the importing account's own rows. One that matches
    somebody else's number, link, address or identifier matches nothing."""

    def name_their_rows(document):
        (entry,) = document["documents"]["cvs"]
        entry["prints"] = {
            "phone": {"choice": "chosen", "number": THEIR_NUMBER},
            "social": {"choice": "chosen", "url": THEIR_LINK},
            "email": {"choice": "chosen", "email": THEIR_ADDRESS},
            "identifiers": {
                "choice": "chosen",
                "rows": [{"scheme": "orcid", "value": THEIR_ORCID}],
            },
        }

    report = importer.load(user, archive_of(user, name_their_rows), force=True)
    imported = CV.objects.filter(owner=user).exclude(pk=cv.pk).get()
    untouched(imported)
    nothing_of_theirs_is_printed(imported)
    assert len([line for line in report.skipped if "chosen to print" in line]) == 4


def test_the_report_names_what_was_left_out_in_bounded_words(user, cv):
    """A key's name is whatever the file says, as long as the file likes and line breaks
    included. The report quotes each with `repr`, cut short, and no more than ten of them,
    as it does the username an archive asks for (#321)."""

    def name_a_great_deal(document):
        (entry,) = document["documents"]["cvs"]
        for index in range(25):
            entry[f"made_up_{index:02}"] = 1
        entry["a line\nbroken in two, and then " + "x" * 5000] = 1

    report = importer.load(user, archive_of(user, name_a_great_deal), force=True)
    (line,) = [line for line in report.skipped if "left out" in line]
    assert "\n" not in line and "\\n" in line, "the break is written out, not obeyed"
    assert "x" * 30 not in line, "each name is cut short"
    assert line.count("made_up_") == 9, "ten names: the long one, and the first nine of these"
    assert "'made_up_08'" in line and "made_up_09" not in line
    assert "16 more" in line
    assert len(line) < 500


def test_a_profile_that_is_not_the_importers_own_offers_no_rows(user, other_user, cv, theirs):
    """While #354 is open, an archive whose `account.profile` carries another account's
    `id` and `user_id` leaves that account's profile in memory as the importer's. The rows
    a choice is looked up among were read off whatever profile that was, so the file could
    then name the other account's number, link and identifier by what they say and have
    pointers at them stored on the importer's CV. `printing.offered` is the one place every
    door asks, and it lists nothing from a profile that is not its owner's."""

    def take_their_profile(document):
        document["account"]["profile"]["id"] = other_user.profile.pk
        document["account"]["profile"]["user_id"] = other_user.pk
        (entry,) = document["documents"]["cvs"]
        entry["prints"] = {
            "phone": {"choice": "chosen", "number": THEIR_NUMBER},
            "social": {"choice": "chosen", "url": THEIR_LINK},
            "identifiers": {
                "choice": "chosen",
                "rows": [{"scheme": "orcid", "value": THEIR_ORCID}],
            },
        }

    report = importer.load(user, archive_of(user, take_their_profile), force=True)
    imported = CV.objects.filter(owner=user).exclude(pk=cv.pk).get()
    untouched(imported)
    nothing_of_theirs_is_printed(imported)
    said = [line for line in report.skipped if "chosen to print" in line]
    assert len(said) == 3 and all(imported.name in line for line in said)


def test_nothing_is_offered_or_followed_from_a_profile_that_is_somebody_elses(
    user, other_user, cv, theirs
):
    """The same guard, asked directly: the profile the account object carries has been
    given another person's keys, which is what the import of #354 leaves behind."""
    user.profile.id, user.profile.user_id = other_user.profile.pk, other_user.pk
    for key in ("phone", "social", "repository", "website", "identifiers"):
        assert printing.offered(user, key) == [], key
    with pytest.raises(printing.NotOffered):
        printing.choose(cv, "phone", Prints.CHOSEN, theirs["phone"].pk, owner=user)
    printed = printing.resolve(user, cv)
    assert (printed.phone, printed.social, printed.identifiers) == ("", "", [])
    assert printing.followed(user)["phone"] == ""


# -------------------------------------------------- whatever wrote the column directly


def test_a_pointer_at_another_accounts_row_prints_nothing(user, cv, theirs, recruiters_number):
    """The second line of defence. The columns are set the way no door allows -- straight
    into the table -- and the render still looks each row up among the owner's own."""
    CV.objects.filter(pk=cv.pk).update(
        phone_choice=Prints.CHOSEN,
        pinned_phone=theirs["phone"],
        social_choice=Prints.CHOSEN,
        pinned_social=theirs["social"],
        email_choice=Prints.CHOSEN,
        pinned_email=theirs["email"],
        identifiers_choice=Prints.CHOSEN,
    )
    cv.pinned_identifiers.add(theirs["identifier"])
    nothing_of_theirs_is_printed(cv)
    details = rendering.contact_details(user, CV.objects.get(pk=cv.pk))
    assert details["phone"] == details["linkedin_url"] == details["email"] == ""
    assert details["identifiers"] == []

    CV.objects.filter(pk=cv.pk).update(pinned_phone=recruiters_number)
    nothing_of_theirs_is_printed(cv)

    # And nothing that reads the choice back says whose rows they are.
    (entry,) = export_module.build_document(user)["documents"]["cvs"]
    said = json.dumps(entry)
    for hidden in (THEIR_NUMBER, THEIR_LINK, THEIR_ADDRESS, THEIR_ORCID, RECRUITER):
        assert hidden not in said, hidden
    assert entry["prints"]["phone"] == {"choice": "none"}
    assert entry["prints"]["identifiers"] == {"choice": "chosen", "rows": []}


def test_the_api_does_not_echo_a_pointer_at_somebody_elses_row(client, user, cv, theirs):
    CV.objects.filter(pk=cv.pk).update(phone_choice=Prints.CHOSEN, pinned_phone=theirs["phone"])
    cv.pinned_identifiers.add(theirs["identifier"])
    CV.objects.filter(pk=cv.pk).update(identifiers_choice=Prints.CHOSEN)
    body = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user)).json()["prints"]
    assert body["phone"] == {"choice": "chosen", "id": None, "printed": "", "offered": []}
    assert body["identifiers"]["ids"] == [] and body["identifiers"]["printed"] == []


# ------------------------------------------------------------ a messaging handle (#682)

THEIR_HANDLE = "@sam-private:example.org"


@pytest.fixture
def their_handle(other_user):
    from postulo.core.models import MessagingHandle

    return MessagingHandle.objects.create(
        owner=other_user,
        holder=other_user.profile,
        service="matrix",
        handle=THEIR_HANDLE,
        is_primary=True,
    )


@pytest.fixture
def a_contacts_handle(user):
    """A handle this account recorded for somebody else: owned by it, and not its own."""
    from postulo.core.models import MessagingHandle
    from postulo.jobs.models import Company, Contact

    contact = Contact.objects.create(
        owner=user, company=Company.objects.create(owner=user, name="Aperture"), name="Cave"
    )
    return MessagingHandle.objects.create(
        owner=user, holder=contact, service="xmpp", handle="cave@aperture.example", is_primary=True
    )


def test_another_accounts_handle_cannot_be_pinned_on_the_page(client, user, cv, their_handle):
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]), posted(prints_messaging=str(their_handle.pk))
    )

    assert response.status_code == 200
    assert "prints_messaging" in response.context["form"].errors
    assert THEIR_HANDLE not in response.content.decode()
    untouched(cv)
    with pytest.raises(printing.NotOffered):
        printing.choose(cv, "messaging", Prints.CHOSEN, their_handle.pk)


def test_a_contacts_handle_is_not_one_of_the_owners_own(client, user, cv, a_contacts_handle):
    client.force_login(user)
    response = client.post(
        reverse("documents:cv_update", args=[cv.pk]),
        posted(prints_messaging=str(a_contacts_handle.pk)),
    )

    assert response.status_code == 200
    assert "prints_messaging" in response.context["form"].errors
    untouched(cv)


def test_the_api_refuses_another_accounts_handle_as_it_refuses_one_that_does_not_exist(
    client, user, cv, their_handle
):
    token = bearer(user, "write")
    refused = patch(
        client, cv, {"prints": {"messaging": {"choice": "chosen", "id": their_handle.pk}}}, **token
    )
    missing = patch(
        client, cv, {"prints": {"messaging": {"choice": "chosen", "id": 987654321}}}, **token
    )

    assert refused.status_code == missing.status_code == 422
    assert refused.json()["detail"] == missing.json()["detail"], "one answer for both"
    assert THEIR_HANDLE not in refused.content.decode()
    untouched(cv)


def test_an_id_that_reached_the_column_another_way_prints_nothing(
    user, cv, their_handle, a_contacts_handle
):
    for row in (their_handle, a_contacts_handle):
        CV.objects.filter(pk=cv.pk).update(messaging_choice="chosen", pinned_messaging=row)
        loaded = CV.objects.get(pk=cv.pk)
        assert rendering.contact_details(user, loaded)["messaging"] == ""
        html = rendering.render_cv_html(loaded) + rendering.cv_text(loaded)
        assert THEIR_HANDLE not in html and "cave@aperture.example" not in html


def test_the_list_offers_only_the_callers_own_handles(client, user, cv, their_handle):
    from postulo.core.models import MessagingHandle

    own = MessagingHandle.objects.create(
        owner=user, holder=user.profile, service="signal", handle="mine.42"
    )
    response = client.get(f"/api/v1/cvs/{cv.pk}", **bearer(user, "read"))

    offered = response.json()["prints"]["messaging"]["offered"]
    assert [one["id"] for one in offered] == [own.pk]
    assert THEIR_HANDLE not in response.content.decode()
