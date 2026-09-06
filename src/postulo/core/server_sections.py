"""The sections of the Server settings area, for administrators."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from .settings_sections import SettingsSection

SECTIONS: tuple[SettingsSection, ...] = (
    SettingsSection(
        slug="overview", label=_("Overview"), url_name="server:overview", icon="monitor", order=10
    ),
    SettingsSection(
        slug="people",
        label=_("People"),
        url_name="server:people",
        icon="user",
        order=20,
        match=(
            "accounts:invite_list",
            "accounts:invite_create",
            "accounts:invite_revoke",
            "server:person_admin",
            "server:person_active",
        ),
    ),
    SettingsSection(
        slug="signin", label=_("Sign-in"), url_name="server:signin", icon="shield", order=30
    ),
    SettingsSection(
        slug="email",
        label=_("Email"),
        url_name="server:email",
        icon="mail",
        order=40,
        match=("server:email_test",),
    ),
    SettingsSection(
        slug="plugins", label=_("Plugins"), url_name="server:plugins", icon="link", order=50
    ),
    SettingsSection(
        slug="capture", label=_("Capture"), url_name="server:capture", icon="search", order=60
    ),
    SettingsSection(
        slug="logs", label=_("Logs"), url_name="server:logs", icon="file-text", order=65
    ),
    SettingsSection(
        slug="defaults", label=_("Defaults"), url_name="server:defaults", icon="settings", order=70
    ),
)
