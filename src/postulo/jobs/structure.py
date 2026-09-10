"""What an employer's structure means, and whether it is being offered (#138).

Three edges, and they are not one relation. Calling them one would produce a model that
cannot answer either question:

===========================  ====================  ==================================
Edge                         Kind of relation      Depth
===========================  ====================  ==================================
company → company            ownership             arbitrary — *Alphabet > Google > …*
company → department         internal structure    one
department → contact         membership            one
===========================  ====================  ==================================

Modelling all three as one recursive parent would let somebody make a company the child of
a person, so each is its own field and each refuses what does not belong on it.

**"Attached to any of the four" is three fields, not four.** A posting's company is the
company applied to, whatever height of the tree it sits at, so *parent company* and *child
company* are the same column holding different rows. A contact is already a direct optional
link. Only the department was missing, and it is the one this issue adds.

Everything here goes through one gate, so "off shows the company alone" is one sentence in
the code as well as in the interface -- otherwise it becomes a rule six call sites remember
and a seventh does not.
"""

from __future__ import annotations

from postulo.plugins.employer_structure import EMPLOYER_STRUCTURE

#: What the company page and the companies table call the two readings of a count. A
#: question, so it lives in the address rather than on the profile.
ACROSS_GROUP = "group"


def structure_allowed(person) -> bool:
    """Whether this person is being offered employers with a structure."""
    from postulo.plugins.policy import decide

    return bool(decide(EMPLOYER_STRUCTURE, person).on)


def parent_of(company, person):
    """The company this one is part of, or nothing -- including when the feature is off.

    Off does not clear the link; it stops it being shown. The row is still there and comes
    back the moment the feature does.
    """
    if company is None or not structure_allowed(person):
        return None
    return company.parent


def children_of(company, person):
    """The companies inside this one, as far as this person is being shown them."""
    if company is None or not structure_allowed(person):
        return []
    return list(company.children.all())


def departments_for(company, person):
    """Every department this person may name for an application at ``company``.

    Across the whole group, not only at the company on the posting: an application through
    the Irish arm can be for the group's engineering team, and refusing that would make the
    tree decorative. Empty when the feature is off, so nothing offers a field that would
    then be refused.
    """
    from .models import Department

    if company is None or not structure_allowed(person):
        return Department.objects.none()
    return (
        Department.objects.for_user(person)
        .filter(company__in=[member.pk for member in company.group_members()])
        .select_related("company")
    )


def group_of(company, person):
    """The employer at the top of the chain, or the company itself where there is no tree.

    With the feature off this is always the company itself, because there is no structure
    being offered -- which is exactly what every count meant before any of this existed.
    """
    if company is None:
        return None
    return company.group if structure_allowed(person) else company


def in_a_group(company, person) -> bool:
    """Whether there is a group here at all, and therefore a second reading to offer."""
    if company is None or not structure_allowed(person):
        return False
    return company.parent_id is not None or company.children.exists()


def companies_counted(company, person, *, across: str = "") -> list[int]:
    """The company ids a count on this company's page covers.

    A hierarchy that silently keeps counting leaves is a hierarchy that changes nothing, so
    the page asks: *at this company*, or *across the group*. The default is the company
    alone, which is what every figure in Postulo has always meant -- changing what a number
    means without being asked would be the worse half of the same mistake.
    """
    if company is None:
        return []
    if across != ACROSS_GROUP or not structure_allowed(person):
        return [company.pk]
    return [member.pk for member in company.group_members()]
