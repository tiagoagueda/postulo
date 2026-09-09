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
"""

from __future__ import annotations

from django.utils.translation import gettext as _


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
    from .installing import canonicalise

    for plugin in _plugins_from(distribution):
        held = rows_held_by(plugin)
        if not held:
            continue
        total = sum(held.values())
        return str(
            _(
                "%(label)s still holds %(count)d record(s), and uninstalling it would leave "
                "them in a table nothing can read, export or restore. Empty it from the "
                "plugin's own pages first, or switch the plugin off instead — off keeps "
                "everything."
            )
            % {"label": getattr(plugin, "label", canonicalise(distribution)), "count": total}
        )
    return ""


def _plugins_from(distribution: str) -> list:
    """Every installed plugin that came from this package."""
    from importlib.metadata import packages_distributions

    from .installing import canonicalise
    from .registry import GROUPS
    from .registry import plugins as registry_plugins

    wanted = canonicalise(distribution)
    mapping = packages_distributions()
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

    A plugin that owns data says how to put it in an archive by answering `export_for`. One
    that owns data and cannot answer is *named* rather than passed over: an archive that is
    quietly incomplete is worse than one that says which part is missing, because the first
    is discovered when somebody restores it and the second while they still have the original.
    """
    from .registry import GROUPS
    from .registry import plugins as registry_plugins

    carried: dict[str, list] = {}
    missing: list[str] = []
    for kind in GROUPS:
        for plugin in registry_plugins(kind):
            if not owned_labels(plugin):
                continue
            exporter = getattr(plugin, "export_for", None)
            if exporter is None:
                missing.extend(owned_labels(plugin))
                continue
            try:
                carried[plugin.name] = list(exporter(person) or [])
            except Exception:  # pragma: no cover - a plugin must not break somebody's export
                missing.extend(owned_labels(plugin))
    document = {"carried": carried}
    if missing:
        document["not_carried"] = sorted(set(missing))
    return document
