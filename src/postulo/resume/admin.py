from django.contrib import admin

from .models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Project,
    Skill,
    SkillGroup,
)


class OwnedAdmin(admin.ModelAdmin):
    list_filter = ("owner",)


@admin.register(Experience)
class ExperienceAdmin(OwnedAdmin):
    list_display = ("role", "organisation", "start_date", "end_date", "owner")
    search_fields = ("role", "organisation")


@admin.register(Education)
class EducationAdmin(OwnedAdmin):
    list_display = ("qualification", "institution", "end_date", "owner")
    search_fields = ("qualification", "institution")


class SkillInline(admin.TabularInline):
    model = Skill
    extra = 0
    fields = ("name", "order")


@admin.register(SkillGroup)
class SkillGroupAdmin(OwnedAdmin):
    list_display = ("name", "order", "owner")
    inlines = (SkillInline,)


@admin.register(Skill)
class SkillAdmin(OwnedAdmin):
    list_display = ("name", "group", "owner")
    search_fields = ("name",)


@admin.register(Project)
class ProjectAdmin(OwnedAdmin):
    list_display = ("name", "role", "owner")
    search_fields = ("name",)


@admin.register(Certification)
class CertificationAdmin(OwnedAdmin):
    list_display = ("name", "issuer", "issued_on", "owner")
    search_fields = ("name", "issuer")


@admin.register(LanguageSkill)
class LanguageSkillAdmin(OwnedAdmin):
    list_display = ("name", "proficiency", "owner")
