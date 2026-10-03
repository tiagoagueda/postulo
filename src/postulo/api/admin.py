from django.contrib import admin

from .models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    """Token metadata, to look at and to delete. Never to make or reword one (#368).

    A token belongs to a person, and this is the one owned model the admin keeps, because
    revoking a leaked token is an operator's job. The form for adding one would mint a
    credential in somebody else's name, so there is none.
    """

    list_display = ("name", "prefix", "owner", "created_at", "last_used_at", "revoked_at")
    list_filter = ("owner",)
    readonly_fields = ("prefix", "token_hash", "last_used_at", "revoked_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
