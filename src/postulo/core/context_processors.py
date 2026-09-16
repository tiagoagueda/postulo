"""Context available to every template."""

from django.http import HttpRequest
from django.utils.translation import gettext as _

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
    from django.utils.translation import get_language

    from . import languages, navigation

    theme = ""
    choice = "system"
    profile = None
    # On unless somebody has said otherwise, which is also the answer where nobody is signed
    # in: there is no profile to ask, and the pages a stranger sees have no shortcuts (#227).
    shortcuts = True
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        if profile:
            choice = profile.theme
            shortcuts = profile.keyboard_shortcuts
        # "system" means stamp nothing and let the operating system preference apply.
        if choice in {"light", "dark"}:
            theme = choice
    from . import site

    return {
        "ui_theme": theme,
        # Postulo's own answer rather than Django's LANGUAGE_BIDI, so that the interface
        # and a rendered document agree and a language Django has never heard of still
        # gets a direction (#43 goes well past the set Django ships with).
        "text_direction": languages.direction(get_language() or ""),
        "theme_switch": theme_switch(choice),
        # Read by `app.js` off <body>, and by the pages that document a key so that they do
        # not promise one that is switched off.
        "keyboard_shortcuts": shortcuts,
        "nav_items": navigation.visible_items(profile),
        "dashboard_hidden": navigation.dashboard_hidden(profile),
        "registration_open": site.signup_open_now(),
        "instance_name": site.instance_name(),
        "instance_tagline": site.tagline(),
        "postulo_version": installed_version(),
    }


def installed_version() -> str:
    """The version of the package that is actually running.

    From the installed distribution's metadata when there is one — which is what a
    wheel or an image carries — and from the package itself otherwise, so a source
    checkout says the same thing.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("postulo")
    except PackageNotFoundError:  # pragma: no cover - a checkout without an install
        from postulo import __version__

        return __version__
