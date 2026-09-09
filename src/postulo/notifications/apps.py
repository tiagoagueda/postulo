from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NotificationsConfig(AppConfig):
    name = "postulo.notifications"
    label = "notifications"
    verbose_name = _("Notifications")

    def ready(self) -> None:
        # Both ship in the box and both are plugins like any other. They are two plugins of
        # two kinds on purpose: the notifier decides something is worth telling somebody and
        # writes the words, the transport gets those words off this machine. One sits on the
        # other, and merging them would make notification settings and delivery settings the
        # same form (#104).
        from postulo.plugins.email import EmailNotifier
        from postulo.plugins.own_mail import OwnMail
        from postulo.plugins.registry import register_builtin
        from postulo.plugins.smtp import SMTPTransport

        register_builtin("notifier", EmailNotifier)
        # The person's half of the mail split. A connected kind rather than a second
        # transport, so it is theirs to switch off and can never be a way back in (#149).
        register_builtin("outbox", OwnMail)
        register_builtin("transport", SMTPTransport)
