"""URLs for the reports app (mounted under /chatbot/report/)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ReportViewSet, report_types

router = DefaultRouter(trailing_slash=True)
router.register(r"data", ReportViewSet, basename="report")

urlpatterns = [
    path("", include(router.urls)),
    path("types/", report_types, name="report-types"),
]
