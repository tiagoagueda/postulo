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
        from postulo.plugins.file_formats import (
            DocxFormat,
            FileFormatsFeature,
            ICalendarFormat,
            OdtFormat,
            PdfFormat,
            VCardFormat,
        )
        from postulo.plugins.gdpr import GdprFeature
        from postulo.plugins.maps import MapsFeature
        from postulo.plugins.messaging_contacts import MessagingContactsFeature
        from postulo.plugins.multiple_fields import MultipleFieldsFeature
        from postulo.plugins.phone_numbers import PhoneNumbersFeature
        from postulo.plugins.postal_rules import PostalRulesFeature
        from postulo.plugins.repositories import RepositoriesFeature
        from postulo.plugins.social_profiles import SocialProfilesFeature
        from postulo.plugins.websites import WebsitesFeature

        # Registers the dashboard widgets core owns, and the export archive as a piece
        # of slow work (#247), and the configuration checks `manage.py check` runs before
        # the first request (#233).
        from . import checks, safe_choices, signals, slow, transfers, widgets_builtin  # noqa: F401

        # A number past what the database holds is an invalid choice, not a 500 (#767).
        safe_choices.install()

        # What the Calendar, Your details and Your career can be imported from and exported
        # to, said once so a plugin can add a format (#862).
        transfers.register_the_ones_postulo_has()

        # The module the file-format sub-modules belong to: PDF, Word, OpenDocument, iCalendar
        # and vCard, with Europass beside them (#877).
        for format_module in (
            FileFormatsFeature,
            PdfFormat,
            DocxFormat,
            OdtFormat,
            ICalendarFormat,
            VCardFormat,
        ):
            registry.register_builtin("feature", format_module)
        # The module the "multiple …" features are sub-modules of (#875).
        registry.register_builtin("feature", MultipleFieldsFeature)
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
