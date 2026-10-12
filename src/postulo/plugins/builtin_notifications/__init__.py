"""Built-in notifications: the module the notifiers Postulo ships are sub-modules of.

Browser notifications, Email notifications and Webhooks are the ways Postulo can tell a
person, or a machine, that something happened without anybody installing anything (#876).
They were three rows among the plugins, and they are one module now with the three as its
sub-modules, so the page can say what they have in common and give an administrator one
switch for the whole family.

**A grouping row, and nothing else.** It sends nothing and has no settings. Switching it off
is the cascade of :mod:`postulo.plugins.policy`: the three are off and their switches are
taken away, and a notifier that is off is left out of every pass, so a delivery waits
untouched instead of failing (#362). A plugin someone installs that is also a notifier is not
in this family; it is a plugin, and its own switch governs it.

*Your own email* (``own-mail``) is not a notification, it is how a person's mail leaves, and
*SMTP* is plumbing nobody holds an opinion about. Neither belongs here.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import declares, shipped

#: The identifier the policy rows key on, and what each notifier names as its parent.
BUILTIN_NOTIFICATIONS = "builtin-notifications"


@declares(
    shipped(
        name=BUILTIN_NOTIFICATIONS,
        label=_("Built-in notifications"),
        kind="feature",
        categories=("notifications",),
        icon="bell",
        description=_(
            "The ways Postulo can tell you, or a machine, that something happened without "
            "installing anything: a notification in your browser, an email, a signed "
            "message posted to an address. This is the switch for all three at once; each "
            "can still be switched off on its own while this is on. Switched off, none of "
            "them sends, and what was set up stays as it is."
        ),
    )
)
class BuiltinNotificationsFeature:
    """The module the notifiers that ship with Postulo belong to. It sends nothing itself."""
