"""A company may belong to another, and what that must never allow.

The relation is one nullable field. Everything interesting is in what it refuses: a company
that is its own parent, a chain that closes into a loop, and a chain long enough that walking
it becomes the page's problem. Each of those is cheap to write by accident through a form and
impossible to see afterwards, which is why they are refused with a sentence rather than a
constraint nobody can read.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.jobs.forms import CompanyForm
from postulo.jobs.models import Company

pytestmark = pytest.mark.django_db


@pytest.fixture
def group(user):
    """Alphabet owns Google owns Google Ireland — three deep, which real ones are."""
    top = Company.objects.create(owner=user, name="Alphabet")
    middle = Company.objects.create(owner=user, name="Google", parent=top)
    bottom = Company.objects.create(owner=user, name="Google Ireland", parent=middle)
    return top, middle, bottom


# --------------------------------------------------------------- what it refuses


def test_a_company_cannot_be_part_of_itself(user):
    company = Company.objects.create(owner=user, name="Aperture")
    company.parent = company

    with pytest.raises(ValidationError) as refused:
        company.clean()

    assert "part of itself" in str(refused.value)


def test_a_loop_is_refused_and_the_message_says_which_link_closes_it(group):
    """ "Not allowed" leaves somebody reading a list of subsidiaries guessing."""
    top, _middle, bottom = group
    top.parent = bottom

    with pytest.raises(ValidationError) as refused:
        top.clean()

    assert "Google Ireland" in str(refused.value), "the message names the company"


def test_a_chain_deeper_than_the_cap_is_refused(user):
    parent = None
    for step in range(Company.MAX_DEPTH + 1):
        parent = Company.objects.create(owner=user, name=f"Level {step}", parent=parent)

    deepest = Company(owner=user, name="One too many", parent=parent)
    with pytest.raises(ValidationError) as refused:
        deepest.clean()

    assert "deep" in str(refused.value)


def test_no_parent_is_the_normal_case(user):
    company = Company(owner=user, name="Aperture")
    company.clean()  # raises nothing


# ------------------------------------------------------------ what it answers


def test_the_group_is_the_company_at_the_top(group):
    top, middle, bottom = group

    assert bottom.group == top
    assert middle.group == top
    assert top.group == top, "a company with no parent is its own group"


def test_descendants_are_everything_underneath(group):
    top, middle, bottom = group

    assert {c.name for c in top.descendants()} == {"Google", "Google Ireland"}
    assert [c.name for c in middle.descendants()] == ["Google Ireland"]
    assert bottom.descendants() == []


def test_a_loop_written_straight_into_the_database_does_not_hang_a_page(group):
    """`clean` cannot run on a `QuerySet.update`, so the walk protects itself."""
    top, _middle, bottom = group
    Company.objects.filter(pk=top.pk).update(parent=bottom)

    assert Company.objects.get(pk=bottom.pk).group is not None, "it returns rather than looping"


def test_removing_a_parent_leaves_its_children_alone(group):
    top, middle, _bottom = group
    top.delete()

    middle.refresh_from_db()
    assert middle.parent is None, "SET_NULL: a holding company going does not take the group"
    assert Company.objects.for_user(middle.owner).count() == 2


# ------------------------------------------------------------------- the form


def test_the_form_does_not_offer_a_company_its_own_subtree(group, user):
    top, middle, bottom = group

    offered = {c.name for c in CompanyForm(user=user, instance=top).fields["parent"].queryset}

    assert offered == set(), "everything else is already under it"
    assert "Google" not in offered and "Google Ireland" not in offered
    assert bottom.pk and middle.pk


def test_the_form_offers_the_others(user):
    Company.objects.create(owner=user, name="Aperture")
    black_mesa = Company.objects.create(owner=user, name="Black Mesa")

    offered = {
        c.name for c in CompanyForm(user=user, instance=black_mesa).fields["parent"].queryset
    }

    assert offered == {"Aperture"}


def test_one_persons_companies_are_never_offered_to_another(user, other_user):
    Company.objects.create(owner=other_user, name="Theirs")
    mine = Company.objects.create(owner=user, name="Mine")

    offered = list(CompanyForm(user=user, instance=mine).fields["parent"].queryset)

    assert offered == []


# ------------------------------------------------------------------ the page


def test_the_page_names_the_parent_and_lists_the_children(client, group, user):
    top, middle, _bottom = group
    client.force_login(user)

    parent_page = client.get(reverse("jobs:company_detail", args=[top.pk])).content.decode()
    child_page = client.get(reverse("jobs:company_detail", args=[middle.pk])).content.decode()

    assert "Part of this company" in parent_page
    assert "Google" in parent_page, "its subsidiary is listed"
    assert "Part of" in child_page and "Alphabet" in child_page


def test_nothing_is_inherited(group):
    """#55's first open question, answered: a subsidiary keeps its own everything."""
    top, middle, _bottom = group
    from postulo.jobs.models import Industry

    top.industries.add(*Industry.named(top.owner, ["Software"]))
    top.location = "Mountain View"
    top.save(update_fields=["location"])

    assert list(middle.industries.all()) == []
    assert middle.location == ""


# ---------------------------------------------------------------- the archive


def _round_trip(from_user, to_user):
    """Export one account and import it into another, the way a person moving would."""
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    buffer = export_module.write_archive(from_user)
    archive = zipfile.ZipFile(buffer)
    document = json.loads(archive.read(export_module.MANIFEST_NAME))
    importer.load(to_user, archive)
    return document


def test_the_tree_survives_an_export_and_an_import(user, other_user, group):
    document = _round_trip(user, other_user)

    by_name = {c["name"]: c for c in document["companies"]}
    assert by_name["Google"]["parent"] == "Alphabet", "written by name, not by a local id"
    assert by_name["Alphabet"]["parent"] == ""

    landed = {c.name: c for c in Company.objects.for_user(other_user).select_related("parent")}
    assert landed["Google"].parent == landed["Alphabet"]
    assert landed["Google Ireland"].parent == landed["Google"]
    assert landed["Alphabet"].parent is None


def test_a_parent_named_later_in_the_file_is_still_resolved(user, other_user):
    """A child can be read before its parent exists, which is why it is a second pass."""
    parent = Company.objects.create(owner=user, name="Zeta Holdings")
    Company.objects.create(owner=user, name="Alpha Systems", parent=parent)

    _round_trip(user, other_user)

    landed = {c.name: c for c in Company.objects.for_user(other_user).select_related("parent")}
    assert landed["Alpha Systems"].parent == landed["Zeta Holdings"]


def test_an_archive_from_before_this_still_reads(user, other_user, group):
    """Format 4 named no parent at all, and an archive written yesterday is an archive."""
    import io
    import json
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    document = json.loads(
        zipfile.ZipFile(export_module.write_archive(user)).read(export_module.MANIFEST_NAME)
    )
    document["postulo"]["format"] = 4
    for company in document["companies"]:
        del company["parent"]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as older:
        older.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    landed = Company.objects.for_user(other_user)
    assert landed.count() == 3, "every company still arrives"
    assert not landed.exclude(parent=None).exists(), "and none of them claims a parent"
