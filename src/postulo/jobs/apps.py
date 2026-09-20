from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.jobs"
    label = "jobs"
    verbose_name = _("Jobs")

    def ready(self) -> None:
        # Registers the table with the settings view.
        # Registers this app's dashboard widgets.
        # Registers the two pieces of slow work this app sends off (#247).
        from . import (
            slow,  # noqa: F401
            tables,  # noqa: F401
            widgets,  # noqa: F401
        )
