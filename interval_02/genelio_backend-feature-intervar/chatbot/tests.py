"""Happy-path tests for chat sessions & messages."""
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import User


class ChatFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="chat@example.com", password="Sup3rSecret!",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_list_send_without_report_returns_stub_reply(self):
        # Create
        resp = self.client.post("/chatbot/chat/session/list/",
                                {"title": "Hello"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        session_id = resp.data["id"]

        # List
        resp = self.client.get("/chatbot/chat/session/list/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)

        # Send — no report attached → graceful stub reply
        resp = self.client.post(
            f"/chatbot/chat/session/{session_id}/send/",
            {"content": "hi"}, format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["user"]["content"], "hi")
        self.assertIn("No report is attached",
                      resp.data["assistant"]["content"])

        # Detail includes both messages
        resp = self.client.get(f"/chatbot/chat/session/{session_id}/")
        self.assertEqual(resp.data["message_count"], 2)
