"""The rest of the fields that suggest, what a choice fills, and who may be asked (#1251)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from postulo.applications.forms import PostingIntakeForm
from postulo.jobs.forms import CompanyForm, ContactForm
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.resume.forms import (
    CertificationForm,
    CourseForm,
    EducationForm,
    ExperienceForm,
    HonourForm,
    MembershipForm,
)
from postulo.resume.models import Education

pytestmark = pytest.mark.django_db


def ask(client, kind, typed=""):
    return client.get(reverse("core:suggest", args=[kind]), {"q": typed})


def kind_of(form, name):
    html = str(form[name])
    start = html.index('data-suggest="') + len('data-suggest="')
    return html[start : html.index('"', start)].rstrip("/").rsplit("/", 1)[-1]


def test_the_listed_fields_each_name_a_kind(user):
    for form, name, kind in [
        (EducationForm(user=user), "institution", "school"),
        (EducationForm(user=user), "qualification", "qualification"),
        (EducationForm(user=user), "field_of_study", "field-of-study"),
        (EducationForm(user=user), "location", "location"),
        (ExperienceForm(user=user), "role", "job-title"),
        (ExperienceForm(user=user), "location", "location"),
        (CertificationForm(user=user), "issuer", "awarding-body"),
        (CourseForm(user=user), "provider", "place-of-learning"),
        (HonourForm(user=user), "awarded_by", "awarding-body"),
        (MembershipForm(user=user), "organisation", "membership-organisation"),
        (CompanyForm(user=user), "name", "employer"),
        (ContactForm(user=user), "name", "contact-name"),
        (PostingIntakeForm(user=user), "company_name", "company"),
    ]:
        assert kind_of(form, name) == kind, (type(form).__name__, name)


def test_the_new_kinds_answer_with_the_persons_own_entries(client, user):
    firm = Company.objects.create(owner=user, name="Seaside College")
    Education.objects.create(
        owner=user,
        institution="Seaside College",
        qualification="Master of Arts",
        field_of_study="Linguistics",
    )
    Contact.objects.create(owner=user, company=firm, name="Ada Lovelace")
    client.force_login(user)

    for kind, typed, wanted in [
        ("school", "sea", "Seaside College"),
        ("qualification", "mas", "Master of Arts"),
        ("field-of-study", "ling", "Linguistics"),
        ("contact-name", "ada", "Ada Lovelace"),
        ("awarding-body", "sea", "Seaside College"),
    ]:
        assert f'data-value="{wanted}"' in ask(client, kind, typed).content.decode(), kind


def test_the_new_kinds_never_offer_another_persons_entries(client, user, other_user):
    firm = Company.objects.create(owner=other_user, name="Secret College")
    Education.objects.create(
        owner=other_user, institution="Secret College", qualification="Secret Degree"
    )
    Contact.objects.create(owner=other_user, company=firm, name="Secret Person")
    client.force_login(user)

    for kind in (
        "school",
        "qualification",
        "field-of-study",
        "contact-name",
        "awarding-body",
        "place-of-learning",
        "membership-organisation",
    ):
        body = ask(client, kind, "").content.decode().lower()
        assert "secret" not in body, kind


# ------------------------------------------------------------------ what a choice fills


def fill(client, kind, value):
    return client.get(reverse("core:suggest_fill", args=[kind]), {"value": value})


def test_choosing_a_company_fills_its_known_location_and_careers_page(client, user):
    firm = Company.objects.create(
        owner=user, name="Aperture Science", careers_url="https://aperture.example/jobs"
    )
    JobPosting.objects.create(owner=user, company=firm, title="Old", location="Lyon")
    JobPosting.objects.create(owner=user, company=firm, title="New", location="Cambridge")
    client.force_login(user)

    answer = fill(client, "company", "aperture science").json()

    assert answer == {
        "fills": {"location": "Cambridge", "careers_url": "https://aperture.example/jobs"}
    }
    assert fill(client, "company", "aperture science")["Cache-Control"] == "private, no-store"


def test_choosing_a_title_fills_the_usual_employment_type(client, user):
    firm = Company.objects.create(owner=user, name="Black Mesa")
    for kind in ("contract", "contract", "full_time", ""):
        JobPosting.objects.create(
            owner=user, company=firm, title="Test Engineer", employment_type=kind
        )
    client.force_login(user)

    assert fill(client, "job-title", "test engineer").json() == {
        "fills": {"employment_type": "contract"}
    }
    assert fill(client, "job-title", "Nobody").json() == {"fills": {}}


def test_choosing_a_town_fills_its_postcode_and_country(client, user):
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(
        holder=user, owner=user, municipality="Lyon", postcode="69001", country="FR"
    )
    client.force_login(user)

    assert fill(client, "municipality", "lyon").json() == {
        "fills": {"postcode": "69001", "country": "FR"}
    }


def test_a_fill_never_carries_another_persons_records(client, user, other_user):
    from postulo.core.models import PostalAddress

    firm = Company.objects.create(
        owner=other_user, name="Secret Inc", careers_url="https://secret.example"
    )
    JobPosting.objects.create(
        owner=other_user,
        company=firm,
        title="Secret Job",
        location="Secretville",
        employment_type="contract",
    )
    PostalAddress.objects.create(
        holder=other_user,
        owner=other_user,
        municipality="Secretville",
        postcode="99999",
        country="FR",
    )
    client.force_login(user)

    for kind, value in [
        ("company", "Secret Inc"),
        ("employer", "Secret Inc"),
        ("job-title", "Secret Job"),
        ("municipality", "Secretville"),
    ]:
        assert fill(client, kind, value).json() == {"fills": {}}, kind


def test_a_fill_is_for_known_kinds_signed_in_people_and_within_the_rate(client, user):
    assert fill(client, "company", "x").status_code == 302
    client.force_login(user)
    assert fill(client, "nope", "x").status_code == 404
    with override_settings(POSTULO_SUGGESTION_RATE="2/m"):
        cache.clear()
        codes = [fill(client, "company", "x").status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes


def test_a_box_that_fills_names_where_and_what_to_say(user):
    intake = str(PostingIntakeForm(user=user)["company_name"])

    assert 'data-suggest-fills="{&quot;location&quot;: &quot;location&quot;}"' in intake
    assert reverse("core:suggest_fill", args=["company"]) in intake
    assert "data-fill-undo" in intake


def test_the_posting_title_fills_the_employment_type(user):
    assert '"employment_type": "employment_type"' in str(
        PostingIntakeForm(user=user)["title"]
    ).replace("&quot;", '"')


def test_the_address_town_fills_the_postcode_and_country(user):
    from postulo.core.postal import PostalAddressForm

    html = str(PostalAddressForm(person=user)["municipality"]).replace("&quot;", '"')

    assert reverse("core:suggest", args=["municipality"]) in html
    assert '"postcode": "postcode"' in html and '"country": "country"' in html
    assert "maxlength" in html


# ------------------------------------------------------------ lookups and the privacy rule


def test_a_lookup_kind_answers_nothing_until_it_is_enabled(client, user):
    from postulo.core import suggest

    asked = []

    def source(who, typed):
        asked.append(typed)
        return ["Third Party Ltd"]

    on = {"value": False}
    suggest.register("test-lookup", source, enabled=lambda who: on["value"])
    try:
        client.force_login(user)
        assert "Third Party" not in ask(client, "test-lookup", "thi").content.decode()
        assert asked == []
        on["value"] = True
        assert "Third Party" in ask(client, "test-lookup", "thi").content.decode()
        assert asked == ["thi"]
    finally:
        suggest._registry.pop("test-lookup", None)


def test_a_switch_that_cannot_be_read_is_off(user):
    from postulo.core import suggest

    def broken(who):
        raise RuntimeError("no setting")

    suggest.register("test-lookup", lambda who, typed: ["x"], enabled=broken)
    try:
        assert suggest.suggest("test-lookup", user, "x") == []
    finally:
        suggest._registry.pop("test-lookup", None)


def test_a_fill_that_fails_is_an_empty_answer(user):
    from postulo.core import suggest

    def broken(who, chosen):
        raise RuntimeError("boom")

    suggest.register("test-fill", lambda who, typed: [], fills=broken)
    try:
        assert suggest.fills("test-fill", user, "x") == {}
    finally:
        suggest._registry.pop("test-fill", None)


def test_what_a_group_the_person_belongs_to_owns_is_offered_and_no_other_groups(
    client, user, other_user
):
    from django.contrib.auth.models import Group

    mine = Group.objects.create(name="Household")
    mine.user_set.add(user, other_user)
    theirs = Group.objects.create(name="Elsewhere")
    theirs.user_set.add(other_user)
    for group, name in [(mine, "Shared Household Ltd"), (theirs, "Hidden Elsewhere Ltd")]:
        company = Company(name=name)
        company.set_principal(group)
        company.save()
    client.force_login(user)

    body = ask(client, "employer", "").content.decode()

    assert "Shared Household Ltd" in body
    assert "Hidden Elsewhere Ltd" not in body


def test_an_enabled_lookup_is_still_within_the_account_rate(client, user):
    from postulo.core import suggest

    calls = []
    suggest.register(
        "test-lookup", lambda who, typed: calls.append(typed) or [], enabled=lambda who: True
    )
    try:
        client.force_login(user)
        with override_settings(POSTULO_SUGGESTION_RATE="2/m"):
            cache.clear()
            codes = [ask(client, "test-lookup", "x").status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and 429 in codes
        assert len(calls) == 2  # a refused question never reached the lookup
    finally:
        suggest._registry.pop("test-lookup", None)
