"""Two records of one company, or of one person, made into one (#239).

`tests/test_duplicates.py` holds the noticing. This holds what happens when somebody
agrees, and the four things the merge promises:

* **it shows what will move before it moves anything**, and the page that shows it changes
  nothing;
* **it discards nothing without saying so** -- everything that can hang off a record moves,
  and where both records fill a field that holds one value, the kept record's wins and the
  other's is on the page and in the note the merge leaves;
* **it writes on the timeline** of every application it touches;
* **it is one transaction, the person's own, and a POST** -- a merge that fails half way
  did not happen, somebody else's record is a 404, and a link cannot delete anything.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import (
    Application,
    EventKind,
    Interview,
    InterviewKind,
    Status,
)
from postulo.applications.services import change_status
from postulo.core.models import PhoneNumber, PostalAddress, WebLink
from postulo.jobs import merging
from postulo.jobs.models import (
    Company,
    CompanyIdentifier,
    CompanyKind,
    CompanyLogo,
    Contact,
    Department,
    Industry,
    JobPosting,
)

pytestmark = pytest.mark.django_db


def company(owner, name, **fields) -> Company:
    return Company.objects.create(owner=owner, name=name, **fields)


def contact(owner, name, **fields) -> Contact:
    return Contact.objects.create(owner=owner, name=name, **fields)


def applied(owner, employer, title="Test Engineer", **fields) -> Application:
    posting = JobPosting.objects.create(owner=owner, company=employer, title=title)
    application = Application.objects.create(
        owner=owner, posting=posting, status=Status.DRAFT, **fields
    )
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=3))
    return application


def merged_entries(application) -> list:
    return [entry for entry in application.events.all() if "was merged into" in entry.summary]


def company_page(kept, other=None) -> str:
    url = reverse("jobs:company_merge", args=[kept.pk])
    return f"{url}?with={other.pk}" if other is not None else url


def contact_page(kept, other=None) -> str:
    url = reverse("jobs:contact_merge", args=[kept.pk])
    return f"{url}?with={other.pk}" if other is not None else url


@pytest.fixture
def acme(user):
    """The one that is kept: a name, and little else."""
    return company(user, "Acme")


@pytest.fixture
def limited(user):
    """The one merged into it, holding one of everything a company can hold."""
    limited = company(
        user,
        "Acme Ltd",
        website="https://acme.example",
        careers_url="https://acme.example/jobs",
        location="Leeds",
        notes="Met them at the fair.",
    )
    limited.industries.set(Industry.named(user, ["Robotics"]))
    CompanyIdentifier.objects.create(owner=user, company=limited, scheme="wikidata", value="Q95")
    Department.objects.create(owner=user, company=limited, name="Engineering")
    return limited


# ----------------------------------------------------------- what a company's merge moves


def test_everything_the_other_company_held_moves(user, acme, limited):
    team = limited.departments.get()
    person = contact(user, "Cave Johnson", company=limited, department=team)
    application = applied(user, limited, department=team)
    child = company(user, "Acme Robotics", parent=limited)
    client_of = applied(user, company(user, "Black Mesa"), "Physicist", through_agency=limited)

    merging.merge_companies(acme, limited)

    assert not Company.objects.filter(pk=limited.pk).exists(), "the other is gone"
    acme.refresh_from_db()
    assert [posting.title for posting in acme.postings.all()] == ["Test Engineer"]
    application.refresh_from_db()
    assert application.posting.company == acme
    person.refresh_from_db()
    assert person.company == acme and person.department == team
    team.refresh_from_db()
    assert team.company == acme
    assert application.department == team
    assert [str(row) for row in acme.identifiers.all()] == ["Wikidata: Q95"]
    assert [industry.name for industry in acme.industries.all()] == ["Robotics"]
    child.refresh_from_db()
    assert child.parent == acme
    client_of.refresh_from_db()
    assert client_of.through_agency == acme
    assert client_of.posting.company.name == "Black Mesa", "whose employer it is has not moved"


def test_the_kept_company_takes_what_it_had_nothing_in(user, acme, limited):
    merging.merge_companies(acme, limited)

    acme.refresh_from_db()
    assert acme.name == "Acme", "the name that was kept is the name"
    assert acme.website == "https://acme.example"
    assert acme.careers_url == "https://acme.example/jobs"
    assert acme.location == "Leeds"


def test_where_both_say_something_the_kept_one_wins_and_the_other_is_written_down(user):
    kept = company(
        user, "Acme", website="https://acme.example", location="London", notes="The good one."
    )
    other = company(
        user,
        "Acme Ltd",
        website="https://acme.co.uk",
        location="Leeds",
        notes="Met them at the fair.",
        kind=CompanyKind.EMPLOYMENT_SERVICE,
    )
    CompanyIdentifier.objects.create(owner=user, company=kept, scheme="wikidata", value="Q95")
    CompanyIdentifier.objects.create(owner=user, company=other, scheme="wikidata", value="Q96")

    plan = merging.merge_companies(kept, other)

    kept.refresh_from_db()
    assert (kept.website, kept.location) == ("https://acme.example", "London")
    assert kept.kind == CompanyKind.EMPLOYER
    assert [row.value for row in kept.identifiers.all()] == ["Q95"]
    differed = {row.label: (row.kept, row.other) for row in plan.differences}
    assert differed == {
        "Website": ("https://acme.example", "https://acme.co.uk"),
        "Location": ("London", "Leeds"),
        "Type": ("Employer", "Public employment service"),
        "Wikidata": ("Q95", "Q96"),
    }
    for line in (
        "The good one.",
        "Merged with Acme Ltd on ",
        "Website: https://acme.co.uk",
        "Location: Leeds",
        "Type: Public employment service",
        "Wikidata: Q96",
        "Met them at the fair.",
    ):
        assert line in kept.notes, line
    assert kept.notes.index("The good one.") < kept.notes.index("Merged with Acme Ltd")


def test_the_note_is_written_even_where_nothing_differed(user, acme):
    """A company nobody applied to has no timeline, so the record itself has to say that
    another was merged into it."""
    other = company(user, "Acme Ltd")

    merging.merge_companies(acme, other)

    acme.refresh_from_db()
    assert acme.notes.startswith("Merged with Acme Ltd on ")


def test_departments_of_one_name_become_one(user, acme, limited):
    theirs = limited.departments.get()
    ours = Department.objects.create(owner=user, company=acme, name="engineering")
    person = contact(user, "Cave Johnson", company=limited, department=theirs)
    application = applied(user, limited, department=theirs)

    merging.merge_companies(acme, limited)

    assert list(acme.departments.all()) == [ours], "one team, not two of one name"
    person.refresh_from_db()
    application.refresh_from_db()
    assert person.department == ours and application.department == ours


def test_an_identifier_both_carry_is_not_doubled(user, acme, limited):
    for holder in (acme, limited):
        CompanyIdentifier.objects.create(
            owner=user, company=holder, scheme="other", label="NIPC", value="500 123 456"
        )

    plan = merging.merge_companies(acme, limited)

    assert sorted(str(row) for row in acme.identifiers.all()) == [
        "NIPC: 500 123 456",
        "Wikidata: Q95",
    ]
    assert not [row for row in plan.differences if row.label == "NIPC"], "the same is no difference"


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def logo_of(company, data: bytes) -> None:
    from postulo.jobs import logos

    logos.store(company, ContentFile(data), source="upload")


def test_the_logo_goes_with_the_company_that_had_one(user, acme, limited):
    logo_of(limited, PNG + b"theirs")
    row = CompanyLogo.objects.get(company=limited)

    plan = merging.merge_companies(acme, limited)

    acme.refresh_from_db()
    assert acme.has_logo and acme.logo_source == "upload"
    assert CompanyLogo.objects.get(company=acme).pk == row.pk, "the row moved, nothing was copied"
    assert CompanyLogo.objects.count() == 1
    assert "Logo" in {row.label for row in plan.fills}


def test_a_logo_the_kept_company_has_no_room_for_is_said_and_then_gone(user, acme, limited):
    logo_of(acme, PNG + b"ours")
    logo_of(limited, PNG + b"theirs")

    plan = merging.merge_companies(acme, limited)

    acme.refresh_from_db()
    assert bytes(CompanyLogo.objects.get(company=acme).data).endswith(b"ours")
    assert CompanyLogo.objects.count() == 1, "one logo row, and the other's went with its company"
    assert any("Its logo" in line for line in plan.left_behind)
    assert "Its logo" in acme.notes


def test_the_place_goes_with_the_words_for_it(user, acme):
    from postulo.jobs.models import LocationSource

    other = company(user, "Acme Ltd", location="Springfield")
    Company.objects.filter(pk=other.pk).update(
        location_lat=39.8,
        location_lon=-89.6,
        location_resolved_from="Springfield, Illinois",
        location_resolved_by=LocationSource.MANUAL,
    )
    other.refresh_from_db()

    merging.merge_companies(acme, other)

    acme.refresh_from_db()
    assert acme.location == "Springfield"
    assert (acme.location_lat, acme.location_lon) == (39.8, -89.6)
    assert acme.location_resolved_by == LocationSource.MANUAL, "a correction is not guessed again"


# --------------------------------------------------------------- the ownership tree


def test_the_kept_company_takes_the_others_place_in_a_group(user, acme):
    group = company(user, "Acme Group")
    other = company(user, "Acme Ltd", parent=group)

    plan = merging.merge_companies(acme, other)

    acme.refresh_from_db()
    assert acme.parent == group
    assert ("Part of", "Acme Group") in {(row.label, row.value) for row in plan.fills}


def test_a_company_merged_with_the_one_it_was_part_of(user):
    group = company(user, "Acme Group")
    parent = company(user, "Acme Ltd", parent=group)
    kept = company(user, "Acme", parent=parent)
    sibling = company(user, "Acme Robotics", parent=parent)

    merging.merge_companies(kept, parent)

    kept.refresh_from_db()
    sibling.refresh_from_db()
    assert kept.parent == group, "it is what its parent was part of, and not part of itself"
    assert sibling.parent == kept


def test_a_merge_never_makes_a_company_part_of_itself(user):
    """The other is two levels above the kept one. What sat between them takes the other's
    place rather than going under the company it is above."""
    top = company(user, "Acme Holdings")
    middle = company(user, "Acme Europe", parent=top)
    kept = company(user, "Acme Lisbon", parent=middle)

    merging.merge_companies(kept, top)

    kept.refresh_from_db()
    middle.refresh_from_db()
    assert kept.parent == middle and middle.parent is None
    kept.full_clean(exclude=["owner"])
    assert kept.group == middle, "and the chain still ends"


def test_a_parent_the_kept_company_cannot_take_is_written_down(user):
    ours = company(user, "Our Group")
    theirs = company(user, "Their Group")
    kept = company(user, "Acme", parent=ours)
    other = company(user, "Acme Ltd", parent=theirs)

    plan = merging.merge_companies(kept, other)

    kept.refresh_from_db()
    assert kept.parent == ours
    assert ("Part of", "Our Group", "Their Group") in {
        (row.label, row.kept, row.other) for row in plan.differences
    }
    assert "Part of: Their Group" in kept.notes


# ------------------------------------------------------------------- the timeline


def test_every_application_it_touches_says_so_on_its_timeline(user, acme, limited):
    theirs = applied(user, limited)
    through = applied(user, company(user, "Black Mesa"), "Physicist", through_agency=limited)
    ours = applied(user, acme, "Untouched")

    plan = merging.merge_companies(acme, limited)

    assert plan.applications == 2
    [entry] = merged_entries(theirs)
    assert entry.kind == EventKind.NOTE
    assert entry.summary == "Acme Ltd was merged into Acme"
    assert entry.body == "It was the employer on the posting."
    assert entry.actor == "", "the person did it themselves"
    assert merged_entries(through)[0].body == "It was the agency this went through."
    assert merged_entries(ours) == [], "and one it did not touch says nothing"


def test_an_application_touched_twice_has_one_entry_saying_both(user, acme, limited):
    """Its employer and the agency it went through were the same record."""
    application = applied(user, limited, through_agency=limited)

    merging.merge_companies(acme, limited)

    [entry] = merged_entries(application)
    assert entry.body == "It was the employer on the posting. It was the agency this went through."


def test_what_changed_is_stamped_so_a_client_catching_up_is_told(user, acme, limited):
    application = applied(user, limited)
    posting = application.posting
    before = timezone.now() - dt.timedelta(days=1)
    Application.objects.filter(pk=application.pk).update(updated_at=before)
    JobPosting.objects.filter(pk=posting.pk).update(updated_at=before)

    merging.merge_companies(acme, limited)

    application.refresh_from_db()
    posting.refresh_from_db()
    assert application.updated_at > before and posting.updated_at > before


# --------------------------------------------------- one transaction, and the owner's own


def test_a_merge_that_fails_did_not_happen(user, acme, limited, monkeypatch):
    application = applied(user, limited)
    person = contact(user, "Cave Johnson", company=limited)

    def fall_over(plan):
        raise RuntimeError("the last step")

    monkeypatch.setattr(merging, "_write_entries", fall_over)
    with pytest.raises(RuntimeError):
        merging.merge_companies(acme, limited)

    assert Company.objects.filter(pk=limited.pk).exists()
    application.refresh_from_db()
    person.refresh_from_db()
    acme.refresh_from_db()
    assert application.posting.company == limited and person.company == limited
    assert acme.website == "" and acme.notes == ""
    assert limited.identifiers.count() == 1 and limited.departments.count() == 1


def test_a_merge_stops_rather_than_delete_what_it_cannot_move(user, acme, limited, monkeypatch):
    """Django is asked what would go with the other record, and anything the merge did not
    expect to find there stops it: here, a posting it was prevented from moving."""
    application = applied(user, limited)
    moved = JobPosting.objects.filter

    def move_nothing(*args, **kwargs):
        return moved(*args, **kwargs).none() if "company" in kwargs else moved(*args, **kwargs)

    monkeypatch.setattr(JobPosting.objects, "filter", move_nothing)
    with pytest.raises(merging.CannotMerge) as refusal:
        merging.merge_companies(acme, limited)
    monkeypatch.undo()

    # The posting, and everything that would have gone with it.
    assert refusal.value.what == ["applications", "events", "job postings"]
    assert refusal.value.why == (
        "Nothing was merged: Postulo does not know how to move the "
        "applications, events, job postings it holds."
    )
    assert Company.objects.filter(pk=limited.pk).exists()
    application.refresh_from_db()
    assert application.posting.company == limited
    assert not merged_entries(application), "undone whole, the entries with it"


def test_two_records_of_two_people_are_never_merged(user, other_user, acme):
    theirs = company(other_user, "Acme Ltd")

    with pytest.raises(ValueError, match="same person"):
        merging.plan_companies(acme, theirs)
    with pytest.raises(ValueError, match="same person"):
        merging.merge_companies(acme, theirs)

    assert Company.objects.filter(pk=theirs.pk).exists()


def test_a_record_is_not_merged_with_itself(user, acme):
    with pytest.raises(ValueError, match="two different"):
        merging.merge_companies(acme, acme)
    assert Company.objects.filter(pk=acme.pk).exists()


def test_a_company_is_not_merged_with_a_person(user, acme):
    with pytest.raises(ValueError, match="one kind"):
        merging.plan_companies(acme, contact(user, "Cave Johnson"))


# ----------------------------------------------------------------------- the page


def test_the_page_asks_which_one_and_offers_what_was_noticed_first(client, user, acme, limited):
    company(user, "Black Mesa")
    client.force_login(user)

    page = client.get(company_page(acme)).content.decode()
    page = page[page.index("<main") : page.index("</main>")]

    assert "Merge companies" in page
    assert "data-possible-duplicates" in page
    assert f'href="{company_page(acme, limited)}"' in page
    assert 'name="with"' in page, "and any other of theirs can be chosen"
    options = page[page.index('name="with"') :]
    assert "Black Mesa" in options and "Acme Ltd" in options
    assert f'value="{acme.pk}"' not in options, "but not the company itself"
    assert 'method="get"' in page and 'method="post"' not in page


def test_the_list_offers_only_the_persons_own(client, user, other_user, acme):
    company(other_user, "Somebody Else's Employer")
    client.force_login(user)

    page = client.get(company_page(acme)).content.decode()

    assert "Somebody Else" not in page
    assert "There is nothing else to merge it with." in page


def test_the_page_shows_what_would_move_before_anything_does(client, user, acme, limited):
    contact(user, "Cave Johnson", company=limited)
    applied(user, limited)
    Company.objects.filter(pk=acme.pk).update(location="London")
    client.force_login(user)

    page = " ".join(client.get(company_page(acme, limited)).content.decode().split())

    kept = page[page.index("data-kept") : page.index("data-other")]
    assert "Acme" in kept and "Acme Ltd" not in kept
    assert "Acme Ltd" in page[page.index("data-other") :]
    moves = page[page.index("data-merge-moves") : page.index("data-merge-fills")]
    for line in (
        "Postings",
        "Test Engineer",
        "People",
        "Cave Johnson",
        "Departments",
        "Engineering",
        "Identifiers",
        "Wikidata: Q95",
        "Industries",
        "Robotics",
    ):
        assert line in moves, line
    fills = page[page.index("data-merge-fills") : page.index("data-merge-differences")]
    assert "Website" in fills and "https://acme.example" in fills
    differences = page[page.index("data-merge-differences") : page.index("data-merge-written")]
    assert "Location" in differences and "London" in differences and "Leeds" in differences
    assert "1 application gets a line on its timeline" in page
    assert 'method="post"' in page and f'name="with" value="{limited.pk}"' in page
    assert f'href="{company_page(limited, acme)}"' in page, "and the two can be exchanged"


def test_looking_at_the_page_changes_nothing(client, user, acme, limited):
    application = applied(user, limited)
    client.force_login(user)

    for address in (company_page(acme), company_page(acme, limited)):
        assert client.get(address).status_code == 200

    assert Company.objects.filter(pk=limited.pk).exists()
    acme.refresh_from_db()
    application.refresh_from_db()
    assert acme.website == "" and acme.notes == ""
    assert application.posting.company == limited
    assert not merged_entries(application)


def test_the_merge_is_the_form_at_the_foot_of_the_page(client, user, acme, limited):
    application = applied(user, limited)
    client.force_login(user)

    response = client.post(company_page(acme), {"with": limited.pk})

    assert response.status_code == 302
    assert response["Location"] == acme.get_absolute_url()
    assert not Company.objects.filter(pk=limited.pk).exists()
    application.refresh_from_db()
    assert application.posting.company == acme
    [said] = [str(message) for message in get_messages(response.wsgi_request)]
    assert said == (
        "Acme Ltd has been merged into Acme. 1 application has a line on its timeline saying so."
    )


def test_a_post_that_names_nothing_merges_nothing(client, user, acme, limited):
    client.force_login(user)

    assert client.post(company_page(acme)).status_code == 404
    assert client.post(company_page(acme), {"with": "everything"}).status_code == 404
    assert client.post(company_page(acme), {"with": acme.pk}).status_code == 404
    assert Company.objects.for_user(user).count() == 2


def test_the_page_needs_somebody_signed_in(client, acme, limited):
    response = client.post(company_page(acme), {"with": limited.pk})

    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
    assert Company.objects.filter(pk=limited.pk).exists()


def test_a_forged_request_merges_nothing(user, acme, limited):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)

    response = strict.post(company_page(acme), {"with": limited.pk})

    assert response.status_code == 403
    assert Company.objects.filter(pk=limited.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_somebody_elses_company_cannot_be_merged_into_mine(client, user, other_user, acme, method):
    theirs = company(other_user, "Acme Ltd")
    applied(other_user, theirs)
    client.force_login(user)

    if method == "get":
        response = client.get(company_page(acme, theirs))
    else:
        response = client.post(company_page(acme), {"with": theirs.pk})

    assert response.status_code == 404, "and never a 403, which would say it exists"
    assert Company.objects.filter(pk=theirs.pk).exists()
    assert theirs.postings.count() == 1
    assert not acme.postings.exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_mine_cannot_be_merged_into_somebody_elses(client, user, other_user, acme, method):
    theirs = company(other_user, "Acme Ltd")
    client.force_login(user)

    if method == "get":
        response = client.get(company_page(theirs, acme))
    else:
        response = client.post(company_page(theirs), {"with": acme.pk})

    assert response.status_code == 404
    assert Company.objects.filter(pk=acme.pk).exists()


def test_a_merge_that_is_refused_says_why_and_leads_back(client, user, acme, limited, monkeypatch):
    def refuse(kept, other):
        raise merging.CannotMerge("Nothing was merged: and this is why.")

    monkeypatch.setattr(merging, "merge_companies", refuse)
    client.force_login(user)

    response = client.post(company_page(acme), {"with": limited.pk})

    assert response["Location"] == company_page(acme, limited)
    [said] = [str(message) for message in get_messages(response.wsgi_request)]
    assert said == "Nothing was merged: and this is why."
    assert Company.objects.filter(pk=limited.pk).exists()


# ------------------------------------------------------------------------ people


@pytest.fixture
def cave(user):
    """The one that is kept."""
    return contact(user, "Cave Johnson")


@pytest.fixture
def twin(user):
    """The one merged into him, with one of everything a person can hold."""
    here = company(user, "Aperture Science")
    team = Department.objects.create(owner=user, company=here, name="Management")
    twin = contact(
        user,
        "C. Johnson",
        company=here,
        department=team,
        role="Founder",
        email="cave@aperture.example",
        notes="Likes lemons.",
    )
    PhoneNumber.objects.create(owner=user, holder=twin, number="+351912345678", is_primary=True)
    PostalAddress.objects.create(
        owner=user, holder=twin, street="Rua do Exemplo 1", municipality="Lisboa", country="PT"
    )
    WebLink.objects.create(
        owner=user,
        holder=twin,
        kind=WebLink.Kind.SOCIAL,
        url="https://social.example/cave",
        is_primary=True,
    )
    return twin


def test_everything_the_other_person_held_moves(user, cave, twin):
    employer = twin.company
    main = applied(user, employer, contact=twin)
    referred = applied(user, company(user, "Black Mesa"), "Physicist", referred_by=twin)
    starts = timezone.now() + dt.timedelta(days=2)
    interview = Interview.objects.create(
        owner=user,
        application=main,
        kind=InterviewKind.VIDEO,
        starts_at=starts,
        ends_at=starts + dt.timedelta(hours=1),
    )
    interview.contacts.set([twin])

    merging.merge_contacts(cave, twin)

    assert not Contact.objects.filter(pk=twin.pk).exists()
    cave.refresh_from_db()
    assert [row.number for row in cave.phone_numbers.all()] == ["+351912345678"]
    assert cave.phone_numbers.get().is_primary, "his only number, so it stays the first"
    assert [row.municipality for row in cave.postal_addresses.all()] == ["Lisboa"]
    assert [row.url for row in cave.web_links.all()] == ["https://social.example/cave"]
    main.refresh_from_db()
    referred.refresh_from_db()
    assert main.contact == cave and referred.referred_by == cave
    assert list(interview.contacts.all()) == [cave]
    assert (cave.role, cave.email) == ("Founder", "cave@aperture.example")
    assert cave.company == employer and cave.department.name == "Management"
    assert "Merged with C. Johnson (Aperture Science) on " in cave.notes
    assert "Likes lemons." in cave.notes


def test_the_kept_persons_own_stay_the_first_of_each(user, cave, twin):
    PhoneNumber.objects.create(owner=user, holder=cave, number="+351911111111", is_primary=True)
    WebLink.objects.create(
        owner=user,
        holder=cave,
        kind=WebLink.Kind.SOCIAL,
        url="https://social.example/cavejohnson",
        is_primary=True,
    )
    WebLink.objects.create(
        owner=user,
        holder=twin,
        kind=WebLink.Kind.WEBSITE,
        url="https://cave.example",
        is_primary=True,
    )

    merging.merge_contacts(cave, twin)

    numbers = {row.number: row.is_primary for row in cave.phone_numbers.all()}
    assert numbers == {"+351911111111": True, "+351912345678": False}
    links = {row.url: row.is_primary for row in cave.web_links.all()}
    assert links == {
        "https://social.example/cavejohnson": True,
        "https://social.example/cave": False,
        "https://cave.example": True,
    }, "one first of each kind, and a kind he had none of keeps the one it came with"


def test_a_link_both_list_is_listed_once(user, cave, twin):
    WebLink.objects.create(
        owner=user, holder=cave, kind=WebLink.Kind.SOCIAL, url="https://social.example/cave"
    )

    plan = merging.merge_contacts(cave, twin)

    assert [row.url for row in cave.web_links.all()] == ["https://social.example/cave"]
    assert not [move for move in plan.moves if move.label == "Web links"]
    assert not WebLink.objects.filter(object_id=twin.pk).exists()


def test_an_interview_both_were_at_has_him_once(user, cave, twin):
    application = applied(user, twin.company)
    starts = timezone.now() + dt.timedelta(days=2)
    interview = Interview.objects.create(
        owner=user,
        application=application,
        kind=InterviewKind.PANEL,
        starts_at=starts,
        ends_at=starts + dt.timedelta(hours=1),
    )
    interview.contacts.set([cave, twin])

    merging.merge_contacts(cave, twin)

    assert list(interview.contacts.all()) == [cave]
    [entry] = merged_entries(application)
    assert entry.body == "They were at an interview."


def test_where_both_say_something_about_a_person_the_kept_one_wins(user, twin):
    elsewhere = company(user, "Black Mesa")
    kept = contact(
        user, "Cave Johnson", company=elsewhere, role="Chairman", email="cave@blackmesa.example"
    )

    plan = merging.merge_contacts(kept, twin)

    kept.refresh_from_db()
    assert (kept.role, kept.email, kept.company) == (
        "Chairman",
        "cave@blackmesa.example",
        elsewhere,
    )
    assert kept.department is None, "a team at another company is not his"
    differed = {row.label: (row.kept, row.other) for row in plan.differences}
    assert differed == {
        "Role": ("Chairman", "Founder"),
        "Email address": ("cave@blackmesa.example", "cave@aperture.example"),
        "Company": ("Black Mesa", "Aperture Science · Management"),
    }
    for line in (
        "Role: Founder",
        "Email address: cave@aperture.example",
        "Company: Aperture Science · Management",
    ):
        assert line in kept.notes, line


def test_the_same_address_in_another_case_is_no_difference(user, twin):
    kept = contact(user, "Cave Johnson", email="Cave@Aperture.example")

    plan = merging.merge_contacts(kept, twin)

    assert "Email address" not in {row.label for row in plan.differences}


def test_a_person_at_the_same_company_takes_the_team(user, twin):
    kept = contact(user, "Cave Johnson", company=twin.company)

    merging.merge_contacts(kept, twin)

    kept.refresh_from_db()
    assert kept.department.name == "Management"
    kept.full_clean(exclude=["owner"])


def test_the_timelines_say_what_the_person_was_to_each(user, cave, twin):
    both = applied(user, twin.company, contact=twin, referred_by=twin)
    neither = applied(user, twin.company, "Untouched")

    plan = merging.merge_contacts(cave, twin)

    assert plan.applications == 1
    [entry] = merged_entries(both)
    assert entry.summary == "C. Johnson (Aperture Science) was merged into Cave Johnson"
    assert entry.body == "They were the main contact. They referred you."
    assert not merged_entries(neither)


def test_a_merge_of_people_that_fails_did_not_happen(user, cave, twin, monkeypatch):
    application = applied(user, twin.company, contact=twin)

    def fall_over(plan):
        raise RuntimeError("the last step")

    monkeypatch.setattr(merging, "_write_entries", fall_over)
    with pytest.raises(RuntimeError):
        merging.merge_contacts(cave, twin)

    assert Contact.objects.filter(pk=twin.pk).exists()
    application.refresh_from_db()
    cave.refresh_from_db()
    assert application.contact == twin
    assert twin.phone_numbers.count() == 1 and not cave.phone_numbers.exists()
    assert cave.notes == "" and cave.email == ""


def test_what_a_plugin_holds_about_them_is_said_before_the_merge(user, cave, twin, monkeypatch):
    """A plugin that owns a table says what it holds about somebody, as it does for the
    archive. Postulo cannot move those rows, and says so rather than finding out."""
    from postulo.plugins import registry

    class Keeper:
        name = "keeper"
        label = "The keeper"
        kind = "feature"
        owns_models = ("core.Tag",)

        def export_for(self, subject):
            return [{"about": subject.name}] if subject.pk == twin.pk else []

    monkeypatch.setattr(
        registry, "plugins", lambda kind, **kwargs: [Keeper()] if kind == "feature" else []
    )

    about_twin = merging.plan_contacts(cave, twin)
    about_cave = merging.plan_contacts(twin, cave)

    assert about_twin.left_behind == [
        "What The keeper holds about them, which Postulo cannot move."
    ]
    assert about_cave.left_behind == []


def test_the_contacts_page_shows_what_would_move(client, user, cave, twin):
    applied(user, twin.company, contact=twin)
    client.force_login(user)

    page = " ".join(client.get(contact_page(cave, twin)).content.decode().split())

    assert "Merge contacts" in page
    moves = page[page.index("data-merge-moves") : page.index("data-merge-fills")]
    for line in (
        "Telephone numbers",
        "+351 912 345 678",
        "Postal addresses",
        "Lisboa",
        "Web links",
        "https://social.example/cave",
        "Applications they are the main contact for",
    ):
        assert line in moves, line
    fills = page[page.index("data-merge-fills") : page.index("data-merge-written")]
    for line in ("Role", "Founder", "Email address", "Company", "Aperture Science", "Department"):
        assert line in fills, line
    assert f'href="{contact_page(twin, cave)}"' in page


def test_merging_people_from_the_page(client, user, cave, twin):
    client.force_login(user)

    response = client.post(contact_page(cave), {"with": twin.pk})

    assert response.status_code == 302
    assert response["Location"] == reverse("jobs:contact_update", args=[cave.pk])
    assert not Contact.objects.filter(pk=twin.pk).exists()
    [said] = [str(message) for message in get_messages(response.wsgi_request)]
    assert said == "C. Johnson (Aperture Science) has been merged into Cave Johnson."


@pytest.mark.parametrize("method", ["get", "post"])
def test_somebody_elses_contact_cannot_be_merged_into_mine(client, user, other_user, cave, method):
    theirs = contact(other_user, "Cave Johnson", email="cave@aperture.example")
    client.force_login(user)

    if method == "get":
        response = client.get(contact_page(cave, theirs))
    else:
        response = client.post(contact_page(cave), {"with": theirs.pk})

    assert response.status_code == 404
    assert Contact.objects.filter(pk=theirs.pk).exists()
    cave.refresh_from_db()
    assert cave.email == ""


def test_the_figures_follow_a_merge(user, acme, limited):
    """They are kept until something they read changes, and a merge changes what they read."""
    from postulo.applications import analytics

    applied(user, company(user, "Black Mesa"), "Physicist", through_agency=limited)
    before = analytics.insights_for(user)
    assert [row.name for row in before.by_agency] == ["Acme Ltd"]

    merging.merge_companies(acme, limited)

    assert [row.name for row in analytics.insights_for(user).by_agency] == ["Acme"]
