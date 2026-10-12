"""Multiple fields: the module that holds the features which keep more than one of something.

Several email addresses, telephone numbers, websites, social profiles, code repositories and
messaging contacts are the same kind of feature -- a field that is one box until it is allowed
to be a list -- and were six unrelated rows on the plugins page. They are one module now, with
the six as its sub-modules (#875), so the page can say "this is the thing that lets a person
keep more than one" and give an administrator one switch for all of them.

**This is a grouping row and nothing else.** It has no behaviour of its own, and asks nothing
of anybody: what *off* means for each sub-module is said by that sub-module, and what *off*
means here is the cascade of :mod:`postulo.plugins.policy`, which turns them all off and takes
their switches away until it is on again. That is also why the sub-modules keep the names they
had -- policy rows, the archive and the plugins record key on them -- and only their labels
changed.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on, and what each sub-module names as its parent.
MULTIPLE_FIELDS = "multiple-fields"


@declares(
    shipped(
        name=MULTIPLE_FIELDS,
        label=_("Multiple fields"),
        kind="feature",
        categories=("contacts",),
        icon="list-plus",
        description=_(
            "Let a field hold more than one value: several email addresses, telephone "
            "numbers, websites, social profiles, code repositories and messaging contacts. "
            "This is the switch for all of them at once; each can still be switched off on "
            "its own while this is on. Switched off, every one of them shows and uses its "
            "primary value only, and the others stay recorded and come back untouched."
        ),
    )
)
class MultipleFieldsFeature:
    """The module the six "multiple …" features belong to. It governs nothing itself."""
