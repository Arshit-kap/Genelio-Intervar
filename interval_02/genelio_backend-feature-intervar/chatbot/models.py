"""Chat session + message models.

Matches the live sidebar (session list -> "New Chat Session", "I need help",
etc.). A session can optionally be linked to an uploaded Report; when it is,
the assistant uses the report's parsed data as context.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from reports.models import Report


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="chat_sessions",
        on_delete=models.CASCADE,
    )
    # Optional so a session can exist before a report is attached.
    report = models.ForeignKey(
        Report, related_name="sessions",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    title = models.CharField(max_length=200, default="New Chat Session")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["owner", "-updated_at"])]

    def __str__(self) -> str:
        return f"{self.title} ({self.id})"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession, related_name="messages", on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=["session", "created_at"])]


class SessionPhenotypeProfile(models.Model):
    """Persistent HPO state for a clinical_csv chat session.

    Captures the symptoms a user has revealed across one or more turns
    of a single chat, resolved to canonical HPO terms, plus the union
    of associated genes that the agentic orchestrator should intersect
    with the patient's variant report.

    One-to-one with ChatSession so we can reload it on every turn.
    """
    session = models.OneToOneField(
        ChatSession, related_name="phenotype_profile",
        on_delete=models.CASCADE,
    )
    # List of resolved-symptom dicts:
    #   {input_text, hpo_id, name, confidence, matched_via}
    hpo_terms = models.JSONField(default=list, blank=True)
    # Cumulative union of all genes associated with the HPO terms above.
    candidate_genes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"hpo-profile({self.session_id})"


class MessageReview(models.Model):
    """Human verdict on an assistant reply — captured for fine-tuning data."""

    class Verdict(models.TextChoices):
        CORRECT = "correct", "Correct"
        PARTIAL = "partial", "Partially correct"
        INCORRECT = "incorrect", "Incorrect"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # OneToOne so a re-review updates rather than duplicates.
    message = models.OneToOneField(
        ChatMessage, related_name="review", on_delete=models.CASCADE,
    )
    # Denormalised so list/export queries don't need joins.
    session = models.ForeignKey(
        ChatSession, related_name="reviews", on_delete=models.CASCADE,
    )
    report = models.ForeignKey(
        "reports.Report", related_name="reviews",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    reviewer_name = models.CharField(max_length=200, blank=True, default="")
    verdict = models.CharField(max_length=16, choices=Verdict.choices)
    notes = models.TextField(blank=True, default="")
    corrected_response = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["session", "-created_at"]),
            models.Index(fields=["verdict"]),
        ]
