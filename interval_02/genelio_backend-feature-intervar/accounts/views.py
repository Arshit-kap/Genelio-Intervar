"""Auth + profile endpoints.

URL shape intentionally mirrors djoser so a React frontend that was built
against genelio.com's API can point here with only a base-URL change:

  POST /auth/users/              -> signup
  GET  /auth/users/me/           -> current user
  PATCH/PUT /auth/users/me/      -> update profile
  POST /auth/users/set_password/ -> change password
  POST /auth/users/set_avatar/   -> upload avatar
  POST /auth/jwt/create/         -> login
  POST /auth/jwt/refresh/        -> refresh token
  POST /auth/jwt/verify/         -> verify token
"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import User
from .serializers import (
    AvatarSerializer,
    SetPasswordSerializer,
    SignupSerializer,
    UserSerializer,
)


class UserViewSet(GenericViewSet):
    """Handles /auth/users/ create + /auth/users/me/ + actions."""

    queryset = User.objects.all()
    parser_classes = (JSONParser, FormParser, MultiPartParser)

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "create":
            return SignupSerializer
        if self.action == "set_password":
            return SetPasswordSerializer
        if self.action == "set_avatar":
            return AvatarSerializer
        return UserSerializer

    # POST /auth/users/
    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    # GET/PATCH /auth/users/me/
    @action(detail=False, methods=["get", "put", "patch"], url_path="me")
    def me(self, request):
        user = request.user
        if request.method == "GET":
            return Response(UserSerializer(user).data)
        serializer = UserSerializer(
            user, data=request.data, partial=(request.method == "PATCH")
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # POST /auth/users/set_password/
    @action(detail=False, methods=["post"], url_path="set_password")
    def set_password(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # POST /auth/users/set_avatar/
    @action(detail=False, methods=["post"], url_path="set_avatar",
            parser_classes=[MultiPartParser, FormParser])
    def set_avatar(self, request):
        serializer = self.get_serializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)
