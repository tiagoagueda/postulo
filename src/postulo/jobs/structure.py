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


def _seen(company, person) -> bool:
    """Whether ``person`` may see ``company`` (#957).

    A link can reach a company its reader may not see: a private company made the child of a
    shared one, or a parent whose share was revoked while the child's stayed. What a link
    leads to is drawn only when it is one of the reader's own, or shared with them.
    """
    from .models import Company

    return Company.objects.visible_to(person).filter(pk=company.pk).exists()


def parent_of(company, person):
    """The company this one is part of, or nothing -- including when the feature is off,
    and when the parent is one this person may not see (#957).

    Off does not clear the link; it stops it being shown. The row is still there and comes
    back the moment the feature does.
    """
    if company is None or not structure_allowed(person):
        return None
    parent = company.parent
    return parent if parent is not None and _seen(parent, person) else None


def children_of(company, person):
    """The companies inside this one that this person is being shown (#957): not a private
    company of somebody else's that was made part of a shared one."""
    from .models import Company

    if company is None or not structure_allowed(person):
        return []
    return list(Company.objects.visible_to(person).filter(parent=company))


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
    if not structure_allowed(person):
        return company
    # Up through the companies the person may see, and no further (#957): the top of what
    # they are shown, not of a chain with a private link in it.
    node, seen = company, {company.pk}
    for _step in range(company.MAX_DEPTH):
        above = parent_of(node, person)
        if above is None or above.pk in seen:
            break
        node = above
        seen.add(node.pk)
    return node


def in_a_group(company, person) -> bool:
    """Whether there is a group here at all, and therefore a second reading to offer."""
    if company is None or not structure_allowed(person):
        return False
    return parent_of(company, person) is not None or bool(children_of(company, person))


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
    from .models import Company

    top = group_of(company, person)
    members = [top.pk, *(member.pk for member in top.descendants())]
    # Only companies the person may see: otherwise the link to the group's figures appears
    # with nothing visible behind it, and their ids reach the narrowed queries (#957).
    visible = Company.objects.visible_to(person).filter(pk__in=members)
    seen = set(visible.values_list("pk", flat=True))
    return [pk for pk in members if pk in seen] or [company.pk]
