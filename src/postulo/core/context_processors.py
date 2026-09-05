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
    """Interface-wide values: the resolved theme and the instance's registration policy."""
    theme = ""
    choice = "system"
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
        "registration_open": site.signup_open_now(),
        "instance_name": site.instance_name(),
        "instance_tagline": site.tagline(),
    }
