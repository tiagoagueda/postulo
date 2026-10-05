"""Whether a plugin is on for a person, and who decided.

One place, because the alternative is every call site remembering — and the call site that
forgets is the one that lets a plugin run for somebody an administrator switched it off
for.

**The order, and why it is this order.**

1. *Switched off for the instance.* A plugin an administrator has disabled is not loaded at
   all, so nothing below can turn it back on for anybody. This one is not really a policy
   decision; it is the code not being there.
2. *An administrator's decision about this person.*
3. *An administrator's decision about everybody.*
4. *The person's own choice*, which applies wherever an administrator has not taken it
   away -- **and only to a plugin installed on the instance.** A plugin shipped inside
   Postulo is the administrator's to switch, for one person or for everybody, and never
   the person's (#200): what Postulo itself does is a decision about the instance, taken
   by whoever runs it, and a page of a dozen built-in switches was a page of things
   nobody meant to be a choice.

**Forcing a plugin on does not make it run.** A notifier, store or sync needs a
``Connection`` holding credentials only the person can supply, so *on* means "this is
available to you and you may not switch it off" rather than "this is now sending your
documents somewhere". Sources and features are the exception: they need nothing from
anybody, so forcing one on genuinely turns it on. The interface has to say which of those
it means, and :func:`decide` returns enough for it to.

**Switching a feature off does not delete what it governs.** It stops Postulo offering and
using that part of itself; the rows stay, and switching it back on finds them unchanged.
That is the same promise every other kind here makes, and it is not weaker for the kind
whose subject happens to be Postulo's own tables.

**Transports are exempt.** Mail delivery is instance infrastructure rather than a capability
a person holds, and *forced off* for a transport would be an account nobody can recover
(#104). They are not offered here at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from postulo.core import memo

# The forgetting lives with the memo, which a policy row can import when it is saved; it is
# handed out here too -- the middleware and the scheduler call it here (#248).
from postulo.core.memo import forget_decisions

#: Kinds a person may hold an opinion about. A transport is deliberately not one of them.
GOVERNED_KINDS = ("source", "notifier", "store", "sync", "importer", "feature", "outbox")

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
#: services a handle can be on (#682).
UNGOVERNED_KINDS = ("transport", "identifier", "link-service", "messaging-service")


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

    @property
    def imposed(self) -> bool:
        return self.decided_by in {"administrator", "instance"}

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
        return {
            "instance": _("Switched off for this whole instance."),
            "administrator": _("Set for your account."),
            "shipped": "",
            "person": _("You chose this."),
            "default": _("Available; you have not changed it."),
            "infrastructure": _("How this instance works, rather than a choice anybody holds."),
        }[self.decided_by]


def _switched_off_for_the_instance(name: str) -> bool:
    from .installing import canonicalise, disabled_names

    try:
        return canonicalise(name) in disabled_names()
    except Exception:  # pragma: no cover - a broken record must not decide anything
        return False


def shipped_inside(plugin_name: str) -> bool:
    """Whether this plugin ships inside Postulo, which makes it the administrator's.

    The same question `installing.is_internal` answers for the plugins page: a built-in
    registered by an app's `ready()`, as against one installed on the volume or loaded
    from an entry point. Asked by name because that is what a policy row holds.
    """
    from .installing import is_internal

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


def _answers() -> dict:
    """This request's answers, in `memo.decisions` (#231)."""
    from .installing import record_stamp

    stamp = record_stamp()
    held = getattr(memo.decisions, "answers", None)
    if held is None or held[0] != stamp:
        held = (stamp, {})
        memo.decisions.answers = held
    return held[1]


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

    if _switched_off_for_the_instance(plugin_name):
        return Decision(on=False, offered=False, theirs=False, decided_by="instance")

    rows = {
        row.person_id: row
        for row in PluginPolicy.objects.filter(plugin=plugin_name).filter(
            models_person_filter(person)
        )
    }
    row = rows.get(getattr(person, "pk", None)) or rows.get(None)
    state = row.state if row else PluginPolicy.State.AVAILABLE

    if state == PluginPolicy.State.UNAVAILABLE:
        return Decision(
            on=False, offered=False, theirs=False, decided_by="administrator", who=row.decided_by
        )
    if state == PluginPolicy.State.FORCED_ON:
        return Decision(
            on=True, offered=True, theirs=False, decided_by="administrator", who=row.decided_by
        )
    if state == PluginPolicy.State.FORCED_OFF:
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
