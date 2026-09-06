from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PluginsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.plugins"
    label = "plugins"
    verbose_name = _("Plugins")

    def ready(self) -> None:
        """Put the data volume's plugins directory on the import path, if there is one.

        Before anything reads entry points, so a plugin installed through the interface
        is found exactly as one installed with pip would be.
        """
        from .installing import activate

        activate()
