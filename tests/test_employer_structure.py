"""An employer is a tree, and an application attaches to any part of it (#138).

> one internal plugin that answer a open issue (company hierarquy)
> parent company > child company > departement > person of contact
> any job application be be attached to any of the four

Two of the three edges shipped already — a company inside a company (#55), a department
inside a company (#137). What is left is the attachment, the switch, and the question the
tree makes every count answer.

**The decision this issue turns on** is which of three shapes the attachment takes, and the
plugin toggle settles it. Under *the finer attachment enriches the existing one*, the
posting keeps its company and that column stays required, so switching the feature off
leaves every application with an employer. Under *the employer link itself becomes
polymorphic*, switching it off would leave applications attached to departments with no
company to fall back to — a toggle that breaks a page. Most of what is asserted here is
that consequence.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.jobs import structure
from postulo.jobs.models import Company, Contact, Department, JobPosting
from postulo.plugins.employer_structure import EMPLOYER_STRUCTURE

pytestmark = pytest.mark.django_db


def switch_off(person) -> None:
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.update_or_create(
        plugin=EMPLOYER_STRUCTURE,
        person=person,
        defaults={"state": PluginPolicy.State.FORCED_OFF},
    )


def a_group(user):
    """Alphabet > Google > Google Ireland, and a team inside the middle one."""
    top = Company.objects.create(owner=user, name="Alphabet")
    middle = Company.objects.create(owner=user, name="Google", parent=top)
    leaf = Company.objects.create(owner=user, name="Google Ireland", parent=middle)
    team = Department.objects.create(owner=user, company=middle, name="Engineering")
    return top, middle, leaf, team


def an_application(user, company, **kwargs):
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED, **kwargs)


# ------------------------------------------------------- three edges, not one tree


def test_the_three_edges_are_three_fields(user):
    """Modelling ownership, internal structure and membership as one recursive parent would
    let somebody make a company the child of a person.
    """
    _top, middle, leaf, team = a_group(user)

    assert leaf.parent == middle, "ownership: company to company"
    assert team.company == middle, "internal structure: company to department"
    contact = Contact.objects.create(owner=user, name="Ada", company=middle, department=team)
    assert contact.department == team, "membership: department to person"


def test_ownership_nests_and_the_other_two_do_not(user):
    """An ownership chain is arbitrarily deep; a department is not inside a department."""
    _top, _middle, leaf, _team = a_group(user)

    assert leaf.group.name == "Alphabet"
    assert not hasattr(Department, "parent")


def test_the_group_is_every_company_in_the_tree(user):
    top, middle, leaf, _team = a_group(user)

    names = {company.name for company in leaf.group_members()}

    assert names == {top.name, middle.name, leaf.name}


# --------------------------------------------- the attachment, and why it is shaped so


def test_an_application_can_name_the_part_of_the_employer_it_was_for(user):
    _top, _middle, leaf, team = a_group(user)

    application = an_application(user, leaf, department=team)

    assert application.department == team


def test_the_posting_still_holds_the_employer(user):
    """Enriching rather than replacing: one source of truth for who this was for."""
    _top, _middle, leaf, team = a_group(user)

    application = an_application(user, leaf, department=team)

    assert application.company == leaf
    assert Application._meta.get_field("posting").null is False


def test_a_department_anywhere_in_the_group_is_allowed(user):
    """An application through the Irish arm can be for the group's engineering team, and
    refusing that would make the tree decorative.
    """
    _top, _middle, leaf, team = a_group(user)

    application = an_application(user, leaf)
    application.department = team
    application.full_clean(exclude=["owner"])


def test_a_department_at_another_employer_is_refused_by_name(user):
    _top, _middle, leaf, _team = a_group(user)
    stranger = Company.objects.create(owner=user, name="Black Mesa")
    elsewhere = Department.objects.create(owner=user, company=stranger, name="Research")

    application = an_application(user, leaf)
    application.department = elsewhere

    with pytest.raises(ValidationError) as refused:
        application.full_clean(exclude=["owner"])
    assert "Black Mesa" in str(refused.value), "and it says which employer it is at"


def test_a_department_going_away_leaves_the_attempt_where_it_is(user):
    _top, _middle, leaf, team = a_group(user)
    application = an_application(user, leaf, department=team)

    team.delete()
    application.refresh_from_db()

    assert application.department is None
    assert application.company == leaf, "the employer of record was never in doubt"


def test_the_group_is_reachable_from_the_application(user):
    _top, _middle, leaf, _team = a_group(user)

    assert an_application(user, leaf).group.name == "Alphabet"


# -------------------------------------------------------- switching it off, safely


def test_off_leaves_every_application_with_an_employer(user):
    """The whole argument for shape 1, asserted rather than described.

    Under a polymorphic employer link this is where the page would break: applications
    attached to a department, and no company to fall back to.
    """
    _top, _middle, leaf, team = a_group(user)
    application = an_application(user, leaf, department=team)
    switch_off(user)

    assert structure.group_of(application.company, user) == leaf
    assert application.company == leaf


def test_off_deletes_nothing(user):
    _top, middle, leaf, team = a_group(user)
    application = an_application(user, leaf, department=team)
    switch_off(user)

    application.refresh_from_db()
    leaf.refresh_from_db()
    assert application.department == team, "still on the row, just not offered"
    assert leaf.parent == middle
    assert Department.objects.filter(pk=team.pk).exists()


def test_off_shows_one_company_and_nothing_else(user):
    _top, middle, leaf, _team = a_group(user)
    switch_off(user)

    assert structure.parent_of(leaf, user) is None
    assert structure.children_of(middle, user) == []
    assert structure.group_of(leaf, user) == leaf, "which is what every count always meant"
    assert not structure.in_a_group(leaf, user)


def test_off_offers_no_department_to_name(user):
    _top, _middle, leaf, _team = a_group(user)
    switch_off(user)

    assert not structure.departments_for(leaf, user).exists()


def test_on_by_default(user):
    """An upgrade takes nothing away from anybody; switching it off is a decision."""
    assert structure.structure_allowed(user)


def test_it_is_a_feature_plugin():
    from postulo.plugins import registry

    names = {plugin.name for plugin in registry.plugins("feature")}

    assert EMPLOYER_STRUCTURE in names


# ------------------------------------------------- counting the group or the node


def test_a_count_means_this_company_unless_asked_otherwise(user):
    """Changing what a number means without being asked would be the worse half of the same
    mistake the hierarchy is meant to fix.
    """
    _top, _middle, leaf, _team = a_group(user)

    assert structure.companies_counted(leaf, user) == [leaf.pk]


def test_across_the_group_counts_the_whole_tree(user):
    top, middle, leaf, _team = a_group(user)

    counted = set(structure.companies_counted(leaf, user, across=structure.ACROSS_GROUP))

    assert counted == {top.pk, middle.pk, leaf.pk}


def test_across_the_group_means_nothing_with_the_feature_off(user):
    _top, _middle, leaf, _team = a_group(user)
    switch_off(user)

    assert structure.companies_counted(leaf, user, across=structure.ACROSS_GROUP) == [leaf.pk]


def test_the_company_page_offers_the_other_reading(client, user):
    _top, middle, leaf, _team = a_group(user)
    an_application(user, middle)
    client.force_login(user)

    html = client.get(reverse("jobs:company_detail", args=[leaf.pk])).content.decode()

    assert "across=group" in html, "a hierarchy that keeps counting leaves changes nothing"


def test_the_company_page_says_which_reading_it_is_showing(client, user):
    _top, middle, leaf, _team = a_group(user)
    an_application(user, middle)
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[leaf.pk]), {"across": "group"})

    assert page.context["showing_group"] is True
    assert "Google" in page.content.decode()
    assert len(page.context["postings"]) == 1, "the posting is at the parent, and shows"


def test_a_company_in_no_group_is_offered_no_second_reading(client, user):
    alone = Company.objects.create(owner=user, name="Aperture Science")
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[alone.pk]))

    assert page.context["in_a_group"] is False


def test_the_page_defaults_to_this_company(client, user):
    _top, middle, leaf, _team = a_group(user)
    an_application(user, middle)
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[leaf.pk]))

    assert page.context["showing_group"] is False
    assert len(page.context["postings"]) == 0


# ---------------------------------------------------------------- in the table


def test_the_table_can_show_which_company_a_row_is_part_of(client, user):
    a_group(user)
    from postulo.core import tables

    tables.save_settings(
        user, "companies", {"columns": ["name", "parent"], "page_size": 50, "widths": {}}
    )
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    assert "Part of" in html
    assert "group=Google" in html, "and it narrows to that whole tree"


def test_narrowing_to_a_group_finds_the_grandchildren_too(client, user):
    """A walk rather than a join: an ownership chain is arbitrarily deep, so a lookup would
    be ten outer joins on every row of every page.
    """
    a_group(user)
    Company.objects.create(owner=user, name="Black Mesa")
    client.force_login(user)

    page = client.get(reverse("jobs:company_list"), {"group": "Alphabet"})

    names = {company.name for company in page.context["companies"]}
    assert names == {"Alphabet", "Google", "Google Ireland"}


def test_a_group_nobody_has_narrows_to_nothing(client, user):
    """A filter that silently does not apply is worse than one that shows an empty page."""
    a_group(user)
    client.force_login(user)

    page = client.get(reverse("jobs:company_list"), {"group": "Umbrella"})

    assert len(page.context["companies"]) == 0


def test_narrowing_to_a_group_does_nothing_with_the_feature_off(client, user):
    a_group(user)
    switch_off(user)
    client.force_login(user)

    page = client.get(reverse("jobs:company_list"), {"group": "Alphabet"})

    assert len(page.context["companies"]) == 3, "every company, because there is no group"


# ------------------------------------------------------------ what is offered


def test_the_company_form_drops_the_parent_when_it_is_off(user):
    from postulo.jobs.forms import CompanyForm

    switch_off(user)

    assert "parent" not in CompanyForm(user=user).fields


def test_the_contact_form_drops_the_department_when_it_is_off(user):
    from postulo.jobs.forms import ContactForm

    switch_off(user)

    assert "new_department" not in ContactForm(user=user).fields


def test_the_application_form_offers_a_department_only_where_there_is_one(user):
    from postulo.applications.forms import ApplicationForm

    _top, _middle, leaf, _team = a_group(user)
    application = an_application(user, leaf)

    assert "department" in ApplicationForm(user=user, instance=application).fields

    bare = an_application(user, Company.objects.create(owner=user, name="Black Mesa"))
    assert "department" not in ApplicationForm(user=user, instance=bare).fields


def test_the_application_form_drops_the_department_when_it_is_off(user):
    from postulo.applications.forms import ApplicationForm

    _top, _middle, leaf, _team = a_group(user)
    application = an_application(user, leaf)
    switch_off(user)

    assert "department" not in ApplicationForm(user=user, instance=application).fields


def test_one_person_never_sees_another_employer(user, other_user):
    a_group(other_user)

    assert not structure.departments_for(
        Company.objects.create(owner=user, name="Alphabet"), user
    ).exists()


# ------------------------------------------------------------- through the archive


def test_the_archive_carries_the_attachment_and_restores_it(user, other_user):
    """By name and by the company holding it: an id means nothing in another instance, and
    a department may live at a different company in the same group.
    """
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    _top, _middle, leaf, team = a_group(user)
    an_application(user, leaf, department=team)

    archive = zipfile.ZipFile(export_module.write_archive(user))
    document = json.loads(archive.read(export_module.MANIFEST_NAME))
    assert document["postulo"]["format"] == 13
    importer.load(other_user, archive)

    restored = Application.objects.for_user(other_user).get()
    assert restored.department is not None
    assert restored.department.name == "Engineering"
    assert restored.department.company.name == "Google", "at the company it belonged to"
    assert restored.posting.company.name == "Google Ireland", "and the employer is unchanged"


def test_a_team_nobody_is_recorded_at_survives_the_archive(user, other_user):
    """A department used to travel only as a name beside a contact, so a team with nobody
    in it was silently dropped -- and *a team you applied to before you knew anybody there*
    is exactly the ordinary case the model was written for (#137). Found by the attachment
    above failing to restore.
    """
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    company = Company.objects.create(owner=user, name="Aperture Science")
    Department.objects.create(owner=user, company=company, name="Enrichment")
    assert not Contact.objects.for_user(user).exists(), "nobody is recorded there"

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))

    assert Department.objects.for_user(other_user).get().name == "Enrichment"


def test_an_archive_written_before_this_still_imports(user):
    """Every earlier format still imports; an application simply has no department."""
    import io
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    document = {
        "postulo": {"format": 12},
        "companies": [
            {
                "id": 1,
                "name": "Aperture Science",
                "postings": [
                    {
                        "id": 1,
                        "title": "Research Engineer",
                        "applications": [{"id": 1, "status": "applied"}],
                    }
                ],
            }
        ],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    importer.load(user, zipfile.ZipFile(buffer))

    assert Application.objects.for_user(user).get().department is None
