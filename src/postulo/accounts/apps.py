from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.accounts"
    label = "accounts"
    verbose_name = _("Accounts")

    def ready(self) -> None:
        from postulo.plugins import registry
        from postulo.plugins.email_addresses import EmailAddressesFeature

        from . import signals  # noqa: F401  (registers the receivers)

        # Governs the *page*, not the addresses: those are allauth's, with allauth's
        # flows reading them, and a plugin that cannot verify an address cannot
        # honestly own one (#145).
        registry.register_builtin("feature", EmailAddressesFeature)
