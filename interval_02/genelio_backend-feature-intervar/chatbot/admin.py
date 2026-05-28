from django.contrib import admin

from .models import ChatMessage, ChatSession


class MessageInline(admin.TabularInline):
    model = ChatMessage
    readonly_fields = ("id", "role", "content", "created_at")
    extra = 0


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "owner", "report", "updated_at")
    list_filter = ("report__report_type",)
    search_fields = ("title", "owner__email")
    inlines = (MessageInline,)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("content",)
    readonly_fields = ("id", "created_at")
