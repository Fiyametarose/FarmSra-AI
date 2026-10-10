from django.contrib import admin

from .models import DiagnosisCase


@admin.register(DiagnosisCase)
class DiagnosisCaseAdmin(admin.ModelAdmin):
    list_display = ("id", "farmer", "crop", "status", "created_at")
    list_filter = ("crop", "status")
    search_fields = ("farmer__email", "predicted_class")
    readonly_fields = ("created_at", "updated_at")
