from allauth.account.adapter import get_adapter
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import Invite, Profile, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    extra = 0


class CleanUsernameMixin:
    """The rules a person signing up is held to, so the blacklist holds here too (#427)."""

    def clean_username(self) -> str:
        username = self.cleaned_data["username"].strip().casefold()
        if self.instance.pk and username == self.instance.username:
            return username
        return get_adapter().clean_username(username)


class UserAddForm(CleanUsernameMixin, AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name")


class UserEditForm(CleanUsernameMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = UserAddForm
    form = UserEditForm
    ordering = ("email",)
    list_display = ("email", "username", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "username", "first_name", "last_name")
    inlines = (ProfileInline,)
    fieldsets = (
        (None, {"fields": ("username", "email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                    "usable_password",
                ),
            },
        ),
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "created_by", "created_at", "expires_at", "accepted_at")
    list_filter = ("accepted_at",)
    search_fields = ("email", "note")
    readonly_fields = ("token_fingerprint", "created_at", "accepted_at", "accepted_by")
