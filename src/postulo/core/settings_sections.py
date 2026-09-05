"""The sections of the Settings area, and how a plugin adds one.

Settings is a frame: a sidebar of sections, each its own page. The core fills it with the
built-in sections below; a plugin with per-person settings registers its own at import or
``ready()`` time and it appears in the sidebar like the rest, in the order it asked for.
Nothing here knows what a section shows — only where it is and what it is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class SettingsSection:
    slug: str
    label: str
    url_name: str
    icon: str = "settings"
    order: int = 100
    #: Fully qualified URL names (``namespace:name``, or allauth's bare names) that mean
    #: this section is the one being looked at, beyond ``url_name`` itself.
    match: tuple[str, ...] = field(default_factory=tuple)

    def is_active(self, request: HttpRequest) -> bool:
        resolved = getattr(request, "resolver_match", None)
        if resolved is None:
            return False
        name = resolved.view_name
        return name == self.url_name or name in self.match


BUILTIN: tuple[SettingsSection, ...] = (
    SettingsSection(
        slug="appearance",
        label=_("Appearance"),
        url_name="settings:appearance",
        icon="sun-moon",
        order=10,
    ),
    SettingsSection(
        slug="locale",
        label=_("Language and time"),
        url_name="settings:locale",
        icon="calendar",
        order=20,
    ),
    SettingsSection(
        slug="account",
        label=_("Account"),
        url_name="settings:account",
        icon="user",
        order=30,
        match=(
            "account_email",
            "account_change_password",
            "account_set_password",
            "account_reauthenticate",
            "socialaccount_connections",
            "mfa_index",
            "mfa_activate_totp",
            "mfa_deactivate_totp",
            "mfa_view_recovery_codes",
            "mfa_generate_recovery_codes",
            "mfa_download_recovery_codes",
            "mfa_reauthenticate",
        ),
    ),
    SettingsSection(
        slug="connections",
        label=_("Connections"),
        url_name="connections:list",
        icon="link",
        order=35,
        match=(
            "connections:pick",
            "connections:create",
            "connections:edit",
            "connections:test",
            "connections:delete",
        ),
    ),
    SettingsSection(
        slug="tokens",
        label=_("API tokens"),
        url_name="api:token_list",
        icon="shield",
        order=40,
        match=("api:token_create", "api:token_revoke"),
    ),
    SettingsSection(
        slug="data",
        label=_("Your data"),
        url_name="core:export",
        icon="download",
        order=50,
        match=("core:export_download",),
    ),
)

_registered: dict[str, SettingsSection] = {}


def register(section: SettingsSection) -> SettingsSection:
    """Add a section to the sidebar. Registering a slug twice replaces the first."""
    _registered[section.slug] = section
    return section


def unregister(slug: str) -> None:
    _registered.pop(slug, None)


def sections() -> list[SettingsSection]:
    """Every section, built-in and registered, in sidebar order."""
    by_slug = {section.slug: section for section in BUILTIN}
    by_slug.update(_registered)
    return sorted(by_slug.values(), key=lambda section: (section.order, section.slug))


def active_section(request: HttpRequest) -> SettingsSection | None:
    for section in sections():
        if section.is_active(request):
            return section
    return None
