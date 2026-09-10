"""The feature that lets an employer be a structure rather than a single name (#138).

An employer is not always one company. Applications land at a subsidiary, at a national
arm, at a brand somebody bought; and inside any of those there is a team the attempt was
actually aimed at. Postulo can record all of that -- a company may be part of another
(#55), a department sits between a company and the people in it (#137), and an application
may name the department it was for -- and this is the one switch that decides whether any
of it is offered.

**Off is exactly what Postulo did before the structure existed.** One company per posting,
one name on the page, contacts hanging directly off it. Nothing is deleted to get there:
the parent link, the departments and the attachments all stay exactly where they are, and
the day somebody switches this back on they are all still there. That is the promise every
plugin here makes, and a feature is not the exception.

**It is safe to switch off only because of how the attachment is shaped.** An application's
employer is its posting's company, and that column is not nullable; naming a department
*enriches* that link rather than replacing it. Had the employer link itself been made
polymorphic -- a posting pointing at a company, a department or a person -- then switching
this off would leave applications attached to departments with no company to fall back to,
which is a toggle that breaks a page rather than a toggle. The shape was chosen with this
switch in view.

**Only the declaration lives here.** The companies, the departments and the attachments are
core models, because they are a person's data and Postulo does the ownership scoping. What
a plugin says is *whether this capability is offered*; :mod:`postulo.jobs.structure` is
where the rest of the application asks.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
EMPLOYER_STRUCTURE = "employer-structure"


@declares(
    shipped(
        name=EMPLOYER_STRUCTURE,
        label=_("Employers with a structure"),
        kind="feature",
        description=_(
            "Record that one company is part of another, keep departments inside a "
            "company, and say which part of an employer an application was aimed at. "
            "Switched off, Postulo shows one company per posting and nothing else; the "
            "parents, the departments and the attachments stay recorded and come back "
            "untouched when it is switched on again."
        ),
    )
)
class EmployerStructureFeature:
    """A company inside a company, a department inside that, and an application naming one.

    Three edges rather than one four-level tree, because they are three different kinds of
    relation and treating them as one would let somebody make a company the child of a
    person. Ownership goes company to company and nests as deep as an ownership structure
    does; internal structure goes company to department; membership goes department to
    person. Each is refused where it does not belong.
    """
