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
   away.

**Forcing a plugin on does not make it run.** A notifier, store or sync needs a
``Connection`` holding credentials only the person can supply, so *on* means "this is
available to you and you may not switch it off" rather than "this is now sending your
documents somewhere". Sources are the exception: they are stateless and need nothing, so
forcing one on genuinely turns it on. The interface has to say which of those it means, and
:func:`decide` returns enough for it to.

**Transports are exempt.** Mail delivery is instance infrastructure rather than a capability
a person holds, and *forced off* for a transport would be an account nobody can recover
(#104). They are not offered here at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

#: Kinds a person may hold an opinion about. A transport is deliberately not one of them.
GOVERNED_KINDS = ("source", "notifier", "store", "sync", "importer")


@dataclass(frozen=True)
class Decision:
    """What is true for one plugin and one person, and who made it true."""

    #: Whether the plugin acts for this person at all.
    on: bool
    #: Whether they even see that it exists.
    offered: bool
    #: Whether they may change it.
    theirs: bool
    #: ``instance``, ``administrator``, ``person`` or ``default`` — for #96 to show.
    decided_by: str

    @property
    def imposed(self) -> bool:
        return self.decided_by in {"administrator", "instance"}

    def explain(self):
        return {
            "instance": _("Switched off for this whole instance."),
            "administrator": _("An administrator decided this for your account."),
            "person": _("You chose this."),
            "default": _("Available; you have not changed it."),
        }[self.decided_by]


def _switched_off_for_the_instance(name: str) -> bool:
    from .installing import canonicalise, disabled_names

    try:
        return canonicalise(name) in disabled_names()
    except Exception:  # pragma: no cover - a broken record must not decide anything
        return False


def decide(plugin_name: str, person) -> Decision:
    """The one answer, for one plugin and one person."""
    from .models import PluginPolicy

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
        return Decision(on=False, offered=False, theirs=False, decided_by="administrator")
    if state == PluginPolicy.State.FORCED_ON:
        return Decision(on=True, offered=True, theirs=False, decided_by="administrator")
    if state == PluginPolicy.State.FORCED_OFF:
        return Decision(on=False, offered=True, theirs=False, decided_by="administrator")

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
    return True


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
