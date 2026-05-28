"""Serializers for signup, profile, password change."""
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """The `/auth/users/me/` payload."""

    class Meta:
        model = User
        fields = (
            "id", "email", "username",
            "first_name", "last_name",
            "avatar", "date_joined",
        )
        read_only_fields = ("id", "email", "date_joined")


class SignupSerializer(serializers.ModelSerializer):
    """Matches the signup form at /signup."""

    password = serializers.CharField(write_only=True, validators=[validate_password])
    re_password = serializers.CharField(write_only=True)
    agreed_to_terms = serializers.BooleanField(write_only=True)

    class Meta:
        model = User
        fields = (
            "id", "email", "first_name", "last_name",
            "password", "re_password", "agreed_to_terms",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        if attrs["password"] != attrs["re_password"]:
            raise serializers.ValidationError({"re_password": "Passwords do not match."})
        if not attrs.get("agreed_to_terms"):
            raise serializers.ValidationError(
                {"agreed_to_terms": "You must accept the Privacy Policy and Terms."}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("re_password")
        agreed = validated_data.pop("agreed_to_terms")
        password = validated_data.pop("password")
        # Use the manager so email is normalised and password properly hashed.
        return User.objects.create_user(
            password=password,
            agreed_to_terms=agreed,
            **validated_data,  # email, first_name, last_name
        )


class SetPasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value


class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("avatar",)
