from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApplicationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.applications"
    label = "applications"
    verbose_name = _("Applications")

    def ready(self) -> None:
        # Registers the table with the settings view.
        # Registers this app's dashboard widgets.
        from . import (
            tables,  # noqa: F401
            widgets,  # noqa: F401
        )
