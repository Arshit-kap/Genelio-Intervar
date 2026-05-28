from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "report_type", "status", "created_at")
    list_filter = ("report_type", "status")
    search_fields = ("owner__email", "original_filename", "id")
    readonly_fields = ("id", "parsed_data", "created_at", "updated_at")
