"""Postulo's side of the `gdpr` feature (#297).

The plugin declares the capability; this is what the site does with the answer. The
division is the same one `phone-numbers` and `email-addresses` use: a plugin never asks
whether it is on for somebody, it says what it is, and Postulo asks the policy.

**The subject is another person, and that is the whole of the difference from the archive.**
The account holder's own rights are met where the data is: the account archive carries every
row and file the account owns, and account deletion removes them. What the archive never
reached is the data the instance keeps *on other people* — a contact's number, address,
notes — and the duties that sit with the instance itself rather than with one account. This
module is the answer for both: one contact, everything on it, one document; and the record
of what the instance does with the data it keeps at all.

**The export follows the archive's discipline rather than inventing one.** A plugin that
owns rows says how to put them in a document by answering `export_for`; one that owns rows
and does not answer is *named* in the document as `not_carried` rather than passed over,
because a document that is quietly incomplete is discovered by whoever restores it and the
original is gone by then.

**Erasure is the one act here that deletes, and it says what it deleted.** Off never deletes
anywhere in the plugin system, and that stays true: switching the feature off keeps every
row and merely stops offering these pages. Erasure is an act a person takes about one
contact, and a deletion that does not say what it removed is a guess about its own effect, so
the report carries the counts.

**Retention is a policy and a dry run, never a janitor.** The setting says how long records
about other people are kept; the dry run says what the policy would touch. Nothing here
deletes on a schedule, because a deletion nobody watched happen is the failure the dry run
exists to prevent, and the operator who has read the report is the one to perform it.

**The record of processing is drawn, not written.** Every installed plugin is a purpose and
every connection is a recipient in its own right, so the Article 30 page is assembled from
the registry and the connections at render time. A second list to keep in sync is how the
page that lies about the instance gets written.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

#: What the document of one contact is, in the shape a program can read it back. Bumped the
#: way the archive's format version is, when the shape changes in a way a reader notices.
DOCUMENT_NAME = "postulo-contact"
DOCUMENT_VERSION = 1


def is_offered(person=None) -> bool:
    """Whether the site offers the data-protection side at all, for this person."""
    from postulo.plugins.gdpr import GDPR
    from postulo.plugins.policy import decide

    return decide(GDPR, person).on


# ------------------------------------------------------------------- the export


def _contact_rows(contact) -> dict:
    """The contact's own row, the way the archive writes one: what it holds, nothing more."""
    return {
        "id": contact.pk,
        "name": contact.name,
        "role": contact.role,
        "email": contact.email,
        "notes": contact.notes,
        "company": contact.company.name if contact.company_id else "",
        "department": (
            contact.department.name if contact.department_id and contact.department else ""
        ),
        "created_at": contact.created_at.isoformat() if contact.created_at else "",
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else "",
    }


def _phone_rows(contact) -> list[dict]:
    return [
        {
            "number": row.number,
            "kind": row.kind,
            "label": row.label,
            "primary": row.is_primary,
        }
        for row in contact.phone_numbers.all()
    ]


def _address_rows(contact) -> list[dict]:
    return [
        {
            "kind": row.kind,
            "label": row.label,
            "street": row.street,
            "postcode": row.postcode,
            "municipality": row.municipality,
            "region": row.region,
            "country": row.country,
            "primary": row.is_primary,
        }
        for row in contact.postal_addresses.all()
    ]


def _web_link_rows(contact) -> list[dict]:
    return [
        {
            "kind": row.kind,
            "label": row.label,
            "url": row.url,
            "primary": row.is_primary,
        }
        for row in contact.web_links.all()
    ]


def contact_document(contact) -> dict:
    """Everything the instance holds on one other person, in one document.

    The answer to "what do you have on me?". The rows Postulo itself keeps, and then every
    installed plugin's own rows for this person through the archive's discipline: what a
    plugin carries is under `plugins.carried`, and what it owns but could not answer for is
    named under `not_carried` rather than dropped, because the document is the one that has
    to be complete or have to say it is not.
    """
    from postulo.plugins.data import export_sections_for

    plugins = export_sections_for(contact)
    document = {
        "document": DOCUMENT_NAME,
        "version": DOCUMENT_VERSION,
        "contact": _contact_rows(contact),
        "phone_numbers": _phone_rows(contact),
        "postal_addresses": _address_rows(contact),
        "web_links": _web_link_rows(contact),
        "plugins": plugins,
    }
    if "not_carried" in plugins:
        # Hoisted to the top level so "what could not be carried" is answered in one
        # place, the way the archive answers it.
        document["not_carried"] = plugins["not_carried"]
    return document


# -------------------------------------------------------------------- the erasure


@dataclasses.dataclass
class ErasureReport:
    """What an erasure did, in counts and in a sentence a person can read.

    ``deleted`` is what stopped existing, by kind. ``unlinked`` is what survived but no
    longer points at the contact — an application keeps its history, it loses its main
    contact. ``not_erased`` names the plugins that hold rows for this person and could not
    be asked: an erasure that leaves data it cannot see about is the same quiet
    incompleteness the export refuses, so it is said rather than discovered.
    """

    name: str
    deleted: dict[str, int]
    unlinked: dict[str, int]
    not_erased: list[str]

    def summary(self) -> str:
        """The sentence shown to whoever did it: what went, what stayed, and what was left
        that could not be reached."""
        gone = ", ".join(
            _("%(count)d %(kind)s") % {"count": count, "kind": _kinds(kind)}
            for kind, count in self.deleted.items()
            if count
        )
        parts = []
        if gone:
            parts.append(_("%(name)s is gone, with %(what)s.") % {"name": self.name, "what": gone})
        if any(self.unlinked.values()):
            parts.append(
                _("%(count)d application kept, without its main contact.")
                % {"count": sum(self.unlinked.values())}
            )
        if self.not_erased:
            parts.append(
                _("These still hold rows Postulo could not reach: %(plugins)s.")
                % {"plugins": ", ".join(self.not_erased)}
            )
        return " ".join(parts) or _("Nothing was left to remove.")


def _kinds(key: str) -> str:
    """The words for a deleted kind. The count beside them carries the number."""
    return {
        "phone_numbers": _("telephone numbers"),
        "postal_addresses": _("postal addresses"),
        "web_links": _("web links"),
        "plugin_rows": _("plugin rows"),
    }.get(key, key)


def _erase_plugin_rows(contact) -> tuple[int, list[str]]:
    """Ask each plugin that owns rows to erase this person's, and count what went.

    A plugin that owns rows but does not answer `erase_for` — or answers with an error —
    is named in the returned list and its rows stay: deleting the contact underneath it
    would leave rows pointing at nothing, the failure the export's `not_carried` and the
    uninstall refusal both exist to prevent.
    """
    from postulo.plugins import data as plugin_data
    from postulo.plugins.registry import GROUPS
    from postulo.plugins.registry import plugins as registry_plugins

    removed = 0
    not_erased: list[str] = []
    for kind in GROUPS:
        for plugin in registry_plugins(kind):
            if not plugin_data.owned_labels(plugin):
                continue
            eraser = getattr(plugin, "erase_for", None)
            if eraser is None:
                not_erased.append(str(plugin.label))
                continue
            try:
                removed += int(eraser(contact) or 0)
            except Exception:
                not_erased.append(str(plugin.label))
    return removed, not_erased


def erase_contact(contact) -> ErasureReport:
    """Delete the contact and everything that points at it, and say what was deleted.

    The rows cascade by construction — the generic relations are why a deleted contact has
    never left its numbers behind — so the erasure is the report as much as the deletion:
    each count is taken before the delete that spends it, and the plugins are asked before
    anything goes, in the same transaction, because a row erased while its contact survives
    is a deletion that cannot be undone and the report that said it happened is wrong.
    """
    from postulo.applications.models import Application

    with transaction.atomic():
        removed, not_erased = _erase_plugin_rows(contact)
        deleted = {
            "phone_numbers": contact.phone_numbers.count(),
            "postal_addresses": contact.postal_addresses.count(),
            "web_links": contact.web_links.count(),
            "plugin_rows": removed,
        }
        unlinked = {"applications": Application.objects.filter(contact=contact).count()}
        name = contact.name
        contact.delete()

    return ErasureReport(name=name, deleted=deleted, unlinked=unlinked, not_erased=not_erased)


# --------------------------------------------------------------------- the retention


def retention_days() -> int | None:
    """How long records about other people are kept, or nothing for no limit."""
    from .models import SiteSettings

    return SiteSettings.get().retention_days


def retention_cutoff() -> dt.date | None:
    """The date the policy would draw the line at, or nothing with no policy."""
    days = retention_days()
    if not days:
        return None
    return timezone.localdate() - dt.timedelta(days=days)


def retention_dry_run() -> dict:
    """What the retention policy would touch. Deletes nothing — that is the whole point.

    A dry run that removed a row to check whether it could would be a deletion with an
    apology, so the question is asked of the query set alone: who is older than the line,
    and what would go with them. The operator reads the report and performs the erasures
    one by one, which is the difference between a policy and a janitor.
    """
    from postulo.applications.models import Application
    from postulo.jobs.models import Contact

    cutoff = retention_cutoff()
    if cutoff is None:
        return {"days": None, "cutoff": None, "contacts": []}

    older_than = timezone.make_aware(dt.datetime.combine(cutoff, dt.time.min))
    rows = []
    for contact in Contact.objects.filter(created_at__lt=older_than).select_related("company"):
        rows.append(
            {
                "id": contact.pk,
                "name": contact.name,
                "company": contact.company.name if contact.company_id else "",
                "created_at": contact.created_at.date().isoformat(),
                "would_remove": {
                    "phone_numbers": contact.phone_numbers.count(),
                    "postal_addresses": contact.postal_addresses.count(),
                    "web_links": contact.web_links.count(),
                    "applications_unlinked": Application.objects.filter(contact=contact).count(),
                },
            }
        )
    return {"days": retention_days(), "cutoff": cutoff.isoformat(), "contacts": rows}


# -------------------------------------------------------- the record of processing


def record_of_processing() -> dict:
    """What the instance processes, why, and who receives it — drawn at read time.

    Every installed plugin is a purpose: what it is, and the one line it says it does.
    Every connection is a recipient: the kind of plugin, the plugin, and the configuration
    that names where it talks. Neither list is written down anywhere to be kept in sync,
    which is the difference between a record that can lie and one that is a mirror.
    """
    from postulo.plugins import registry
    from postulo.plugins.api import description_of, label_of
    from postulo.plugins.models import Connection

    purposes = [
        {
            "name": plugin.name,
            "label": label_of(plugin),
            "kind": plugin.kind,
            "description": description_of(plugin),
        }
        for kind in registry.GROUPS
        for plugin in registry.plugins(kind)
    ]
    recipients = [
        {
            "kind": connection.kind,
            "plugin": connection.plugin,
            "label": connection.label,
            "config": connection.config,
            "enabled": connection.enabled,
        }
        for connection in Connection.objects.all().order_by("kind", "plugin")
    ]
    return {"purposes": purposes, "recipients": recipients}


# ---------------------------------------------------------------------- the notice


def notice() -> str:
    """The instance's privacy text, or nothing when the operator has written none.

    An instance with no notice to give does not invent one: the text is the operator's
    words, and a page of Postulo's own reassurance would be a promise the operator never
    made.
    """
    from .models import SiteSettings

    return SiteSettings.get().privacy_notice
