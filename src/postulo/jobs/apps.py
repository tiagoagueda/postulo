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
        # Registers the pieces of slow work this app sends off (#247).
        # Connects the receiver that removes a kept page's files with its row (#256), and
        # the ones that unlink a listing's history from a capture or a file that goes (#270).
        # Registers the system check that names two ESCO revisions in the data directory (#535).
        from . import (
            checks,  # noqa: F401
            signals,  # noqa: F401
            slow,  # noqa: F401
            tables,  # noqa: F401
            widgets,  # noqa: F401
        )
