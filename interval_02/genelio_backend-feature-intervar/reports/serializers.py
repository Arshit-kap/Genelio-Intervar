"""Report serializers."""
from rest_framework import serializers

from .models import Report, ReportType


class ReportSerializer(serializers.ModelSerializer):
    """Shape matches what /my-reports renders: id, type, created_at, file URL."""

    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = (
            "id", "report_type", "original_filename",
            "status", "error_message",
            "file", "file_url",
            "enrichment_status", "enrichment_updated_at", "enrichment_error",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "status", "error_message",
            "file_url",
            "enrichment_status", "enrichment_updated_at", "enrichment_error",
            "created_at", "updated_at",
        )
        extra_kwargs = {"file": {"write_only": True}}

    def get_file_url(self, obj: Report) -> str | None:
        if not obj.file:
            return None
        request = self.context.get("request")
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class ReportUploadSerializer(serializers.ModelSerializer):
    report_type = serializers.ChoiceField(choices=ReportType.choices)
    file = serializers.FileField()

    class Meta:
        model = Report
        fields = ("report_type", "file")

    def create(self, validated_data):
        file = validated_data["file"]
        return Report.objects.create(
            owner=self.context["request"].user,
            report_type=validated_data["report_type"],
            file=file,
            original_filename=file.name,
        )


class ReportDetailSerializer(ReportSerializer):
    """Includes the parsed analyzer output + Franklin enrichment payload."""

    class Meta(ReportSerializer.Meta):
        fields = ReportSerializer.Meta.fields + ("parsed_data", "enrichment_data")
