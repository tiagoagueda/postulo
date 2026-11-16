from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ResumeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.resume"
    label = "resume"
    verbose_name = _("Resume")

    def ready(self) -> None:
        from postulo.plugins import registry
        from postulo.plugins.europass import EuropassImporter

        registry.register_builtin("importer", EuropassImporter)

        from . import suggesters  # noqa: F401  (registers what the career forms suggest, #1251)
