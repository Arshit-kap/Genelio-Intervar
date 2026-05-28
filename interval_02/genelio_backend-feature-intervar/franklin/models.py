"""Franklin lookup cache.

A persistent fallback for the Redis hot cache. Every successful scrape
gets a row here keyed by `(kind, normalized_query, reference)`; the row
holds the full JSON payload plus a TTL. The Redis layer fronts this — if
Redis is cold, the service falls back to reading from this table.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class FranklinLookupKind(models.TextChoices):
    GENE = "gene", "Gene"
    VARIANT = "variant", "Variant"


class FranklinCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=FranklinLookupKind.choices)
    # Normalized cache key (gene: upper-cased symbol; variant: original query
    # string lower-cased). Combined with reference, this is what we look up by.
    cache_key = models.CharField(max_length=255)
    reference = models.CharField(max_length=8)  # HG19 / HG38

    payload = models.JSONField()

    fetched_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ("-fetched_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "cache_key", "reference"],
                name="franklin_cache_unique_key",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "cache_key", "reference"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.cache_key}:{self.reference}"

    @property
    def is_fresh(self) -> bool:
        return self.expires_at > timezone.now()

    @classmethod
    def default_ttl(cls) -> timedelta:
        # Gene/variant metadata is essentially static day-to-day.
        return timedelta(days=7)
