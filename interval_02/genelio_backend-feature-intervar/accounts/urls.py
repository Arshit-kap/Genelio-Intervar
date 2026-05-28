"""URL map for /auth/*.

Mirrors djoser + simplejwt:
  /auth/users/           -> signup  (POST)
  /auth/users/me/        -> profile (GET / PATCH / PUT)
  /auth/users/set_password/
  /auth/users/set_avatar/
  /auth/jwt/create/      -> login
  /auth/jwt/refresh/
  /auth/jwt/verify/
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .views import UserViewSet

router = DefaultRouter(trailing_slash=True)
router.register(r"users", UserViewSet, basename="users")

urlpatterns = [
    path("", include(router.urls)),
    path("jwt/create/", TokenObtainPairView.as_view(), name="jwt-create"),
    path("jwt/refresh/", TokenRefreshView.as_view(), name="jwt-refresh"),
    path("jwt/verify/", TokenVerifyView.as_view(), name="jwt-verify"),
]
