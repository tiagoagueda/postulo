from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.documents"
    label = "documents"
    verbose_name = _("Documents")

    def ready(self) -> None:
        from postulo.plugins import registry
        from postulo.plugins.localstore import LocalStore

        from . import kinds, signals  # noqa: F401 - connects the receivers

        # What kinds of document exist, said once. Here rather than at import time,
        # because a registry filled while a module loads is a registry that is empty in
        # whichever test imported it first (#133).
        kinds.register_the_ones_postulo_has()

        registry.register_builtin("store", LocalStore)
