"""Context available to every template."""

from django.conf import settings
from django.http import HttpRequest


def ui(request: HttpRequest) -> dict:
    """Interface-wide values: the resolved theme and the instance's registration policy."""
    theme = ""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        # "system" means stamp nothing and let the operating system preference apply.
        if profile and profile.theme in {"light", "dark"}:
            theme = profile.theme
    return {
        "ui_theme": theme,
        "registration_open": settings.POSTULO_REGISTRATION_OPEN,
    }
