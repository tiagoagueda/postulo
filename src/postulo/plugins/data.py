"""What happens to a plugin's table when the plugin goes.

**No plugin owns a table today**, and that is why this exists now rather than later. Everything
the newest plugin governs lives in core — `core.PhoneNumber`, core's migrations, a generic
relation on two holders in two other apps — so the question has never had to be answered. Make
one plugin self-contained in the sense the imperative asks for and it becomes a Django app with
a migration history of its own, and three questions arrive at once that have no answers.

**The rule.** *A plugin that owns a table may not be uninstalled while that table holds
anything.* Postulo refuses, says how many rows are in the way, and leaves both the package and
the data where they are.

Three options were open and they are not equally safe.

*Uninstall and keep the table* leaves a table with no model: invisible to `migrate`, present in
every backup, and absent from the export of the person whose data it is — which is precisely
the failure the export exists to prevent. Data nothing can see is worse than data somebody
decided about.

*Uninstall and delete, behind a confirmation* makes removing a package a data-destroying act.
Somebody swapping a plugin for a newer build of the same plugin would lose everything it held,
and the confirmation would be the only thing between them and that. Postulo's plugin system
promises in thirty-nine languages that switching a plugin **off** deletes nothing; making
*uninstall* the act that does is a distinction nobody will hold in their head at the moment it
matters.

*Refuse while rows exist* is the one left, and it is the shape this codebase already uses three
times: the last administrator, the mail transport that is the last way back in, a plugin that
is somebody's only recovery route. A refusal that names what holds it is a thing a person can
act on. Emptying the table is something the plugin itself knows how to offer — while it is
still installed, which is exactly when it should be asked.

**Off and uninstalled are now different acts, and the difference carries weight.** Off keeps
everything and offers nothing; uninstalled takes the code away and is refused while there is
anything to take away with it.

**And the export has to know.** A plugin that owns a person's data says how to put it in their
archive. One that owns data and does not say leaves a line in the archive naming what could
not be carried — because an archive that is quietly incomplete is worse than one that says so.

**One place asks.** The archive, the merge of two contacts and the erasure of one each need to
ask every plugin that owns rows about somebody. They ask through `export_sections_for`,
`holds_rows_for` and `erase_rows_for` below, which share one walk over the plugins and one
way of calling into them: inside a savepoint, so a plugin whose query fails undoes its own
work and nobody else's, and with the failure logged, so the operator can tell a plugin that
crashed from one that merely has nothing to say (#371).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

from django.db import transaction
from django.utils.translation import ngettext

logger = logging.getLogger(__name__)


def owned_labels(plugin) -> tuple[str, ...]:
    """The models this plugin owns, as ``app_label.ModelName`` strings.

    Absent means it owns none, which is true of every plugin Postulo ships and of every
    stateless kind — a source, an importer, a transport. Those can be moved out of core
    without any of this being decided, which is what makes a staged answer possible.
    """
    declared = getattr(plugin, "owns_models", ()) or ()
    if isinstance(declared, str):
        declared = (declared,)
    return tuple(str(label) for label in declared)


def owned_models(plugin) -> list:
    """The model classes, skipping any whose app is not loaded.

    A label that does not resolve is not an error here. It is a plugin whose app is missing
    from `INSTALLED_APPS` — which is a start-up problem with its own message, not a reason for
    the plugins page to raise while listing what is installed.
    """
    from django.apps import apps

    found = []
    for label in owned_labels(plugin):
        try:
            found.append(apps.get_model(label))
        except (LookupError, ValueError):
            continue
    return found


def rows_held_by(plugin) -> dict[str, int]:
    """How many rows each of this plugin's models holds. Empty models are left out."""
    counts = {}
    for model in owned_models(plugin):
        total = model._default_manager.count()
        if total:
            counts[model._meta.label] = total
    return counts


def refuse_removing(distribution: str) -> str:
    """Why this package may not be uninstalled, or an empty string if it may.

    Named after what it protects, the way every other refusal here is. The count is in the
    sentence because "there is data" is not something anybody can act on and "four telephone
    numbers" is.
    """
    from .record import canonicalise

    for plugin in _plugins_from(distribution):
        held = rows_held_by(plugin)
        if not held:
            continue
        total = sum(held.values())
        return str(
            ngettext(
                "%(label)s still holds %(count)d record, and uninstalling it would leave "
                "it in a table nothing can read, export or restore. Empty it from the "
                "plugin's own pages first, or switch the plugin off instead — off keeps "
                "everything.",
                "%(label)s still holds %(count)d records, and uninstalling it would leave "
                "them in a table nothing can read, export or restore. Empty it from the "
                "plugin's own pages first, or switch the plugin off instead — off keeps "
                "everything.",
                total,
            )
            % {"label": getattr(plugin, "label", canonicalise(distribution)), "count": total}
        )
    return ""


def _plugins_from(distribution: str) -> list:
    """Every installed plugin that came from this package."""
    from .record import canonicalise, packages_by_distribution
    from .registry import GROUPS
    from .registry import plugins as registry_plugins

    wanted = canonicalise(distribution)
    mapping = packages_by_distribution()
    found = []
    for kind in GROUPS:
        for plugin in registry_plugins(kind):
            module = type(plugin).__module__.split(".")[0]
            for name in mapping.get(module) or []:
                if canonicalise(name) == wanted:
                    found.append(plugin)
                    break
    return found


# ------------------------------------------------------------- what an archive carries


def export_sections(person) -> dict:
    """Every installed plugin's own data for one person, and what could not be carried.

    The account archive's half of this; the same discipline, asked about one person, is
    what the data-subject export of a contact needs, and one loop rather than two is what
    keeps the two from drifting apart (#297).
    """
    return export_sections_for(person)


def data_owners() -> Iterator:
    """Every installed plugin that says it owns a table.

    The registry is imported here and not at the top: it is what a test replaces to stand
    a plugin in, and the name has to be looked up when the walk happens.
    """
    from .registry import GROUPS
    from .registry import plugins as registry_plugins

    for kind in GROUPS:
        for plugin in registry_plugins(kind):
            if owned_labels(plugin):
                yield plugin


def name_of(plugin) -> str:
    """What a person is told the plugin is called: its label, or its name without one."""
    return str(getattr(plugin, "label", "") or plugin.name)


#: What `_ask` answers in place of the plugin: it has no such method, or it has one and it
#: raised. Different answers, though every caller here treats them alike: *cannot tell* is
#: never read as *nothing*.
SAYS_NOTHING = object()
FAILED = object()


def _ask(plugin, question: str, subject, read: Callable):
    """Call ``plugin.<question>(subject)`` and read its answer, or say why there is none.

    **In a savepoint.** Every request is a transaction, and a plugin whose failure comes
    out of an ORM write -- a constraint, a table whose migration was never applied -- leaves
    that transaction refusing every further query. Swallowing the exception was not enough:
    the next line of the caller raised instead, and erasing a contact answered 500. The
    savepoint is rolled back with the plugin's failure and the caller carries on.

    **And said.** `logger.exception`, naming the plugin, the question and the subject: the
    careful answer a caller gives in place of the plugin's is the same one it gives for a
    plugin that has nothing to say, and only the log tells them apart. ``read`` runs inside
    as well, because an answer may be a generator that has not touched the database yet.
    """
    method = getattr(plugin, question, None)
    if method is None:
        return SAYS_NOTHING
    try:
        with transaction.atomic():
            return read(method(subject))
    except Exception:
        logger.exception(
            "Plugin %r failed in %s for %s %s",
            plugin.name,
            question,
            getattr(getattr(subject, "_meta", None), "label", type(subject).__name__),
            getattr(subject, "pk", ""),
        )
        return FAILED


def _rows(answer) -> list:
    return list(answer or [])


def _count(answer) -> int:
    return int(answer or 0)


def export_sections_for(subject) -> dict:
    """Every installed plugin's own data for one subject, and what could not be carried.

    A plugin that owns data says how to put it in an archive by answering `export_for`.
    One that owns data and cannot answer is *named* rather than passed over: an archive
    that is quietly incomplete is worse than one that says which part is missing, because
    the first is discovered when somebody restores it and the second while they still have
    the original.
    """
    carried: dict[str, list] = {}
    missing: list[str] = []
    for plugin in data_owners():
        rows = _ask(plugin, "export_for", subject, _rows)
        if rows is SAYS_NOTHING or rows is FAILED:
            missing.extend(owned_labels(plugin))
        else:
            carried[plugin.name] = rows
    document = {"carried": carried}
    if missing:
        document["not_carried"] = sorted(set(missing))
    return document


def _holds(plugin, subject) -> bool:
    """Whether this plugin holds rows about the subject, or cannot be got to say."""
    rows = _ask(plugin, "export_for", subject, _rows)
    return rows is SAYS_NOTHING or rows is FAILED or bool(rows)


def holds_rows_for(subject) -> list[str]:
    """The plugins that hold rows of their own about this subject, by name.

    Asked the way the archive asks (`export_for`). One that owns a table and cannot say is
    named as well, because *nothing* and *cannot tell* are different answers and only the
    first is reassuring.
    """
    return sorted({name_of(plugin) for plugin in data_owners() if _holds(plugin, subject)})


def erase_rows_for(subject) -> tuple[int, list[str]]:
    """Have every plugin remove its rows about this subject: how many went, and who could not.

    A plugin removes its rows by answering `erase_for`, with the number it removed. The
    second half of the answer names the plugins that still hold something and could not
    remove it, and while it is not empty the caller must not report the subject as gone.

    Those with no `erase_for` at all are looked at first, before any plugin is asked to
    remove anything: one that holds rows about the subject, or cannot say, already settles
    it, and there is then no reason to have another plugin do work that will be undone.
    One that has no `erase_for` and demonstrably holds nothing is not in the way.
    """
    owners = list(data_owners())
    unable = sorted(
        {
            name_of(plugin)
            for plugin in owners
            if getattr(plugin, "erase_for", None) is None and _holds(plugin, subject)
        }
    )
    if unable:
        return 0, unable

    removed = 0
    for plugin in owners:
        count = _ask(plugin, "erase_for", subject, _count)
        if count is FAILED:
            unable.append(name_of(plugin))
        elif count is not SAYS_NOTHING:
            removed += count
    return removed, sorted(set(unable))
