from django.contrib import admin

from .models import Application, ApplicationEvent, Interview, Reminder


class EventInline(admin.TabularInline):
    model = ApplicationEvent
    extra = 0
    fields = ("occurred_at", "kind", "summary", "from_status", "to_status")
    ordering = ("-occurred_at",)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "priority", "applied_at", "owner")
    list_filter = ("owner", "status", "priority", "channel")
    search_fields = ("posting__title", "posting__company__name")
    autocomplete_fields = ("posting", "contact")
    filter_horizontal = ("tags",)
    inlines = (EventInline,)


@admin.register(ApplicationEvent)
class ApplicationEventAdmin(admin.ModelAdmin):
    list_display = ("application", "kind", "occurred_at", "summary")
    list_filter = ("kind",)
    search_fields = ("summary", "body")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("summary", "due_at", "done_at", "application", "owner")
    list_filter = ("owner", "done_at")
    search_fields = ("summary",)


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "starts_at", "outcome", "owner")
    list_filter = ("owner", "kind", "outcome")
    search_fields = ("application__posting__title", "application__posting__company__name")
    autocomplete_fields = ("application",)
    filter_horizontal = ("contacts",)
    readonly_fields = ("uid",)
