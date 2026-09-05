from django.contrib import admin

from .models import Capture, Company, Contact, Industry, JobPosting


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("name", "role", "email", "phone")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "location", "industry_names")
    list_filter = ("owner",)
    search_fields = ("name", "location", "industries__name")
    filter_horizontal = ("industries",)
    inlines = (ContactInline,)


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner")
    list_filter = ("owner",)
    search_fields = ("name",)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "role", "email", "owner")
    list_filter = ("owner",)
    search_fields = ("name", "role", "email")


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "location", "remote_type", "closed_at", "owner")
    list_filter = ("owner", "remote_type", "employment_type")
    search_fields = ("title", "location", "description")
    autocomplete_fields = ("company",)


@admin.register(Capture)
class CaptureAdmin(admin.ModelAdmin):
    list_display = ("__str__", "source_name", "status", "origin", "owner", "created_at")
    list_filter = ("owner", "status", "source_name", "origin")
    search_fields = ("url",)
    readonly_fields = ("data", "source_name", "source_version", "origin")
