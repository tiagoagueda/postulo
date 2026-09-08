from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.core"
    label = "core"
    verbose_name = _("Core")

    def ready(self) -> None:
        from postulo.plugins import registry

        # Registers the dashboard widgets core owns.
        from . import widgets_builtin  # noqa: F401
        from .features import PhoneNumbersFeature

        registry.register_builtin("feature", PhoneNumbersFeature)
