from django.contrib import admin

from .models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "owner", "created_at", "last_used_at", "revoked_at")
    list_filter = ("owner",)
    readonly_fields = ("prefix", "token_hash", "last_used_at", "revoked_at")
