from django.contrib import admin

from .models import Connection, SyncLink


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ("label", "kind", "plugin", "owner", "enabled", "last_ok_at", "synced_at")
    list_filter = ("kind", "plugin", "enabled")
    search_fields = ("label", "plugin")
    readonly_fields = ("secrets_encrypted", "last_ok_at", "last_error", "synced_at", "last_summary")


@admin.register(SyncLink)
class SyncLinkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "connection", "etag", "last_synced_at", "remote_gone")
    list_filter = ("connection", "remote_gone")
