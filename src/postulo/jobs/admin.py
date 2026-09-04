from django.contrib import admin

from .models import Company, Contact, JobPosting


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("name", "role", "email", "phone")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "location", "industry")
    list_filter = ("owner",)
    search_fields = ("name", "location", "industry")
    inlines = (ContactInline,)


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
