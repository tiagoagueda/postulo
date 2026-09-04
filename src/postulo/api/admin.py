from django.contrib import admin

from .models import CaptureToken


@admin.register(CaptureToken)
class CaptureTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "owner", "created_at", "last_used_at", "revoked_at")
    list_filter = ("owner",)
    readonly_fields = ("prefix", "token_hash", "last_used_at", "revoked_at")
