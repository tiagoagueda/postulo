from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PluginsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.plugins"
    label = "plugins"
    verbose_name = _("Plugins")
