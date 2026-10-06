"""An experience names its organisation as a company (#683).

The entry keeps its own text, and links to a company of the person's beside it. What is
held to here: the link is found or made by name and never reaches another account; a
company only the career added stays out of the places a company is picked for new work
until something is attached to it; and a merge or a delete does not lose the career.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.applications.models import Application
from postulo.jobs import merging, recall
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.resume.forms import ExperienceForm
from postulo.resume.models import Experience

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
