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
        from postulo.plugins.gdpr import GdprFeature
        from postulo.plugins.maps import MapsFeature
        from postulo.plugins.messaging_contacts import MessagingContactsFeature
        from postulo.plugins.phone_numbers import PhoneNumbersFeature
        from postulo.plugins.postal_rules import PostalRulesFeature
        from postulo.plugins.repositories import RepositoriesFeature
        from postulo.plugins.social_profiles import SocialProfilesFeature
        from postulo.plugins.websites import WebsitesFeature

        # Registers the dashboard widgets core owns, and the export archive as a piece
        # of slow work (#247), and the configuration checks `manage.py check` runs before
        # the first request (#233).
        from . import checks, signals, slow, widgets_builtin  # noqa: F401

        registry.register_builtin("feature", PhoneNumbersFeature)
        # How somebody is reached on Signal, Matrix, Telegram, XMPP (#682).
        registry.register_builtin("feature", MessagingContactsFeature)
        # What a country expects of an address, and what it calls each part (#147).
        registry.register_builtin("feature", PostalRulesFeature)
        # An employer as a structure rather than a single name (#138).
        registry.register_builtin("feature", EmployerStructureFeature)
        # A person's addresses on the web, three kinds and a switch for each (#189).
        registry.register_builtin("feature", SocialProfilesFeature)
        registry.register_builtin("feature", RepositoriesFeature)
        registry.register_builtin("feature", WebsitesFeature)
        # What the instance keeps on other people, and the duties that go with it (#297).
        registry.register_builtin("feature", GdprFeature)
        # The companies on a map, placed from an offline table of cities (#700).
        registry.register_builtin("feature", MapsFeature)

        # The contact channels Postulo already has, described by one contract (#146).
        from . import channels

        channels.register_the_ones_that_exist()

        # SQLite's LIKE folds the case of ASCII letters only (#505).
        from . import sqlite_like

        sqlite_like.connect()

        # The identifier schemes this instance defines for itself are kept on the policy
        # row, and the registry is told so here: it cannot import the row's module (#311).
        from . import identifiers, site

        identifiers.kept_on(site.current)
