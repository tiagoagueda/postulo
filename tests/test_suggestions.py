"""The boxes that are typed every time offer what this person already recorded (#261).

`get_or_create_company` matches on `name__iexact`, which catches *Acme*, *acme* and *ACME*
and nothing else: `Acme Ltd` and `ACME Inc.` become two employers, silently, at the moment
somebody is busy doing something else. A list of what they have already typed works before
the record exists rather than reconciling two afterwards.

The isolation test here is the one that matters. A suggestion list built from the instance
rather than the account is the classic shape of a leak — type `a`, learn every employer
everybody is applying to — and it is invisible until somebody looks for it.
"""

from __future__ import annotations

import pytest
from django import forms
from django.urls import reverse
from django.utils import translation

from postulo.applications.forms import ApplicationIntakeForm, PostingIntakeForm
from postulo.applications.models import Application, Priority, Status
from postulo.jobs import esco, recall
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

#: The least an intake form takes, minus the company the test under it is about.
INTAKE = {"title": "Engineer", "status": Status.APPLIED, "priority": Priority.NORMAL}


def a_posting(user, company_name="Aperture Science", *, location="Cambridge", source="LinkedIn"):
    company, _made = Company.objects.get_or_create(owner=user, name=company_name)
    return JobPosting.objects.create(
        owner=user,
        company=company,
        title="Test Engineer",
        location=location,
        source=source,
    )


# ------------------------------------------------------------------ what is offered


def test_what_has_been_recorded_comes_back(user):
    a_posting(user)

    assert recall.companies(user) == ["Aperture Science"]
    assert recall.locations(user) == ["Cambridge"]
    assert recall.sources(user) == ["LinkedIn"]


def test_the_commonest_answer_comes_first(user):
    """The list is cut at a limit, so the order decides what survives the cut."""
    for _ in range(3):
        a_posting(user, "Black Mesa", location="Lyon", source="A friend")
    a_posting(user, "Aperture Science", location="Cambridge", source="LinkedIn")

    assert recall.companies(user)[0] == "Black Mesa"
    assert recall.locations(user)[0] == "Lyon"
    assert recall.sources(user)[0] == "A friend"


def test_a_company_with_no_posting_yet_is_still_offered(user):
    """It is on the list because somebody typed it, which is the whole signal here."""
    Company.objects.create(owner=user, name="Black Mesa")

    assert recall.companies(user) == ["Black Mesa"]


def test_nothing_recorded_offers_nothing(user):
    assert recall.companies(user) == []
    assert recall.locations(user) == []
    assert recall.sources(user) == []


def test_a_blank_is_not_a_suggestion(user):
    """`location` and `source` are optional, so most postings have none."""
    a_posting(user, location="", source="")

    assert recall.locations(user) == []
    assert recall.sources(user) == []


def test_the_same_answer_is_offered_once(user):
    a_posting(user, "Aperture Science", location="Cambridge")
    a_posting(user, "Aperture Science", location="Cambridge")

    assert recall.companies(user) == ["Aperture Science"]
    assert recall.locations(user) == ["Cambridge"]


def test_the_list_is_cut_rather_than_growing_without_end(user, monkeypatch):
    """A few hundred names is a few kilobytes; past that an endpoint is the answer."""
    monkeypatch.setattr(recall, "AT_MOST", 3)
    for number in range(6):
        a_posting(user, f"Company {number}", location=f"Town {number}")

    assert len(recall.companies(user)) == 3
    assert len(recall.locations(user)) == 3


# ------------------------------------------------------------------- and to nobody else


def test_one_person_is_never_offered_anothers_records(user, other_user):
    """The whole reason this test file exists."""
    a_posting(other_user, "Black Mesa", location="Lyon", source="A friend")
    a_posting(user, "Aperture Science", location="Cambridge", source="LinkedIn")

    assert recall.companies(user) == ["Aperture Science"]
    assert recall.locations(user) == ["Cambridge"]
    assert recall.sources(user) == ["LinkedIn"]


def test_a_form_with_nobody_attached_offers_nothing_of_theirs_rather_than_everything(user):
    """The safe way round, for a route that forgets to pass the person.

    The title's list is the classification, not this person's records, so it is the one
    thing an unattached form is still allowed to offer.
    """
    a_posting(user)

    datalists = PostingIntakeForm().datalists
    assert "company-suggestions" not in datalists
    assert "location-suggestions" not in datalists
    assert "source-suggestions" not in datalists
    assert datalists["title-suggestions"]


def test_the_title_offers_the_classification_not_this_persons_records(user, other_user):
    """What the title offers is the ESCO unit groups, the same for everyone (#266)."""
    a_posting(user, "Aperture Science", location="Cambridge", source="LinkedIn")

    mine = PostingIntakeForm(user=user).datalists["title-suggestions"]
    theirs = PostingIntakeForm(user=other_user).datalists["title-suggestions"]

    assert mine == theirs, "the classification is not this person's records"
    assert "Software developers" in mine, "the unit groups, in code order"
    # Unit groups, not occupations: the level a report can say.
    assert all(esco.code_for(name) for name in mine)


def test_the_title_list_is_read_in_the_language_read(user):
    with translation.override("fr"):
        offered = PostingIntakeForm(user=user).datalists["title-suggestions"]

    assert "Concepteurs de logiciels" in offered


# ----------------------------------------------------------------- on the page itself


def test_the_form_declares_a_list_for_each_of_the_four(user):
    form = PostingIntakeForm(user=user)

    for name, expected in (
        ("company_name", "company-suggestions"),
        ("title", "title-suggestions"),
        ("location", "location-suggestions"),
        ("source", "source-suggestions"),
    ):
        attrs = form.fields[name].widget.attrs
        assert attrs["list"] == expected
        # The browser's own memory of what was typed into a box with this name *on any
        # site* is a different list and a worse one; it would sit on top of this one.
        assert attrs["autocomplete"] == "off"


def test_the_field_component_draws_the_list_beside_the_box(user, client):
    """One place draws it, so intake, capture review and the listing form all have it."""
    a_posting(user, "Aperture Science", location="Cambridge", source="LinkedIn")
    client.force_login(user)

    page = client.get(reverse("applications:create")).content.decode()

    assert '<datalist id="company-suggestions">' in page
    assert '<option value="Aperture Science">' in page
    assert '<option value="Cambridge">' in page
    assert '<option value="LinkedIn">' in page
    # The title's list is the classification, which belongs to no one and is always on.
    assert '<datalist id="title-suggestions">' in page
    assert '<option value="Software developers">' in page


def test_the_listing_form_and_the_capture_review_get_it_too(user, client):
    """They render the same form through the same component and were never touched."""
    a_posting(user, "Aperture Science")
    client.force_login(user)

    listing = client.get(reverse("listings:create")).content.decode()

    assert '<datalist id="company-suggestions">' in listing
    assert '<option value="Aperture Science">' in listing


def test_a_page_with_nothing_recorded_draws_no_empty_list(user, client):
    """An empty `<datalist>` is a control that does nothing, which is worse than none.

    The title's list is the classification, which belongs to no one and is never empty, so
    it is the one list a page with nothing recorded may still carry.
    """
    client.force_login(user)

    page = client.get(reverse("applications:create")).content.decode()

    assert '<datalist id="company-suggestions">' not in page
    assert '<datalist id="location-suggestions">' not in page
    assert '<datalist id="source-suggestions">' not in page
    assert '<datalist id="title-suggestions">' in page


def test_offering_never_becomes_requiring(user, client):
    """A company nobody has recorded is what a new application usually is."""
    a_posting(user, "Aperture Science")
    client.force_login(user)

    form = ApplicationIntakeForm(
        INTAKE | {"company_name": "Somewhere Entirely New"},
        user=user,
    )

    assert form.is_valid(), form.errors
    assert isinstance(form.fields["company_name"], forms.CharField), "a text box, not a select"


def test_a_company_typed_that_was_never_offered_is_recorded_as_typed(user, client):
    client.force_login(user)

    client.post(
        reverse("applications:create"),
        INTAKE | {"company_name": "Somewhere Entirely New"},
    )

    assert Application.objects.for_user(user).count() == 1
    assert Company.objects.for_user(user).get().name == "Somewhere Entirely New"
