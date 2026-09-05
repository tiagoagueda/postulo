from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    name = "postulo.notifications"
    label = "notifications"
    verbose_name = _("Notifications")

    def ready(self) -> None:
        # The built-in notifier is a plugin like any other; it merely ships in the box.
        from postulo.plugins.registry import register_builtin

        from .email import EmailNotifier

        register_builtin("notifier", EmailNotifier)
