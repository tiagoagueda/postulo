from django.contrib import admin

from .models import CV, CoverLetter, CVItem, DocumentCopy, RenderedDocument, UploadedDocument


class CVItemInline(admin.TabularInline):
    model = CVItem
    extra = 0
    fields = ("content_type", "object_id", "order", "is_included")


@admin.register(CV)
class CVAdmin(admin.ModelAdmin):
    list_display = ("name", "theme", "owner")
    list_filter = ("owner", "theme")
    search_fields = ("name", "headline")
    inlines = (CVItemInline,)


@admin.register(CoverLetter)
class CoverLetterAdmin(admin.ModelAdmin):
    list_display = ("name", "is_template", "owner")
    list_filter = ("owner", "is_template")
    search_fields = ("name", "subject")


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "version", "owner", "created_at")
    list_filter = ("owner", "kind")
    search_fields = ("title",)


@admin.register(RenderedDocument)
class RenderedDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "application", "rendered_at", "owner")
    list_filter = ("owner", "kind")
    search_fields = ("title",)
    readonly_fields = ("checksum", "source_text")


@admin.register(DocumentCopy)
class DocumentCopyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "store", "status", "attempts", "owner", "sent_at")
    list_filter = ("status", "store", "owner")
    readonly_fields = ("attempts", "last_attempt_at", "sent_at", "last_error")
