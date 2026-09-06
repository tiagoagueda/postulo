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
    return {
        "current": choice,
        "next": following,
        "title": _("Theme: %(current)s. Switch to: %(next)s.")
        % {"current": labels[choice], "next": labels[following]},
    }


def ui(request: HttpRequest) -> dict:
    """Interface-wide values: the resolved theme, the navigation, the instance's policy."""
    from . import navigation

    theme = ""
    choice = "system"
    profile = None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        if profile:
            choice = profile.theme
        # "system" means stamp nothing and let the operating system preference apply.
        if choice in {"light", "dark"}:
            theme = choice
    from . import site

    return {
        "ui_theme": theme,
        "theme_switch": theme_switch(choice),
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
