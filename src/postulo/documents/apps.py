from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.documents"
    label = "documents"
    verbose_name = _("Documents")

    def ready(self) -> None:
        from postulo.plugins import registry

        from . import signals  # noqa: F401 - connects the receivers
        from .stores import LocalStore

        registry.register_builtin("store", LocalStore)
