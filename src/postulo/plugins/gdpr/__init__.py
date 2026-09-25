"""Data protection, the way a feature in this system can be: a switch, not a database.

Postulo keeps a lot of personal data on a person's behalf — contacts and their phone
numbers, addresses, notes, everything a capture brings in — and where the instance is in
the EU, or holds data of EU data subjects, GDPR applies to it. The account holder's own
rights were already met where the data is: the account archive carries every row and file
the account owns, and account deletion removes them. This feature is the rest: the data the
site keeps about *other people*, and the duties that sit with the instance itself.

> make the site GDPR-compliant with a plugin of its own

**A feature, and no more — which is the honest shape, not a limitation.** A feature
governs what the site offers and uses, and it cannot act: the moment it could, "off" would
mean two different things depending on which plugin you asked. So the pages, the export,
the erasure and the record of processing live in `postulo.core.gdpr`, and this says what
they are. Off is the same promise it makes everywhere in the system: every row stays, and
the site simply stops offering the pages. What a feature never does, and what this does
not, is delete.

**The name, and why it is the regulation and not the mechanism.** The pages this switches
are the data-subject export of one contact, the erasure of one, the retention policy with
its dry run, the record of processing, and the notice. Those are not Postulo's features in
the way several telephone numbers are: they are the duties the regulation names, and the
operator who reads "Data protection" on the plugins page reads the reason, not the
machinery. The operator's legal bases and the legal opinion are the operator's, not this
plugin's, which is the whole of what is left out.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on. Changing it orphans every decision an
#: administrator has recorded about it, so it is fixed the way a source's name is.
GDPR = "gdpr"


@declares(
    shipped(
        name=GDPR,
        label=_("Data protection"),
        kind="feature",
        description=_(
            "The data the instance keeps about other people: what it holds on one contact, "
            "asked in one document, the erasure of one that says what it removed, a "
            "retention policy with a dry run that deletes nothing, the record of what the "
            "instance processes and who receives it, and the instance's own privacy notice. "
            "Switched off, the site offers none of these pages; nothing is deleted, because "
            "a switch is not a deletion."
        ),
    )
)
class GdprFeature:
    """Data protection for the data the site keeps on other people.

    Off is what Postulo did before the regulation was named for this codebase: it kept the
    rows, it offered no page for them, and deleting a contact was a plain delete with no
    report.
    """
