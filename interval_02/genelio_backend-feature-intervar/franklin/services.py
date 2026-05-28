"""Cache-aware Franklin lookups.

Two layers of cache fronting the scraper:

1. Django cache (Redis if configured, LocMem in dev) — hot path, 7d TTL.
2. ``FranklinCache`` table — persistent fallback; survives Redis flush.

Both layers cache *successful* results only. Failures (HTTP errors, empty
parse_search, exceptions) are returned to the caller without caching so a
transient Franklin outage doesn't poison the cache for a week.
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from . import client
from .models import FranklinCache, FranklinLookupKind

logger = logging.getLogger(__name__)

# Hot-cache TTL must agree with FranklinCache.default_ttl().
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def _cache_key(kind: str, query: str, reference: str) -> str:
    return f"franklin:{kind}:{reference.upper()}:{query.strip().upper()}"


def _is_successful_result(result: dict[str, Any]) -> bool:
    """A result is cacheable if there's no top-level error AND at least one
    section came back OK. Stops us from caching a fully-failed scrape."""
    if not result or result.get("error"):
        return False
    sections = result.get("sections") or {}
    return any(sec.get("status") == 200 for sec in sections.values())


def _read_persistent(kind: str, key: str, reference: str) -> dict[str, Any] | None:
    try:
        row = FranklinCache.objects.get(
            kind=kind, cache_key=key, reference=reference.upper()
        )
    except FranklinCache.DoesNotExist:
        return None
    if not row.is_fresh:
        return None
    return row.payload


def _write_persistent(
    kind: str, key: str, reference: str, payload: dict[str, Any]
) -> None:
    expires_at = timezone.now() + FranklinCache.default_ttl()
    FranklinCache.objects.update_or_create(
        kind=kind,
        cache_key=key,
        reference=reference.upper(),
        defaults={"payload": payload, "expires_at": expires_at},
    )


def lookup_gene(symbol: str, reference: str = "HG19") -> dict[str, Any]:
    symbol = symbol.strip().upper()
    reference = reference.upper()
    key = _cache_key(FranklinLookupKind.GENE, symbol, reference)

    cached = cache.get(key)
    if cached is not None:
        cached["_cache"] = "hot"
        return cached

    persisted = _read_persistent(FranklinLookupKind.GENE, symbol, reference)
    if persisted is not None:
        cache.set(key, persisted, _CACHE_TTL_SECONDS)
        persisted["_cache"] = "warm"
        return persisted

    result = client.scrape_gene(symbol, reference)
    if _is_successful_result(result):
        try:
            _write_persistent(FranklinLookupKind.GENE, symbol, reference, result)
        except Exception:  # pragma: no cover — cache write must never break the response
            logger.exception("FranklinCache write failed for gene %s/%s", symbol, reference)
        cache.set(key, result, _CACHE_TTL_SECONDS)
    result["_cache"] = "miss"
    return result


def lookup_variant(query: str, reference: str = "HG19") -> dict[str, Any]:
    query = query.strip()
    reference = reference.upper()
    key = _cache_key(FranklinLookupKind.VARIANT, query, reference)

    cached = cache.get(key)
    if cached is not None:
        cached["_cache"] = "hot"
        return cached

    persisted = _read_persistent(FranklinLookupKind.VARIANT, query, reference)
    if persisted is not None:
        cache.set(key, persisted, _CACHE_TTL_SECONDS)
        persisted["_cache"] = "warm"
        return persisted

    result = client.scrape_variant(query, reference)
    if _is_successful_result(result):
        try:
            _write_persistent(FranklinLookupKind.VARIANT, query, reference, result)
        except Exception:  # pragma: no cover
            logger.exception("FranklinCache write failed for variant %s/%s", query, reference)
        cache.set(key, result, _CACHE_TTL_SECONDS)
    result["_cache"] = "miss"
    return result


def lookup_auto(query: str, reference: str = "HG19") -> dict[str, Any]:
    """Auto-detect gene vs variant from query shape, then dispatch."""
    if client.looks_like_variant(query):
        return lookup_variant(query, reference)
    return lookup_gene(query, reference)
