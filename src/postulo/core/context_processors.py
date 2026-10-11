"""Context available to every template."""

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext as _

# The running version is asked by pages and by the update check alike, so it lives with the
# check (#248). Read here for the footer, and still handed out here to the health endpoint.

#: What one press of the header switch moves to. Light, dark, then back to the system.
NEXT_THEME = {"light": "dark", "dark": "system", "system": "light"}


def theme_switch(choice: str) -> dict:
    """What the header switch needs to draw itself for ``choice``."""
    from postulo.accounts.models import Theme

    if choice not in NEXT_THEME:
        choice = "system"
    labels = dict(Theme.choices)
    following = NEXT_THEME[choice]
    # The row of the account menu says the current theme in a word (#197): the full
    # sentence, with what pressing does, is the title -- a menu row that wrapped to three
    # lines was a 76-pixel target, and the closed menu's boxes sat on the page beneath.
    short = {"system": _("System"), "light": _("Light"), "dark": _("Dark")}
    return {
        "current": choice,
        "next": following,
        "label": _("Theme: %(theme)s") % {"theme": short[choice]},
        "title": _("Theme: %(current)s. Switch to: %(next)s.")
        % {"current": labels[choice], "next": labels[following]},
    }


def ui(request: HttpRequest) -> dict:
    """Interface-wide values: the resolved theme, the direction, the navigation, the policy."""
    from . import languages, navigation

    theme = ""
    choice = "system"
    profile = None
    # On unless somebody has said otherwise, which is also the answer where nobody is signed
    # in: there is no profile to ask, and the pages a stranger sees have no shortcuts (#227).
    shortcuts = True
    # Likewise on: the badges are a default, and a stranger has none of the keys (#658).
    key_hints = True
    # Likewise on unless somebody has said otherwise: the default is what the conformance
    # claim rests on, and a stranger has no profile to ask (#289).
    nav_underline = True
    # Comfortable unless somebody has asked for less, which is also the answer where nobody
    # is signed in: the generous interface is the one the conformance claim rests on (#292).
    density = "comfortable"
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        if profile:
            choice = profile.theme
            shortcuts = profile.keyboard_shortcuts
            key_hints = profile.show_key_hints
            nav_underline = profile.nav_underline
            density = profile.density
        # "system" means stamp nothing and let the operating system preference apply.
        if choice in {"light", "dark"}:
            theme = choice
    from . import site

    name = site.instance_name()
    return {
        "ui_theme": theme,
        # As Postulo writes it, over Django's answer of the same name, which is in lower
        # case: this processor runs after Django's own, so this is the one a page sees
        # (#337).
        "LANGUAGE_CODE": languages.current(),
        # Postulo's own answer rather than Django's LANGUAGE_BIDI, so that the interface
        # and a rendered document agree and a language Django has never heard of still
        # gets a direction (#43 goes well past the set Django ships with).
        "text_direction": languages.direction(languages.current()),
        "theme_switch": theme_switch(choice),
        # Read by `app.js` off <body>, and by the pages that document a key so that they do
        # not promise one that is switched off.
        "keyboard_shortcuts": shortcuts,
        "key_hints": key_hints,
        # Read off <body> by the stylesheet alone; nothing scripts this one.
        "nav_underline": nav_underline,
        # Likewise: the stylesheet tightens what it tightens and no script is involved.
        "ui_density": density,
        "nav_items": navigation.visible_items(profile),
        "dashboard_hidden": navigation.dashboard_hidden(profile),
        "registration_open": site.signup_open_now(),
        "instance_name": name,
        "instance_tagline": site.tagline(),
        # The footer's line, which the administrator sets (#835). Not the tagline, which
        # is the home page's and the web app's description.
        "instance_motto": site.motto(),
        # Whether an administrator gave this instance a name of its own. The footer then
        # says that name where it said Postulo's slogan, which belongs to an instance
        # nobody has named (#212).
        "instance_named": name != "Postulo",
        # The foot of every page (#212): only addresses. Nobody is told the version there
        # any more (#836); it is on /healthz and under Server settings.
        "postulo_releases_url": releases_url(),
        "postulo_source_url": settings.POSTULO_SOURCE_URL,
        # The mark beside it, only where it names the place the link goes (#837).
        "postulo_source_brand": source_brand(),
        "postulo_help_url": HELP_URL,
        "postulo_legal_notice_url": settings.POSTULO_LEGAL_NOTICE_URL,
    }


#: The documentation, which is the upstream wiki whoever runs the instance: an operator's
#: own changes are theirs to document, and Postulo's pages are still Postulo's (#212).
HELP_URL = "https://source.tiagoagueda.com/postulo/postulo/wiki"

#: Hosts known to run Forgejo: the upstream repository's, and Codeberg's. A fork on another
#: forge, or on an operator's own server, gets the words alone: the mark is Forgejo's, and a
#: Forgejo mark beside a GitHub address names the wrong place (`TRADEMARKS.md`, #837).
FORGEJO_HOSTS = frozenset({"source.tiagoagueda.com", "codeberg.org"})


def source_brand() -> str:
    """The mark for the "Source code" link: ``forgejo`` on a Forgejo host, else nothing."""
    from urllib.parse import urlsplit

    host = (urlsplit(settings.POSTULO_SOURCE_URL).hostname or "").lower()
    return "forgejo" if host in FORGEJO_HOSTS or host.startswith("forgejo.") else ""


#: Where the releases are: the Forgejo releases the release workflow makes from the changelog,
#: all of them, so the footer says what Postulo is without saying which build this is (#836).
RELEASES_URL = "https://source.tiagoagueda.com/postulo/postulo/releases/"


def releases_url() -> str:
    """The page of Postulo's releases, upstream's even where the operator's source is a fork.

    The footer used to link the notes of the running version, which told anybody who could
    see it which version to look for advisories against. It now links all of them, and the
    version is for people who ask the instance (`/healthz`, Server settings).
    """
    return RELEASES_URL
