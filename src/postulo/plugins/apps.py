from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PluginsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "postulo.plugins"
    label = "plugins"
    verbose_name = _("Plugins")

    def ready(self) -> None:
        """Put the data volume's plugins directory on the import path, if there is one.

        Before anything reads entry points, so a plugin installed through the interface
        is found exactly as one installed with pip would be.
        """
        from .identifiers import Identifiers
        from .installing import activate
        from .link_services import LinkServices
        from .messaging_services import MessagingServices
        from .register_lookup import RegisterAdapters
        from .registry import (
            load_everything,
            register_builtin,
            register_builtin_locales,
            register_builtin_themes,
        )

        activate()
        _forget_decisions_when_their_rows_change()
        # The registry of identifier schemes, registered before the catalogues are swept so
        # its own labels come from its own locale/ like every other plugin's (#109).
        register_builtin("identifier", Identifiers)
        # And the registry of the services a web link can be on, for the same reason (#305).
        register_builtin("link-service", LinkServices)
        # And the services a messaging handle can be on, the same way (#682).
        register_builtin("messaging-service", MessagingServices)
        # And the company registers Register lookup may ask (#1021): a registry, which the
        # feature beside it switches.
        register_builtin("register-adapter", RegisterAdapters)
        register_builtin_locales()
        register_builtin_themes()
        # And now import what is installed, so a plugin that will not load says so in the
        # start-up log rather than inside somebody's request (#228).
        load_everything()


def _forget_decisions(**_kwargs) -> None:
    from postulo.core.memo import forget_decisions

    forget_decisions()


def _forget_decisions_when_their_rows_change() -> None:
    """Drop the request's plugin decisions whenever what they were made from changes (#1112).

    A request reads every policy row and access rule that can reach a person once, and decides
    from those. A row saved or deleted through its model already says so (`PluginPolicy._forget`).
    These are the changes that go round it: rows deleted through a queryset, as switching a
    module back on deletes its sub-modules', or with the account or group they name; and
    somebody joining or leaving a group, which changes which rules reach them without a rule
    being touched. Signals, so that no page, admin action or shell has to remember.

    This reaches the thread that made the change, which is the one that would otherwise go on
    answering from what it read before. Every other request reads its own rows as it begins.
    """
    from django.contrib.auth import get_user_model
    from django.db.models.signals import m2m_changed, post_delete

    from .models import PluginAccess, PluginPolicy

    for model in (PluginPolicy, PluginAccess):
        post_delete.connect(
            _forget_decisions,
            sender=model,
            dispatch_uid=f"plugins.forget_decisions.{model.__name__}",
        )
    m2m_changed.connect(
        _forget_decisions,
        sender=get_user_model().groups.through,
        dispatch_uid="plugins.forget_decisions.membership",
    )
