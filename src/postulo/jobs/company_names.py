"""Whether a company's new name repeats one its owner already holds (#949).

The clash check runs against the owner's whole set of companies. For the owner that is a
courtesy; for somebody holding a read/write share on one company it is a question about the
owner's *other* companies, answered with a refusal or a rename. The check cannot be dropped
(the per-owner uniqueness needs it) and renaming stays a grantee's right (#896), so the
answer is bounded as the telephone one is (#758): a person who does not own the company is
told at most so many times an hour, and once the allowance is spent a free name and a held
one are refused in the same words.
"""

from __future__ import annotations

from postulo.core import slugs, throttle
from postulo.core.models import group_ids

from .models import Company

ACTION = "company-name-clash"
SETTING = "POSTULO_COMPANY_NAME_RATE"


def _owns(asked_by, company: Company) -> bool:
    """Whether ``asked_by`` is the company's owner: the person, or a member of its group."""
    if company.owner_id is not None:
        return company.owner_id == asked_by.pk
    return company.owner_group_id in group_ids(asked_by)


def clashes(asked_by, owner, name: str, *, company: Company | None = None) -> bool:
    """Whether ``owner`` already holds another company by ``name``.

    ``company`` is the one being renamed, left out of the search. Raises
    :class:`postulo.core.throttle.TooOften` when ``asked_by`` is not the owner and has been
    told as often as it may: asked before the lookup and without using anything, so the
    spent answer is the same whether the name is held or free.
    """
    shared = company is not None and company.pk and not _owns(asked_by, company)
    rate = throttle.rate_for(SETTING)
    if shared:
        wait = throttle.spent(ACTION, asked_by, rate)
        if wait:
            raise throttle.TooOften(rate, retry_after=wait)
    found = Company.objects.owned_by(owner).filter(name_key=slugs.name_key(name))
    if company is not None and company.pk:
        found = found.exclude(pk=company.pk)
    held = found.exists()
    if held and shared:
        throttle.consume(ACTION, asked_by, rate)
    return held
