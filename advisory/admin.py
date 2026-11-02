from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html

from users.models import User

from .knowledge import extract_uploaded_document, index_document_version, remove_version_chunks
from .models import KnowledgeDocument, KnowledgeDocumentVersion


class AdministratorOnlyAdmin(admin.ModelAdmin):
    def _authorized(self, request):
        return (
            request.user.is_active and request.user.is_staff
            and request.user.role == User.Role.ADMINISTRATOR
        )

    def has_module_permission(self, request):
        return self._authorized(request)

    def has_view_permission(self, request, obj=None):
        return self._authorized(request)

    def has_add_permission(self, request):
        return self._authorized(request)

    def has_change_permission(self, request, obj=None):
        return self._authorized(request)

    def has_delete_permission(self, request, obj=None):
        return False


class KnowledgeVersionInline(admin.TabularInline):
    model = KnowledgeDocumentVersion
    extra = 0
    fields = ("version", "lifecycle_status", "verification_status", "expert_review_status", "is_active", "indexing_status")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(AdministratorOnlyAdmin):
    list_display = ("title", "publisher", "original_language", "coverage", "updated_at")
    search_fields = ("title", "author", "publisher", "source_url", "provenance")
    inlines = (KnowledgeVersionInline,)
    readonly_fields = ("created_by", "created_at", "updated_at")
    fieldsets = (
        ("Identity", {"fields": ("title", "author", "publisher", "publication_date", "source_url", "original_language", "source_identity")}),
        ("Coverage", {"fields": ("countries", "regions", "crops", "diseases")}),
        ("Rights and provenance", {"fields": ("license", "usage_permissions", "provenance")}),
        ("Audit", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        governed_fields = {
            "title", "author", "publisher", "publication_date", "source_url",
            "original_language", "countries", "regions", "crops", "diseases",
            "license", "usage_permissions", "provenance",
        }
        if change and governed_fields.intersection(form.changed_data):
            for version in obj.versions.filter(is_active=True):
                version.lifecycle_status = version.Lifecycle.DEACTIVATED
                version.is_active = False
                version.indexing_status = version.Indexing.NOT_ELIGIBLE
                version.review_notes = (version.review_notes + "\nMetadata changed; re-review required.").strip()
                version.save(update_fields=["lifecycle_status", "is_active", "indexing_status", "review_notes", "updated_at"])
                try:
                    remove_version_chunks(version)
                except Exception:
                    pass

    @admin.display(description="Crop / disease coverage")
    def coverage(self, obj):
        return f"{', '.join(obj.crops)} / {', '.join(obj.diseases)}"


@admin.register(KnowledgeDocumentVersion)
class KnowledgeDocumentVersionAdmin(AdministratorOnlyAdmin):
    list_display = (
        "document", "version", "lifecycle_status", "verification_status",
        "expert_review_status", "is_active", "extraction_status", "indexing_status",
    )
    list_filter = (
        "lifecycle_status", "verification_status", "expert_review_status",
        "is_active", "extraction_status", "indexing_status",
    )
    search_fields = ("document__title", "document__publisher", "original_filename", "extracted_text")
    actions = ("approve_versions", "reject_versions", "deactivate_versions", "mark_outdated", "retry_indexing")
    readonly_fields = (
        "original_filename", "file_size", "content_type", "content_hash",
        "extraction_status", "extraction_error", "text_preview", "reviewed_by",
        "reviewed_at", "approved_by", "approved_at", "indexing_status",
        "indexing_error", "indexed_at", "created_at", "updated_at",
    )
    fieldsets = (
        ("Version upload", {"fields": ("document", "version", "uploaded_file")}),
        ("Extraction preview", {"fields": ("original_filename", "file_size", "content_type", "content_hash", "extraction_status", "extraction_error", "text_preview")}),
        ("Verification and review", {"fields": ("verification_status", "expert_review_status", "checked_claims", "checked_references", "review_notes", "rejection_reason")}),
        ("Lifecycle", {"fields": ("lifecycle_status", "is_active", "reviewed_by", "reviewed_at", "approved_by", "approved_at")}),
        ("Indexing", {"fields": ("indexing_status", "indexing_error", "indexed_at")}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        uploaded_changed = "uploaded_file" in form.changed_data
        if change and uploaded_changed:
            try:
                remove_version_chunks(obj)
            except Exception:
                pass
            obj.lifecycle_status = obj.Lifecycle.DRAFT
            obj.verification_status = obj.Verification.UNVERIFIED
            obj.expert_review_status = obj.ExpertReview.PENDING
            obj.is_active = False
            obj.approved_by = None
            obj.approved_at = None
            obj.reviewed_by = None
            obj.reviewed_at = None
            obj.indexing_status = obj.Indexing.NOT_ELIGIBLE
            obj.indexing_error = "File changed; verification and approval are required."
            obj.indexed_at = None
        super().save_model(request, obj, form, change)
        if uploaded_changed:
            extract_uploaded_document(obj)

    @admin.display(description="Extracted text preview")
    def text_preview(self, obj):
        text = obj.extracted_text[:5000] if obj.extracted_text else "No extractable text available."
        return format_html("<pre style='white-space:pre-wrap;max-height:32rem;overflow:auto'>{}</pre>", text)

    @admin.action(description="Approve, activate and index selected verified versions")
    def approve_versions(self, request, queryset):
        approved = 0
        for version in queryset.select_related("document"):
            if (
                version.verification_status != version.Verification.VERIFIED
                or version.expert_review_status != version.ExpertReview.REVIEWED
                or version.extraction_status != version.Extraction.SUCCEEDED
                or not version.checked_claims.strip()
                or not version.checked_references.strip()
            ):
                self.message_user(request, f"{version} was not approved: verification, expert review, checked claims/references and extracted text are required.", messages.ERROR)
                continue
            now = timezone.now()
            older = KnowledgeDocumentVersion.objects.filter(document=version.document, is_active=True).exclude(pk=version.pk)
            for previous in older:
                previous.lifecycle_status = previous.Lifecycle.SUPERSEDED
                previous.is_active = False
                previous.indexing_status = previous.Indexing.NOT_ELIGIBLE
                previous.save(update_fields=["lifecycle_status", "is_active", "indexing_status", "updated_at"])
                try:
                    remove_version_chunks(previous)
                except Exception:
                    pass
            version.lifecycle_status = version.Lifecycle.APPROVED
            version.is_active = True
            version.reviewed_by = request.user
            version.reviewed_at = now
            version.approved_by = request.user
            version.approved_at = now
            version.indexing_status = version.Indexing.PENDING
            try:
                version.full_clean()
            except ValidationError as exc:
                self.message_user(request, f"{version}: {exc}", messages.ERROR)
                continue
            version.save()
            index_document_version(version)
            approved += 1
        self.message_user(request, f"Approved {approved} document version(s).", messages.SUCCESS)

    def _disable(self, request, queryset, status, reason=""):
        for version in queryset:
            version.lifecycle_status = status
            version.is_active = False
            version.indexing_status = version.Indexing.NOT_ELIGIBLE
            if reason and not version.rejection_reason:
                version.rejection_reason = reason
            version.reviewed_by = request.user
            version.reviewed_at = timezone.now()
            version.save()
            try:
                remove_version_chunks(version)
            except Exception:
                pass

    @admin.action(description="Reject selected versions")
    def reject_versions(self, request, queryset):
        self._disable(request, queryset, KnowledgeDocumentVersion.Lifecycle.REJECTED, "Rejected by administrator.")

    @admin.action(description="Deactivate selected versions")
    def deactivate_versions(self, request, queryset):
        self._disable(request, queryset, KnowledgeDocumentVersion.Lifecycle.DEACTIVATED)

    @admin.action(description="Mark selected versions outdated")
    def mark_outdated(self, request, queryset):
        self._disable(request, queryset, KnowledgeDocumentVersion.Lifecycle.OUTDATED)

    @admin.action(description="Retry indexing selected eligible versions")
    def retry_indexing(self, request, queryset):
        succeeded = sum(index_document_version(version) for version in queryset)
        self.message_user(request, f"Indexed {succeeded} of {queryset.count()} selected version(s).")
