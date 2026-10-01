"""Context available to every template."""

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext as _

# The running version is asked by pages and by the update check alike, so it lives with the
# check (#248). Read here for the footer, and still handed out here to the health endpoint.
from .updates import installed_version

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
        # Read off <body> by the stylesheet alone; nothing scripts this one.
        "nav_underline": nav_underline,
        # Likewise: the stylesheet tightens what it tightens and no script is involved.
        "ui_density": density,
        "nav_items": navigation.visible_items(profile),
        "dashboard_hidden": navigation.dashboard_hidden(profile),
        "registration_open": site.signup_open_now(),
        "instance_name": name,
        "instance_tagline": site.tagline(),
        # Whether an administrator gave this instance a name of its own. The footer then
        # says that name where it said Postulo's slogan, which belongs to an instance
        # nobody has named (#212).
        "instance_named": name != "Postulo",
        "postulo_version": installed_version(),
        # The foot of every page (#212). The version's release notes are shown only to
        # somebody signed in, and the template decides that; these are only addresses.
        "postulo_release_url": release_notes_url(),
        "postulo_source_url": settings.POSTULO_SOURCE_URL,
        "postulo_help_url": HELP_URL,
        "postulo_legal_notice_url": settings.POSTULO_LEGAL_NOTICE_URL,
    }


#: The documentation, which is the upstream wiki whoever runs the instance: an operator's
#: own changes are theirs to document, and Postulo's pages are still Postulo's (#212).
HELP_URL = "https://source.tiagoagueda.com/postulo/postulo/wiki"

#: Where a release's notes are: the Forgejo release the release workflow makes from the
#: changelog, under the tag `v` + the version (#212).
RELEASE_NOTES_URL = "https://source.tiagoagueda.com/postulo/postulo/releases/tag/v{version}"


def release_notes_url() -> str:
    """The notes of the release this code is.

    From ``__version__`` rather than from the installed metadata the footer prints, because
    the tag is ``v`` + ``__version__`` exactly -- `release_tools.py check` refuses a tag that
    is anything else -- and packaging normalises the other (``1.0.0-rc.1`` is installed as
    ``1.0.0rc1``). The upstream release even where `POSTULO_SOURCE_URL` names a fork: the
    number is upstream's, and so are its notes.

    A build between two releases carries the number of the last one, and nothing a build
    records says which commit it was made from, so this is that release's notes. Linking a
    commit would need the image to be told it at build time; until then there is nothing
    true to link to.
    """
    from postulo import __version__

    return RELEASE_NOTES_URL.format(version=__version__)
