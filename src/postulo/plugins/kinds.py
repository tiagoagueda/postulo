"""What a kind of plugin is called, and what colour it wears.

Two pages draw a plugin's kind beside its name — a person's *Settings → Plugins* and an
administrator's *Server → Plugins* — and until #184 both drew the raw slug, so a French
reader was shown "source" and "notifier" in English, in the same grey pill the *origin* was
drawn in. Two different facts wearing one badge, and one of them untranslated.

**The kind takes colour and the origin stays grey.** That is not decoration. A kind is a
category out of a small fixed set, which is what colour is good for; where a plugin came
from is a fact nobody should read as a rating, and `server/plugins.html` has said so since
#94 — *"deliberately not styled as a reassurance: installing a plugin runs somebody else's
code whatever this says"*. A green *Official* badge would undo that sentence.

Colour is never the only carrier: every tag says its own word, and the word is what a screen
reader announces. The palette repeats across the kinds on purpose — eight or nine hues told
apart at a glance is more than any palette honestly gives, so kinds that are rarely seen
together share one and lean on the word.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

#: What each kind is called, in the reader's language. Keyed by the slug a plugin declares.
LABELS: dict[str, str] = {
    "source": _("Source"),
    "notifier": _("Notifications"),
    "store": _("Document store"),
    "sync": _("Synchronisation"),
    "importer": _("Importer"),
    "feature": _("Feature"),
    "transport": _("Transport"),
    "identifier": _("Identifiers"),
    "link-service": _("Link services"),
}

#: The badge tone each kind wears: `data-tone` on a `.badge`, painted in
#: `assets/css/basecoat.css` from the same palette a tag chooses from (#291).
TONES: dict[str, str] = {
    "source": "blue",
    "notifier": "amber",
    "store": "violet",
    "sync": "teal",
    "importer": "green",
    "feature": "rose",
    "transport": "teal",
    "identifier": "violet",
    "link-service": "blue",
}


def label_for(kind: str) -> str:
    """The kind's name, or the slug itself for a kind this version does not know.

    A third-party plugin may declare a kind added after this release, and showing the slug
    is better than showing nothing: it is at least what the plugin calls itself.
    """
    return LABELS.get(kind, kind)


def tone_for(kind: str) -> str:
    """The badge tone for a kind, falling back to the neutral one."""
    return TONES.get(kind, "grey")
