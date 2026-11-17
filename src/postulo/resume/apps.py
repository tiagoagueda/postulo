from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ResumeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.resume"
    label = "resume"
    verbose_name = _("Resume")

    def ready(self) -> None:
        from postulo.core import transfers
        from postulo.plugins import registry
        from postulo.plugins.europass import EuropassImporter

        # The importers that ship, and the entry each has behind the career's *Import*
        # button (#1260): in the order the menu lists them.
        for place, importer in enumerate((EuropassImporter,)):
            registry.register_builtin("importer", importer)
            entry = transfers.importer_entry(importer, 100 + place)
            if entry.key not in transfers.REGISTRY:
                transfers.register(entry)

        from . import suggesters  # noqa: F401  (registers what the career forms suggest, #1251)
