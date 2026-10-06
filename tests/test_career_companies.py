"""An experience names its organisation as a company (#683).

The entry keeps its own text, and links to a company of the person's beside it. What is
held to here: the link is found or made by name and never reaches another account; a
company only the career added stays out of the places a company is picked for new work
until something is attached to it; and a merge or a delete does not lose the career.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import translation

from postulo.applications.models import Application
from postulo.jobs import industries, merging, recall, roles
from postulo.jobs.models import Company, Contact, Industry, JobPosting
from postulo.resume.forms import EducationForm, ExperienceForm
from postulo.resume.models import Education, Experience

pytestmark = pytest.mark.django_db


def save_entry(user, organisation: str, **extra) -> Experience:
    data = {
        "role": "Engineer",
        "organisation": organisation,
        "start_date": "2020-01-01",
        **extra,
    }
    form = ExperienceForm(data, user=user)
    assert form.is_valid(), form.errors
    form.instance.owner = user
    return form.save()


def test_an_existing_company_is_linked_by_name_in_any_case(user):
    acme = Company.objects.create(owner=user, name="Acme")
    entry = save_entry(user, "ACME")
    assert entry.company == acme
    assert entry.organisation == "ACME"
    acme.refresh_from_db()
    assert acme.from_career is False


def test_a_company_that_does_not_exist_is_added_and_marked(user):
    entry = save_entry(user, "Weyland-Yutani")
    assert entry.company.name == "Weyland-Yutani"
    assert entry.company.owner == user
    assert entry.company.from_career is True


def test_another_accounts_company_is_never_linked(user, other_user):
    theirs = Company.objects.create(owner=other_user, name="Initech")
    entry = save_entry(user, "Initech")
    assert entry.company != theirs
    assert entry.company.owner == user
    theirs.refresh_from_db()
    assert theirs.from_career is False


def test_the_form_says_where_the_link_goes(user):
    form = ExperienceForm(user=user)
    assert "added to your companies" in str(form.fields["organisation"].help_text)


def test_the_form_offers_the_companies_including_former_employers(user):
    Company.objects.create(owner=user, name="Acme")
    save_entry(user, "Old Employer Ltd")
    assert "Old Employer Ltd" in ExperienceForm(user=user).datalists["company-suggestions"]


def test_the_entry_page_shows_the_company_as_a_link(client, user):
    entry = save_entry(user, "Acme")
    client.force_login(user)
    page = client.get(reverse("resume:item_update", args=["experience", entry.pk]))
    assert entry.company.get_absolute_url() in page.content.decode()


def test_the_company_page_lists_the_entries_that_name_it(client, user):
    entry = save_entry(user, "Acme", role="Welder")
    client.force_login(user)
    page = client.get(entry.company.get_absolute_url())
    assert "Your history here" in page.content.decode()
    assert "Welder" in page.content.decode()


def test_deleting_the_company_keeps_the_entry_and_its_text(client, user):
    entry = save_entry(user, "Acme")
    client.force_login(user)
    dialog = client.get(reverse("jobs:company_delete", args=[entry.company.pk]))
    assert "1 career entry keeps its text and loses the link." in dialog.content.decode()
    entry.company.delete()
    entry.refresh_from_db()
    assert entry.company is None
    assert entry.organisation == "Acme"


# ---------------------------------------------------------------- the marker


def test_a_posting_a_contact_or_an_agency_application_clears_the_marker(user):
    for how in ("posting", "contact", "agency"):
        company = save_entry(user, f"Former {how}").company
        assert company.from_career
        if how == "posting":
            JobPosting.objects.create(owner=user, company=company, title="Role")
        elif how == "contact":
            Contact.objects.create(owner=user, company=company, name="Ana")
        else:
            employer = Company.objects.create(owner=user, name=f"Employer {how}")
            posting = JobPosting.objects.create(owner=user, company=employer, title="Role")
            Application.objects.create(owner=user, posting=posting, through_agency=company)
        company.refresh_from_db()
        assert company.from_career is False, how


def test_the_pickers_and_suggestions_leave_a_career_company_out(client, user):
    former = save_entry(user, "Former Employer").company
    Company.objects.create(owner=user, name="Current Employer")
    assert "Former Employer" not in recall.companies(user)
    assert "Current Employer" in recall.companies(user)
    assert former not in Company.objects.for_user(user).offered()
    # A row that already names it keeps it.
    assert former in Company.objects.for_user(user).offered(including=former.pk)

    client.force_login(user)
    page = client.get(reverse("jobs:contact_create")).content.decode()
    assert "Former Employer" not in page
    assert "Current Employer" in page


def test_the_posting_picker_keeps_the_company_the_row_names(user):
    from postulo.jobs.forms import JobPostingForm

    former = save_entry(user, "Former Employer").company
    posting = JobPosting.objects.create(owner=user, company=former, title="Role")
    # Attached, so no longer marked; mark it again to see the row's own company stay.
    Company.objects.filter(pk=former.pk).update(from_career=True)
    assert former in JobPostingForm(instance=posting, user=user).fields["company"].queryset
    other = Company.objects.create(owner=user, name="Marked", from_career=True)
    assert other not in JobPostingForm(instance=posting, user=user).fields["company"].queryset


def test_the_table_leaves_a_career_company_out_until_a_filter_asks(client, user):
    save_entry(user, "Former Employer")
    Company.objects.create(owner=user, name="Current Employer")
    client.force_login(user)
    url = reverse("jobs:company_list")
    plain = client.get(url).content.decode()
    assert "Former Employer" not in plain
    assert "Current Employer" in plain
    asked = client.get(url, {"from_career": "1"}).content.decode()
    assert "Former Employer" in asked
    assert "Current Employer" not in asked


def test_the_map_leaves_a_career_company_out(client, user):
    company = save_entry(user, "Former Employer", location="Lisbon").company
    Company.objects.filter(pk=company.pk).update(location="Lisbon")
    client.force_login(user)
    response = client.get(reverse("jobs:company_map"))
    if response.status_code == 200:
        assert "Former Employer" not in response.content.decode()


def test_the_company_form_can_switch_the_marker_off_by_hand(client, user):
    company = save_entry(user, "Former Employer").company
    client.force_login(user)
    page = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    assert 'name="from_career"' in page
    plain = Company.objects.create(owner=user, name="Plain")
    page = client.get(reverse("jobs:company_update", args=[plain.pk])).content.decode()
    assert 'name="from_career"' not in page


# ------------------------------------------------------------------- merging


def test_a_merge_moves_the_entries_and_says_so(client, user):
    kept = Company.objects.create(owner=user, name="Acme")
    other = save_entry(user, "Acme Ltd").company
    entry = Experience.objects.get(company=other)
    plan = merging.plan_companies(kept, other)
    assert any(line.label == "Career entries" for line in plan.moves)
    client.force_login(user)
    page = client.get(reverse("jobs:company_merge", args=[kept.pk]), {"with": other.pk})
    assert "Career entries" in page.content.decode()

    merging.merge_companies(kept, other)
    entry.refresh_from_db()
    assert entry.company == kept
    assert entry.organisation == "Acme Ltd"


def test_a_merge_keeps_the_marker_only_if_both_had_it(user):
    kept = save_entry(user, "One").company
    other = save_entry(user, "Two").company
    merging.merge_companies(kept, other)
    kept.refresh_from_db()
    assert kept.from_career is True

    third = Company.objects.create(owner=user, name="Three")
    marked = save_entry(user, "Four").company
    merging.merge_companies(third, marked)
    third.refresh_from_db()
    assert third.from_career is False


def test_a_merge_clears_the_marker_when_work_moves_in(user):
    kept = save_entry(user, "One").company
    other = save_entry(user, "Two").company
    Company.objects.filter(pk=other.pk).update(from_career=True)
    JobPosting.objects.create(owner=user, company=other, title="Role")
    other.refresh_from_db()
    assert other.from_career is False
    kept.refresh_from_db()
    merging.merge_companies(kept, other)
    kept.refresh_from_db()
    assert kept.from_career is False


# -------------------------------------------------------------------- privacy


def test_nothing_of_the_companys_notes_reaches_a_cv_or_the_entry_text(user):
    company = Company.objects.create(owner=user, name="Acme", notes="They pay late.")
    entry = save_entry(user, "Acme")
    assert entry.company == company
    assert "They pay late." not in entry.organisation
    assert "They pay late." not in str(entry)


# ---------------------------------------------------------------- education (#685)


def save_study(user, institution: str, **extra) -> Education:
    data = {"qualification": "BSc", "institution": institution, **extra}
    form = EducationForm(data, user=user)
    assert form.is_valid(), form.errors
    form.instance.owner = user
    return form.save()


def test_an_education_entry_links_to_the_company_of_that_name_and_gives_it_nothing(user):
    uni = Company.objects.create(owner=user, name="Universidade de Aveiro")
    entry = save_study(user, "universidade de aveiro")
    assert entry.company == uni
    assert entry.institution == "universidade de aveiro"
    assert uni.industries.count() == 0


def test_a_new_institution_is_added_marked_and_given_the_education_industry(user):
    entry = save_study(user, "University of Aveiro")
    company = entry.company
    assert company.from_career is True
    assert [(i.name, i.code) for i in company.industries.all()] == [("Education", "85")]
    assert company in Company.objects.for_user(user).qualifying(roles.PLACE_OF_LEARNING)


def test_the_industry_is_named_in_the_persons_language(user):
    with translation.override("fr"):
        entry = save_study(user, "Université de Lille")
    industry = entry.company.industries.get()
    assert industry.name == industries.name_for("85", "fr")
    assert industry.code == "85"


def test_another_accounts_company_is_never_linked_nor_classified_by_an_education_entry(
    user, other_user
):
    theirs = Company.objects.create(owner=other_user, name="MIT")
    entry = save_study(user, "MIT")
    assert entry.company != theirs
    assert entry.company.owner == user
    assert theirs.industries.count() == 0


def test_an_entry_saved_again_without_touching_its_institution_keeps_its_link(user):
    entry = save_study(user, "Old Name")
    entry.company.name = "New Name"
    entry.company.save()
    form = EducationForm(
        {"qualification": "MSc", "institution": "Old Name"}, instance=entry, user=user
    )
    assert form.is_valid()
    form.save()
    assert Company.objects.for_user(user).count() == 1
    assert form.added_school is None


def test_the_form_offers_the_places_of_learning_first_and_hides_nothing(user):
    Company.objects.create(owner=user, name="Aaa Bank")
    school = Company.objects.create(owner=user, name="Zzz College")
    school.industries.set(Industry.named(user, ["Education"]))
    offered = EducationForm(user=user).datalists["school-suggestions"]
    assert offered[0] == "Zzz College"
    assert "Aaa Bank" in offered


def test_the_education_form_says_where_the_link_goes(user):
    assert "added to your companies" in str(
        EducationForm(user=user).fields["institution"].help_text
    )


def test_the_view_says_a_company_was_added_as_education(client, user):
    client.force_login(user)
    response = client.post(
        reverse("resume:item_create", args=["education"]),
        {"qualification": "BSc", "institution": "University of Aveiro"},
        follow=True,
    )
    assert "added to your companies as Education" in response.content.decode()


def test_the_notice_shows_for_a_company_with_no_education_industry_and_never_blocks(client, user):
    Company.objects.create(owner=user, name="Acme Training")
    entry = save_study(user, "Acme Training")
    client.force_login(user)
    url = reverse("resume:item_update", args=["education", entry.pk])
    page = client.get(url).content.decode()
    assert "is Education, so it is not listed as a school or university" in page
    assert reverse("jobs:company_update", args=[entry.company.pk]) in page
    saved = client.post(url, {"qualification": "BSc", "institution": "Acme Training"})
    assert saved.status_code == 302

    entry.company.industries.set(Industry.named(user, ["Education"]))
    assert "is Education, so it is not" not in client.get(url).content.decode()


def test_the_company_page_lists_the_education_entries_that_name_it(client, user):
    entry = save_study(user, "Acme", qualification="Diploma in Welding")
    client.force_login(user)
    page = client.get(entry.company.get_absolute_url()).content.decode()
    assert "Your education here" in page
    assert "Diploma in Welding" in page


def test_a_merge_moves_education_entries_and_the_delete_dialog_counts_them(client, user):
    kept = Company.objects.create(owner=user, name="Aveiro")
    other = save_study(user, "Aveiro Uni").company
    entry = Education.objects.get(company=other)
    client.force_login(user)
    dialog = client.get(reverse("jobs:company_delete", args=[other.pk]))
    assert "1 career entry keeps its text" in dialog.content.decode()
    plan = merging.plan_companies(kept, other)
    assert any(line.label == "Education entries" for line in plan.moves)
    merging.merge_companies(kept, other)
    entry.refresh_from_db()
    assert entry.company == kept
    assert entry.institution == "Aveiro Uni"


def test_deleting_the_company_keeps_the_education_entry(user):
    entry = save_study(user, "Aveiro Uni")
    entry.company.delete()
    entry.refresh_from_db()
    assert entry.company is None
    assert entry.institution == "Aveiro Uni"


def test_the_migration_links_exact_matches_only(user, other_user):
    import importlib

    from django.apps import apps

    migration = importlib.import_module("postulo.resume.migrations.0016_education_company")
    mine = Company.objects.create(owner=user, name="Aveiro")
    Company.objects.create(owner=other_user, name="Lisbon")
    linked = Education.objects.create(owner=user, qualification="A", institution=" aveiro ")
    stranger = Education.objects.create(owner=user, qualification="B", institution="Lisbon")
    unmatched = Education.objects.create(owner=user, qualification="C", institution="Nowhere")
    before = Company.objects.count()

    migration.link_where_one_company_has_the_name(apps, None)

    for entry in (linked, stranger, unmatched):
        entry.refresh_from_db()
    assert linked.company == mine
    assert stranger.company is None
    assert unmatched.company is None
    assert Company.objects.count() == before
