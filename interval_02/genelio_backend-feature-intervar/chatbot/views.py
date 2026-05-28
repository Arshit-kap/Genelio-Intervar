"""Chat endpoints.

Mirrors the live API shape:
  GET  /chatbot/chat/session/list/         -> paginated sessions for the user
  POST /chatbot/chat/session/list/         -> create a new session
  GET  /chatbot/chat/session/<id>/         -> session detail + messages
  PATCH/DELETE /chatbot/chat/session/<id>/
  POST /chatbot/chat/session/<id>/send/    -> post a user message, get reply
"""
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ChatMessage, ChatSession
from .pipeline import generate_reply
from .serializers import (
    ChatMessageSerializer,
    ChatSessionDetailSerializer,
    ChatSessionSerializer,
    SendMessageSerializer,
)


class ChatSessionViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        return ChatSession.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action in {"retrieve", "send"}:
            return ChatSessionDetailSerializer
        return ChatSessionSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        """POST a user message and get the assistant reply back."""
        session = self.get_object()
        payload = SendMessageSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        user_msg = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.USER,
            content=payload.validated_data["content"],
        )

        reply_text = generate_reply(session, user_msg.content)
        assistant_msg = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.ASSISTANT,
            content=reply_text,
        )
        # Touch updated_at so list ordering reflects the latest activity.
        session.save(update_fields=["updated_at"])

        return Response(
            {
                "user": ChatMessageSerializer(user_msg).data,
                "assistant": ChatMessageSerializer(assistant_msg).data,
            },
            status=status.HTTP_201_CREATED,
        )
