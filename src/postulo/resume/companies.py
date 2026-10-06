"""The company an experience names as its organisation (#683).

The entry keeps its own text and links to a company of the person's beside it. Three
doors reach the link, and they differ on one point: the career form may add the company,
a file never does. A stranger's file must not fill the list.
"""

from __future__ import annotations

from postulo.core import slugs
from postulo.jobs.models import Company


def existing(owner, name: str):
    """The owner's company with this name, case-insensitively, or None."""
    key = slugs.name_key(name or "")
    if not key:
        return None
    return Company.objects.for_user(owner).filter(name_key=key).first()


def find_or_add(owner, name: str):
    """The owner's company with this name, added -- marked as from the career -- if none.

    Added through the one door every typed name goes through, so a name that exists in
    another capitalisation is that company, and never another owner's.
    """
    from postulo.applications.services import get_or_create_company

    found = existing(owner, name)
    if found is not None:
        return found
    name = slugs.collapse(name or "")[:200]
    if not name:
        return None
    company = get_or_create_company(owner, name)
    company.from_career = True
    company.save(update_fields=["from_career"])
    return company


def find_or_add_school(owner, name: str):
    """The owner's company of this name, and whether this call added it (#685).

    A company the education form adds is, by the person's own statement, a place of
    learning, so it is given the industry the NACE division *Education* is called in the
    person's language, and the form says so. An existing company is never given an
    industry here: classifying a business already in the list is the person's act.
    """
    from django.utils import translation

    from postulo.jobs import industries
    from postulo.jobs.models import Industry

    found = existing(owner, name)
    if found is not None:
        return found, False
    company = find_or_add(owner, name)
    if company is None:
        return None, False
    label = industries.name_for("85", translation.get_language() or "")
    if label:
        company.industries.add(*Industry.named(owner, [label]))
    return company, True


def is_school(company) -> bool:
    """Whether the company has an industry in the place-of-learning division."""
    from postulo.jobs import roles
    from postulo.jobs.models import Company

    return (
        Company.objects.for_user(company.owner)
        .qualifying(roles.PLACE_OF_LEARNING)
        .filter(pk=company.pk)
        .exists()
    )
