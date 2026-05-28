from django.contrib import admin

from .models import FranklinCache


@admin.register(FranklinCache)
class FranklinCacheAdmin(admin.ModelAdmin):
    list_display = ("kind", "cache_key", "reference", "fetched_at", "expires_at")
    list_filter = ("kind", "reference")
    search_fields = ("cache_key",)
    readonly_fields = ("id", "payload", "fetched_at", "expires_at")
