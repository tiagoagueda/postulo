from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.core"
    label = "core"
    verbose_name = _("Core")

    def ready(self) -> None:
        from postulo.plugins import registry
        from postulo.plugins.employer_structure import EmployerStructureFeature
        from postulo.plugins.phone_numbers import PhoneNumbersFeature
        from postulo.plugins.postal_rules import PostalRulesFeature

        # Registers the dashboard widgets core owns.
        from . import widgets_builtin  # noqa: F401

        registry.register_builtin("feature", PhoneNumbersFeature)
        # What a country expects of an address, and what it calls each part (#147).
        registry.register_builtin("feature", PostalRulesFeature)
        # An employer as a structure rather than a single name (#138).
        registry.register_builtin("feature", EmployerStructureFeature)

        # The contact channels Postulo already has, described by one contract (#146).
        from . import channels

        channels.register_the_ones_that_exist()
