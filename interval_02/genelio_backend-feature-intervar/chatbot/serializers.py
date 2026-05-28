"""Chat serializers."""
from rest_framework import serializers

from reports.models import Report

from .models import ChatMessage, ChatSession


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ("id", "role", "content", "created_at")
        read_only_fields = fields


class ChatSessionSerializer(serializers.ModelSerializer):
    report_id = serializers.PrimaryKeyRelatedField(
        source="report",
        queryset=Report.objects.all(),
        required=False, allow_null=True, write_only=True,
    )
    report = serializers.SerializerMethodField(read_only=True)
    message_count = serializers.IntegerField(source="messages.count", read_only=True)

    class Meta:
        model = ChatSession
        fields = (
            "id", "title", "report", "report_id",
            "message_count", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "message_count")

    def get_report(self, obj):
        if not obj.report:
            return None
        return {
            "id": str(obj.report.id),
            "report_type": obj.report.report_type,
            "status": obj.report.status,
        }

    def validate_report_id(self, value):
        user = self.context["request"].user
        if value and value.owner_id != user.id:
            raise serializers.ValidationError("Report does not belong to this user.")
        return value


class ChatSessionDetailSerializer(ChatSessionSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = ChatSessionSerializer.Meta.fields + ("messages",)


class SendMessageSerializer(serializers.Serializer):
    content = serializers.CharField()
