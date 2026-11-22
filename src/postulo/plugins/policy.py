"""Whether a plugin is on for a person, and who decided.

One place, because the alternative is every call site remembering — and the call site that
forgets is the one that lets a plugin run for somebody an administrator switched it off
for.

**The order, and why it is this order.**

1. *An administrator's decision about this person.*
2. *An administrator's decision about everybody.*
3. *The person's own choice*, which applies wherever an administrator has not taken it
   away -- **and only to a plugin installed on the instance.** A plugin shipped inside
   Postulo is the administrator's to switch, for one person or for everybody, and never
   the person's (#200): what Postulo itself does is a decision about the instance, taken
   by whoever runs it, and a page of a dozen built-in switches was a page of things
   nobody meant to be a choice.

**Switching a package off for the instance is not a step here.** The plugins record names
distributions (``postulo-imap``), a policy asks about plugins (``imap``), and the two are
not the same string. The registry is the switch: it leaves out every plugin of a disabled
distribution, so one never reaches :func:`decide`. That is the code not being there, not a
policy decision, and a check here that compared the two names would only look like
protection (#596).

**Forcing a plugin on does not make it run.** A notifier, store or sync needs a
``Connection`` holding credentials only the person can supply, so *on* means "this is
available to you and you may not switch it off" rather than "this is now sending your
documents somewhere". Sources and features are the exception: they need nothing from
anybody, so forcing one on genuinely turns it on. The interface has to say which of those
it means, and :func:`decide` returns enough for it to.

**A sub-module follows its parent** (#873). A module may have sub-modules, each with exactly one
parent, and what the parent decides reaches them first: a child of a parent that is off is
off, and its switch is not the person's or the administrator's to move until the parent is
on again. Switching a parent *on* clears its children's *off* rows, as the page does, so they
all come on together and can then be switched one by one. A parent is an ordinary row with
no behaviour of its own, so nothing here knows what it governs; it is only asked.

**A plugin follows what it needs** (#884). A manifest's ``requires`` names the plugins and
modules it cannot work without; one that is missing, or is off for this person, makes this one
off too, with the labels of what is wanted beside it. Asked after the parent and before the
rows, so that a plugin forced on by an administrator is still not run without its need.

**What is required is on** (#877). A manifest marked ``required`` answers yes before anything
else is asked: its parent being off does not reach it, and a row an administrator wrote for it
is not read. It is not the person's or the administrator's, and the page says so.

**Switching a feature off does not delete what it governs.** It stops Postulo offering and
using that part of itself; the rows stay, and switching it back on finds them unchanged.
That is the same promise every other kind here makes, and it is not weaker for the kind
whose subject happens to be Postulo's own tables.

**Transports are exempt.** Mail delivery is instance infrastructure rather than a capability
a person holds, and *forced off* for a transport would be an account nobody can recover
(#104). They are not offered here at all.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from django.db import models
from django.utils.translation import gettext_lazy as _

from postulo.core import memo

# The forgetting lives with the memo, which a policy row can import when it is saved; it is
# handed out here too -- the middleware and the scheduler call it here (#248).
from postulo.core.memo import forget_decisions

#: Kinds a person may hold an opinion about. A transport is deliberately not one of them.
GOVERNED_KINDS = (
    "source",
    "notifier",
    "store",
    "sync",
    "importer",
    "feature",
    "outbox",
    "suggestion-source",
)

#: The rest. Named rather than implied, because the guard below has to look a plugin up by
#: name and "every kind that is not governed" is the honest way to write that.
#:
#: An **identifier** plugin is here for a different reason from a transport. A transport is
#: instance plumbing nobody should be able to switch off; a registry of identifier schemes
#: is not a behaviour at all. Every other kind answers "is this on for this person"; this one
#: answers "what does this key mean", and off would leave every stored identifier without a
#: label, a link or a check -- which is not what off means anywhere else here (#109).
#:
#: A **link-service** plugin is the other registry, of the services a web link can be on,
#: and is here for the identifier's reason: no person holds an opinion about what
#: ``linkedin`` means (#305). So is a **messaging-service** plugin, the registry of the
#: services a handle can be on (#682). So is a **register-adapter** plugin (#1021): it says how
#: a country's register is asked, and whether anything is asked at all is the decision of the
#: ``register-lookup`` feature, which is governed like any other. So is a **listing-search**
#: plugin (#1253): it says how a job service is searched, and whether one is asked is the
#: decision of the ``listing-search`` feature and of the switch each service has under it.
#:
#: A **backup-destination** plugin is here for the transport's reason: where the instance's
#: archives are kept is the operator's plumbing, and *forced off* for one person would mean
#: nothing at all, since nobody's data is copied on their own behalf (#325).
UNGOVERNED_KINDS = (
    "transport",
    "identifier",
    "link-service",
    "messaging-service",
    "register-adapter",
    "listing-search",
    "backup-destination",
)


@dataclass(frozen=True)
class Decision:
    """What is true for one plugin and one person, and who made it true."""

    #: Whether the plugin acts for this person at all.
    on: bool
    #: Whether they even see that it exists.
    offered: bool
    #: Whether they may change it.
    theirs: bool
    #: ``instance``, ``administrator``, ``shipped``, ``person`` or ``default`` — for #96
    #: to show. ``shipped`` is a built-in nobody decided: on, and an administrator's to
    #: switch (#200).
    decided_by: str
    #: The administrator who decided, where one did and is still an account. Shown on the
    #: administrator's own view of a person's row (`server/person_plugins.html`), where who
    #: decided is the point; no longer on the person's page, where on nearly every instance
    #: it named the reader to themselves (#287).
    who: object = None
    #: For ``requires``: the labels of what is missing or off, said beside the row (#884).
    because: tuple[str, ...] = ()

    @property
    def imposed(self) -> bool:
        return self.decided_by == "administrator"

    def explain(self):
        """The sentence under the row on the person's own page, or nothing.

        A built-in gets no sentence: the rows Postulo ships appear only behind a mark whose
        own caption already says an administrator switches them, and a per-row echo of it
        was noise (#287). A decision made for the account gets a neutral one, because on
        nearly every instance the administrator is the person reading, and "an
        administrator decided this for you" read as the interface explaining them to
        themselves in the tone of a permission held over them. The row is still shown and
        still disabled; what is dropped is the attribution, not the visibility.
        """
        if self.decided_by == "requires":
            return _("Off, because it needs %(names)s, which is off or not installed.") % {
                "names": ", ".join(self.because)
            }
        return {
            "administrator": _("Set for your account."),
            "shipped": "",
            "person": _("You chose this."),
            "default": _("Available; you have not changed it."),
            "infrastructure": _("How this instance works, rather than a choice anybody holds."),
            "parent": _("Off, because the module it belongs to is off."),
            "required": _("Always on: Postulo cannot work without it."),
            "access": _("Set by your administrator."),
            "sub-default": _("Off until an administrator switches it on."),
            "unavailable": " ".join(self.because),
        }[self.decided_by]


def access_for(plugin_name: str, person):
    """The access rule that applies to this person for this plugin, or ``None`` (#886).

    The account's own rule over its groups': with several groups the most permissive reading
    wins on each of the three rights. ``None`` means nobody said, and the default stands.
    """
    from .models import PluginAccess

    pk = getattr(person, "pk", None)
    if not pk:
        return None
    # Asked afresh, for the one plugin: this is what the administration pages call, about
    # whoever they are drawing, and what `may_change_settings` rests on. A decision reads
    # the request's rows instead (`_rows_for`), and both end in `_rule_among` (#1112).
    rules = list(
        PluginAccess.objects.filter(plugin=plugin_name).filter(
            models.Q(person_id=pk) | models.Q(group__user=pk)
        )
    )
    return _rule_among(plugin_name, pk, rules)


def _rule_among(plugin_name: str, pk, rules: list):
    """The one rule these come to for an account: its own, or its groups' read together."""
    from .models import PluginAccess

    for rule in rules:
        if rule.person_id == pk:
            return rule
    if not rules:
        return None
    return PluginAccess(
        plugin=plugin_name,
        may_use=any(rule.may_use for rule in rules),
        may_switch=any(rule.may_switch for rule in rules),
        may_change_settings=any(rule.may_change_settings for rule in rules),
    )


def may_change_settings(user, plugin_name: str) -> bool:
    """Whether this person may change the plugin's settings (#885, #886).

    The operator (a superuser) always may. Another administrator may unless a rule for them, or
    for their groups, says they may not; anyone who is not an administrator never reaches the
    page. A rule is an exception to the default, which is that an administrator may.
    """
    if not (getattr(user, "is_staff", False) and getattr(user, "is_active", True)):
        return False
    if getattr(user, "is_superuser", False):
        return True
    rule = access_for(plugin_name, user)
    return True if rule is None else rule.may_change_settings


def shipped_inside(plugin_name: str) -> bool:
    """Whether this plugin ships inside Postulo, which makes it the administrator's.

    The same question `installing.is_internal` answers for the plugins page: a built-in
    registered by an app's `ready()`, as against one installed on the volume or loaded
    from an entry point. Asked by name because that is what a policy row holds.
    """
    from .installing import is_internal
    from .registry import find_sub_plugin

    # A sub-plugin is its plugin's: it ships inside Postulo exactly when its plugin does.
    if (sub := find_sub_plugin(plugin_name)) is not None:
        plugin_name = sub.parent
    return is_internal(plugin_name)


def is_ungoverned(plugin_name: str) -> bool:
    """Whether this plugin is instance plumbing rather than anybody's to decide.

    A transport is. None of *available*, *unavailable*, *forced on* or *forced off* means
    anything about where an instance's mail goes, and *forced off* would mean an account
    nobody can recover (#104).
    """
    from .registry import plugins

    return any(
        getattr(item, "name", None) == plugin_name
        for kind in UNGOVERNED_KINDS
        for item in plugins(kind)
    )


def _find(plugin_name: str):
    """The plugin called ``plugin_name``, or the sub-plugin of a plugin that is called that."""
    from .registry import find_any, find_sub_plugin

    found = find_any(plugin_name)
    return found if found is not None else find_sub_plugin(plugin_name)


def default_state(plugin_name: str):
    """What a plugin is when nobody has decided: available, but for a sub-plugin that its
    plugin declared ``default_on=False`` (and for one that cannot work here), which is off."""
    from .models import PluginPolicy
    from .registry import find_sub_plugin

    sub = find_sub_plugin(plugin_name)
    if sub is not None and (not sub.sub.default_on or sub.sub.unavailable):
        return PluginPolicy.State.FORCED_OFF
    return PluginPolicy.State.AVAILABLE


def is_required(plugin_name: str) -> bool:
    """Whether Postulo cannot work without this one, so no row or parent can turn it off (#877)."""
    from . import base

    plugin = _find(plugin_name)
    return bool(plugin is not None and base.manifest_of(plugin).required)


def parent_of(plugin_name: str) -> str:
    """The module this plugin is a sub-module of, or nothing (#873).

    Read off the manifest of whatever is registered under the name. A name nothing is
    registered under has no parent, which is what a policy row left behind by a plugin since
    removed should mean.
    """
    from . import base

    plugin = _find(plugin_name)
    return base.manifest_of(plugin).parent if plugin is not None else ""


#: The names being decided in this thread right now, so that two plugins which need each other
#: are not an infinite regress: the second to ask is told yes, and a test holds what ships to
#: no cycle at all (#884).
_deciding = threading.local()


def _unmet_needs(plugin_name: str, person) -> list[str]:
    """The labels of what this plugin needs that is missing, or off for this person (#884)."""
    from . import dependencies
    from .registry import find_any

    plugin = _find(plugin_name)
    if plugin is None:
        return []
    needs = dependencies.needs_of(plugin)
    if not needs:
        return []
    asking = getattr(_deciding, "names", None)
    if asking is None:
        asking = _deciding.names = set()
    if plugin_name in asking:
        return []
    asking.add(plugin_name)
    try:
        found = []
        for need in needs:
            if not dependencies.is_present(need):
                found.append(dependencies.label_of(need.name))
            elif find_any(need.name) is not None and not decide(need.name, person).on:
                found.append(dependencies.label_of(need.name))
        return found
    finally:
        asking.discard(plugin_name)


def children_of(plugin_name: str) -> list[str]:
    """The names of the sub-modules of a module, in the order the registry lists them."""
    from . import base
    from .registry import plugins, sub_plugins

    return [
        *(
            item.name
            for kind in GOVERNED_KINDS
            for item in plugins(kind)
            if base.manifest_of(item).parent == plugin_name
        ),
        *(entry.name for entry in sub_plugins(plugin_name)),
    ]


def _held() -> tuple:
    """What this request remembers, in `memo.decisions`: the stamp, the answers, the rows.

    One holder for both, so that whatever forgets the answers forgets the rows they were made
    from in the same breath, and a plugin installed or removed drops the two together (#1112).
    """
    from .installing import record_stamp

    stamp = record_stamp()
    held = getattr(memo.decisions, "answers", None)
    if held is None or held[0] != stamp:
        held = (stamp, {}, {})
        memo.decisions.answers = held
    return held


def _answers() -> dict:
    """This request's answers, in `memo.decisions` (#231)."""
    return _held()[1]


def _rows_for(person) -> tuple[dict, dict]:
    """Every row that can reach this person, read once a request: two queries (#1112).

    The policy rows -- the instance's and the person's own, by plugin and then by whose they
    are -- and the access rules for the account or its groups, by plugin. Each first answer
    used to read its own, so a page asking about ten plugins made twenty queries for rows
    that, on most instances, are not there at all.

    For the request and no longer, like the answers beside them: `forget_decisions` drops
    both, and it is called at every request boundary, when a policy row or an access rule is
    written or deleted, when somebody joins or leaves a group, and when a person changes
    their own choice. Never across requests, where another worker's write would not be heard.

    Who decided comes with the row, since a row an administrator wrote is read for its name
    every time the plugin is asked about.
    """
    from .models import PluginAccess, PluginPolicy

    pk = getattr(person, "pk", None)
    rows = _held()[2]
    if pk not in rows:
        policies: dict = {}
        for row in PluginPolicy.objects.filter(models_person_filter(person)).select_related(
            "decided_by"
        ):
            policies.setdefault(row.plugin, {})[row.person_id] = row
        # What the environment decides for the instance is laid over what was stored (#1186).
        for name, pinned in pinned_rows().items():
            policies.setdefault(name, {})[None] = pinned
        access: dict = {}
        if pk:
            for rule in PluginAccess.objects.filter(
                models.Q(person_id=pk) | models.Q(group__user=pk)
            ):
                access.setdefault(rule.plugin, []).append(rule)
        rows[pk] = (policies, access)
    return rows[pk]


def decide(plugin_name: str, person) -> Decision:
    """The one answer, for one plugin and one person. Memoised for the request."""
    key = (plugin_name, getattr(person, "pk", None))
    answers = _answers()
    if key not in answers:
        answers[key] = _decide(plugin_name, person)
    return answers[key]


def _decide(plugin_name: str, person) -> Decision:
    from .models import PluginPolicy

    # Before anything else, because this is what stops a hand-written POST reaching
    # `set_choice` with a transport's name and switching the mail off for somebody.
    if is_ungoverned(plugin_name):
        return Decision(on=True, offered=False, theirs=False, decided_by="infrastructure")

    if is_required(plugin_name):
        return Decision(on=True, offered=True, theirs=False, decided_by="required")

    parent = parent_of(plugin_name)
    if parent:
        above = decide(parent, person)
        if not above.on:
            return Decision(on=False, offered=above.offered, theirs=False, decided_by="parent")

    if wanting := _unmet_needs(plugin_name, person):
        return Decision(
            on=False, offered=True, theirs=False, decided_by="requires", because=tuple(wanting)
        )

    policies, access = _rows_for(person)
    rows = policies.get(plugin_name, {})
    row = rows.get(getattr(person, "pk", None)) or rows.get(None)
    state = row.state if row else default_state(plugin_name)

    if sub := _sub_unavailable(plugin_name):
        return Decision(
            on=False, offered=True, theirs=False, decided_by="unavailable", because=(sub,)
        )
    if state == PluginPolicy.State.UNAVAILABLE:
        return Decision(
            on=False, offered=False, theirs=False, decided_by="administrator", who=row.decided_by
        )
    # An access list narrows the instance's default for this account or its groups (#886). It
    # is an exception to the default and never to an administrator's row for this very person.
    rule = None
    if state != PluginPolicy.State.FORCED_OFF and getattr(row, "person_id", None) is None:
        rule = _rule_among(plugin_name, getattr(person, "pk", None), access.get(plugin_name, []))
    if rule is not None:
        if not rule.may_use:
            return Decision(on=False, offered=False, theirs=False, decided_by="access")
        if not shipped_inside(plugin_name):
            if rule.may_switch:
                chosen_off = plugin_name in _their_choices(person)
                return Decision(
                    on=not chosen_off,
                    offered=True,
                    theirs=True,
                    decided_by="person" if chosen_off else "default",
                )
            return Decision(on=True, offered=True, theirs=False, decided_by="access")
    if state == PluginPolicy.State.FORCED_ON:
        return Decision(
            on=True, offered=True, theirs=False, decided_by="administrator", who=row.decided_by
        )
    if state == PluginPolicy.State.FORCED_OFF:
        if row is None:
            # Nobody decided: a sub-plugin its plugin declared off until somebody switches it on.
            return Decision(on=False, offered=True, theirs=False, decided_by="sub-default")
        return Decision(
            on=False, offered=True, theirs=False, decided_by="administrator", who=row.decided_by
        )

    if shipped_inside(plugin_name):
        # Postulo's own: on unless an administrator said otherwise above, and never the
        # person's to switch. A name the person's list still holds from before #200 is
        # not read, and the migration that came with it emptied those out.
        return Decision(on=True, offered=True, theirs=False, decided_by="shipped")

    chosen_off = plugin_name in _their_choices(person)
    return Decision(
        on=not chosen_off,
        offered=True,
        theirs=True,
        decided_by="person" if chosen_off else "default",
    )


# ------------------------------------------------------- what the environment decides (#1186)


def variable_name(plugin_name: str) -> str:
    """The variable that sets the instance's decision about a plugin: `POSTULO_PLUGIN_<NAME>`."""
    from .base import environment_prefix

    return environment_prefix(plugin_name).removesuffix("_")


def variable_for(plugin_name: str, label: str = ""):
    """The registry entry of that variable, for the start-up check and `manage.py settings`."""
    from postulo.core import configuration

    states = tuple(state.value for state in _states())
    return configuration.Variable(
        variable_name(plugin_name),
        configuration.Kind.CHOICE,
        "plugins",
        "The instance's decision about this plugin: available, unavailable, on or off.",
        default="available",
        choices=states,
        empty_is_unset=True,
        notes=(
            "Available: each person chooses. Unavailable: not offered at all. On and off: "
            "held for everybody. Set, the Plugins page shows it read-only; a person's own "
            "exception, if an administrator made one, still applies."
        ),
        plugin=label or plugin_name,
    )


def environment_variables() -> dict:
    """Every variable a loaded plugin brings, by name: what it declares it reads, and the
    instance's decision about it. For the start-up check and `manage.py settings`."""
    from .base import label_of
    from .registry import environment_variables as declared
    from .registry import plugins

    found = dict(declared())
    for kind in GOVERNED_KINDS:
        for plugin in plugins(kind):
            name = str(getattr(plugin, "name", "") or "")
            if name and not is_required(name):
                decision = variable_for(name, label_of(plugin))
                found.setdefault(decision.name, decision)
    return found


def _states():
    from .models import PluginPolicy

    return list(PluginPolicy.State)


def pinned_state(plugin_name: str) -> str | None:
    """What the environment says about the plugin, as a stored state, or nothing.

    A word that is not one of the four is nothing here: the start-up check names it.
    """
    from postulo.core import configuration

    variable = variable_for(plugin_name)
    if not configuration.is_set(variable.name, variables={variable.name: variable}):
        return None
    value = configuration.read(variable.name, variables={variable.name: variable})
    return value if value in variable.choices else None


def pinned_row(plugin_name: str):
    """The instance's decision as the environment makes it: a row that is never saved."""
    from .models import PluginPolicy

    state = pinned_state(plugin_name)
    if state is None:
        return None
    return PluginPolicy(plugin=plugin_name, person=None, state=state, decided_by=None)


def pinned_rows() -> dict:
    """The decisions the environment makes, by plugin name. Nothing, without a look at the
    plugins, where no variable of the family is set at all."""
    import os

    if not any(name.startswith("POSTULO_PLUGIN_") for name in os.environ):
        return {}
    found = {}
    for name in _governed_names():
        if (pinned := pinned_row(name)) is not None:
            found[name] = pinned
    return found


def instance_rows() -> dict:
    """The instance's decision for each plugin, by name: what is stored, the environment over it."""
    from .models import PluginPolicy

    rows = {row.plugin: row for row in PluginPolicy.objects.filter(person=None)}
    rows.update(pinned_rows())
    return rows


def _governed_names() -> list[str]:
    from .registry import plugins

    return [
        item.name for kind in GOVERNED_KINDS for item in plugins(kind) if not is_required(item.name)
    ]


def _sub_unavailable(plugin_name: str) -> str:
    """Why a sub-plugin cannot work on this instance, or nothing. A decision cannot change it."""
    from .registry import find_sub_plugin

    sub = find_sub_plugin(plugin_name)
    return str(sub.sub.unavailable) if sub is not None else ""


def sub_plugin_enabled(plugin_name: str, sub_id: str, person) -> bool:
    """Whether the sub-plugin ``sub_id`` of the plugin ``plugin_name`` is on for ``person``."""
    from .base import sub_plugin_name

    return decide(sub_plugin_name(plugin_name, sub_id), person).on


def enabled_sub_plugins(plugin_name: str, person) -> list:
    """The `SubPlugin`s of ``plugin_name`` that are on for ``person``, in declared order."""
    from .registry import sub_plugins

    return [entry.sub for entry in sub_plugins(plugin_name) if decide(entry.name, person).on]


def models_person_filter(person):
    """The instance default plus this person's exception, in one query."""
    from django.db.models import Q

    pk = getattr(person, "pk", None)
    return Q(person__isnull=True) | Q(person_id=pk) if pk else Q(person__isnull=True)


def _their_choices(person) -> list[str]:
    profile = getattr(person, "profile", None)
    return list(getattr(profile, "plugins_off", None) or [])


def set_choice(person, plugin_name: str, *, on: bool) -> bool:
    """A person switching a plugin on or off for themselves. Refused where it is not theirs.

    Returns whether anything changed, so a view can say "saved" honestly rather than
    always.
    """
    if not decide(plugin_name, person).theirs:
        return False
    profile = person.profile
    chosen = [name for name in (profile.plugins_off or []) if name != plugin_name]
    if not on:
        chosen.append(plugin_name)
    if chosen == list(profile.plugins_off or []):
        return False
    profile.plugins_off = sorted(chosen)
    profile.save(update_fields=["plugins_off"])
    # The third thing that makes a memoised decision wrong, beside a policy row and the
    # plugins record: the person's own list (#231). It is a column on their profile, so
    # nothing else here would notice.
    forget_decisions()
    return True


def overview(person, *, internal: bool = False) -> list[dict]:
    """Every plugin this person can see, what it is doing, and who said so.

    The page this feeds (#96) exists for one row of it: the one an administrator decided.
    A permission somebody holds over your account should be visible from your own settings
    without your having to ask anybody, or the arrangement is one worth avoiding.

    Plugins that are *unavailable* are left out — that is what unavailable means. A plugin
    forced off is included, because being told it was switched off for you is the whole
    difference between the two states.

    **What Postulo ships is left out unless asked for** (`internal=True`), because it is
    not the person's to switch and a dozen locked rows drowned the ones that are (#200).
    The exception is exactly the row the page exists for: a built-in an administrator has
    decided for this person, or for everybody, stays on the page whatever was asked, so a
    decision held over an account never hides behind a check mark.
    """
    from . import base, kinds, provenance
    from .record import canonicalise, packages_by_distribution
    from .registry import plugins

    # Where each plugin came from, read once for the whole page rather than per row: the
    # answer for an installed one involves the record and the repositories' checksums, and
    # asking eight times would ask eight times (#184).
    marks = {row["name"]: row for row in provenance.status()}
    # An installed plugin's row is named for its distribution (`postulo-imap`), not for what
    # the plugin registers as (`imap`), so the second is found through the package the
    # plugin's code lives in, as `base._from_the_wheel` does (#603).
    by_distribution = {canonicalise(name): row for name, row in marks.items()}
    packages = packages_by_distribution()

    def mark_of(plugin) -> dict:
        top_level = type(plugin).__module__.split(".")[0]
        if top_level != "postulo":
            for distribution in packages.get(top_level, []):
                found = by_distribution.get(canonicalise(distribution))
                if found:
                    return found
        return marks.get(plugin.name, {})

    rows = []
    for kind in GOVERNED_KINDS:
        for plugin in plugins(kind):
            decision = decide(plugin.name, person)
            if not decision.offered:
                continue
            shipped = shipped_inside(plugin.name)
            if shipped and not internal and decision.decided_by != "administrator":
                continue
            mark = mark_of(plugin)
            rows.append(
                {
                    "name": plugin.name,
                    # The instance itself, so the template can ask for a logo rather than
                    # being handed one it has no way to fall back from (#106, #288).
                    "plugin": plugin,
                    "internal": shipped,
                    "label": base.label_of(plugin),
                    "description": base.description_of(plugin),
                    "kind": kind,
                    "kind_label": kinds.label_for(kind),
                    "kind_tone": kinds.tone_for(kind),
                    "provenance": mark.get("provenance", ""),
                    "provenance_label": mark.get("provenance_label", ""),
                    "provenance_explanation": mark.get("provenance_explanation", ""),
                    "on": decision.on,
                    "theirs": decision.theirs,
                    "why": decision.explain(),
                }
            )
    return rows


def plugins_for(person, kind: str) -> list:
    """Every plugin of a kind that acts for this person.

    The one place the rest of the application asks. A call site that filtered by hand would
    be a call site that could forget, and the one that forgets is the one that runs a plugin
    for somebody it was switched off for.
    """
    from .registry import plugins

    if kind not in GOVERNED_KINDS:
        return plugins(kind)
    return [item for item in plugins(kind) if decide(item.name, person).on]


def allows(connection) -> bool:
    """Whether the policy lets this connection's plugin act for the connection's owner (#362).

    `Connection.plugin_instance` is what the list page uses, which must still show the row of
    a plugin that was switched off. Whatever *runs* the plugin asks this as well, or
    :func:`active_plugin`, so the decision the pages make is the decision the scheduler keeps.
    A function here rather than a property on the model, which the policy reads.
    """
    return decide(connection.plugin, connection.owner).on


def active_plugin(connection):
    """The connection's plugin, only when it is installed and the policy has it on."""
    return connection.plugin_instance if allows(connection) else None


def refused_connections(connections) -> list[int]:
    """The ids, among these connections, whose plugin is off for the connection's owner.

    For the passes that pick rows to work on: a delivery or a copy whose plugin was switched
    off waits, untouched, and is left out of the pass rather than tried and failed (#362).
    """
    return [
        connection.pk
        for connection in connections.select_related("owner")
        if not decide(connection.plugin, connection.owner).on
    ]


def connected_plugins(person=None) -> list:
    """Every installed plugin a person can connect to, whatever its kind.

    A built-in that needs nothing from anyone — the local document store — says so with
    ``needs_connection = False`` and is left off the list: there is no form to draw.

    Given a person, what an administrator has decided for them applies (#95). Without one
    this is every installed plugin, which is what an administration page wants.

    Here rather than in the registry because it asks this module when it is given a person:
    the registry is what everything below the models reads, and it reached up into the policy
    through an import moved inside the function (#248).
    """
    from . import registry
    from .base import CONNECTED_KINDS

    found: list = []
    for kind in CONNECTED_KINDS:
        of_kind = plugins_for(person, kind) if person is not None else registry.plugins(kind)
        found.extend(p for p in of_kind if getattr(p, "needs_connection", True))
    return found
