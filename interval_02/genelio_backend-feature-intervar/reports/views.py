"""Report endpoints.

Mirrors the live API shape:
  GET  /chatbot/report/data/         -> paginated list of the user's reports
  POST /chatbot/report/data/         -> upload a new report (multipart)
  GET  /chatbot/report/data/<id>/    -> single report with parsed data
  DELETE /chatbot/report/data/<id>/
  GET  /chatbot/report/types/        -> catalogue of supported report types
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .analyzers import IMPLEMENTED_TYPES
from .models import EnrichmentStatus, Report, ReportType
from .serializers import (
    ReportDetailSerializer,
    ReportSerializer,
    ReportUploadSerializer,
)
from .services import run_analysis


class ReportViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser)

    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("report_type", "status")

    def get_queryset(self):
        return Report.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return ReportUploadSerializer
        if self.action == "retrieve":
            return ReportDetailSerializer
        return ReportSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save()
        # Run the analyzer inline. For large files, move to Celery later.
        run_analysis(report)
        return Response(
            ReportDetailSerializer(report, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def enrich(self, request, pk=None):
        """POST /chatbot/report/data/<id>/enrich/ — re-run Franklin enrichment."""
        from franklin.tasks import ENRICHABLE_TYPES, enrich_report

        report = self.get_object()
        if report.report_type not in ENRICHABLE_TYPES:
            return Response(
                {"detail": f"Enrichment not applicable for report_type={report.report_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        report.enrichment_status = EnrichmentStatus.PENDING
        report.enrichment_error = ""
        report.save(update_fields=["enrichment_status", "enrichment_error"])
        try:
            enrich_report.delay(str(report.id))
            queued = True
        except Exception as exc:  # noqa: BLE001 — surface to client
            return Response(
                {"detail": f"Failed to enqueue: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {"queued": queued, "enrichment_status": report.enrichment_status},
            status=status.HTTP_202_ACCEPTED,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def report_types(_request):
    """GET /chatbot/report/types/ -> catalogue shown in the upload picker (public)."""
    return Response([
        {
            "value": value,
            "label": label,
            "implemented": value in IMPLEMENTED_TYPES,
        }
        for value, label in ReportType.choices
    ])
