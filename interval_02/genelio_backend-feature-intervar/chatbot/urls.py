"""Chatbot URL map (mounted under /chatbot/).

The live site uses the path `/chatbot/chat/session/list/` — we keep that
pattern exactly so a React client built against the production API can be
pointed at this backend without route changes.
"""
from django.urls import path

from .views import ChatSessionViewSet

session_list = ChatSessionViewSet.as_view({"get": "list", "post": "create"})
session_detail = ChatSessionViewSet.as_view({
    "get": "retrieve", "patch": "partial_update",
    "put": "update", "delete": "destroy",
})
session_send = ChatSessionViewSet.as_view({"post": "send"})

urlpatterns = [
    # List + create
    path("chat/session/list/", session_list, name="chat-session-list"),
    # Detail
    path("chat/session/<uuid:pk>/", session_detail, name="chat-session-detail"),
    # Send a message
    path("chat/session/<uuid:pk>/send/", session_send, name="chat-session-send"),
]
