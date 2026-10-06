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
