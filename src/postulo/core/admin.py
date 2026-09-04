from django.contrib import admin

from .models import Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "colour", "owner")
    list_filter = ("owner",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
