"""Root URL configuration.

The URL shape mirrors genelio.com's observed API:
  /auth/...              -> accounts (djoser-compatible surface, JWT)
  /chatbot/chat/...      -> chat sessions + messages
  /chatbot/report/...    -> report uploads (6 types)
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth + user profile (mirrors /auth/users/me/, /auth/jwt/create/, etc.)
    path("auth/", include("accounts.urls")),

    # Business endpoints sit under /chatbot/, matching the live API
    path("chatbot/", include("chatbot.urls")),
    path("chatbot/report/", include("reports.urls")),

    # Franklin (Genoox) gene + variant lookups
    path("franklin/", include("franklin.urls")),

    # OpenAPI schema + interactive docs
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("schema/swagger/", SpectacularSwaggerView.as_view(url_name="schema"),
         name="swagger-ui"),
    path("schema/redoc/", SpectacularRedocView.as_view(url_name="schema"),
         name="redoc"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
