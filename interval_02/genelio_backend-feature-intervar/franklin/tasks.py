"""Async Franklin enrichment for WES/WGS reports.

For every variant in the analyzer's ``parsed_data["report"]["variants"]``
list, build an HGVS query (``{transcript}:{c.}``) or fall back to the
gene symbol, then call :func:`franklin.services.lookup_auto`. The
aggregated result is written back to ``Report.enrichment_data``.

Enrichment is best-effort: per-variant failures don't fail the whole
task, and a fully-failed task leaves ``enrichment_status='failed'`` with
the exception string in ``enrichment_error`` but does **not** affect the
underlying report's ``status``.
"""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any

from celery import shared_task
from django.utils import timezone

from . import services

logger = logging.getLogger(__name__)

# Report types that have variant payloads worth enriching. Microbiome
# analyzers produce relative abundances, not genomic variants, so they
# stay at enrichment_status="none".
ENRICHABLE_TYPES = {"wes", "wgs"}

# Default reference build for Franklin lookups. The BioAro analyzer
# template doesn't commit to a build, so HG19 is the safer default — it
# matches the public-coordinate format on most clinical reports.
_DEFAULT_REFERENCE = "HG19"

# Cap parallel Franklin calls so we don't hammer the external API. 5 is
# enough to drain a typical WES (3–20 variants) in a single batch.
_MAX_PARALLEL = 5


def _build_query(variant: dict[str, Any]) -> tuple[str, str]:
    """Return (kind, query) for a single variant dict.

    Prefers HGVS (``{transcript}:{c.}``) when both fields are present and
    look HGVS-shaped; falls back to the bare gene symbol otherwise.
    """
    transcript = (variant.get("transcript") or "").strip()
    c_notation = (variant.get("variant") or "").strip()
    gene = (variant.get("gene") or "").strip()

    if transcript and c_notation and ("c." in c_notation or "p." in c_notation):
        return "variant", f"{transcript}:{c_notation}"
    if gene:
        return "gene", gene
    return "unknown", ""


def _enrich_variants(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fan out lookups for a list of variants, preserving input order."""
    results: list[dict[str, Any] | None] = [None] * len(variants)

    def _do(idx: int, v: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        kind, query = _build_query(v)
        if not query:
            return idx, {
                "kind": kind,
                "query": "",
                "error": "no gene symbol or HGVS notation in variant",
            }
        try:
            return idx, services.lookup_auto(query, _DEFAULT_REFERENCE)
        except Exception as exc:  # noqa: BLE001 — surface in payload
            logger.warning("Franklin lookup failed for variant %s: %s", query, exc)
            return idx, {"kind": kind, "query": query, "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as pool:
        for idx, payload in pool.map(
            lambda iv: _do(*iv), list(enumerate(variants))
        ):
            results[idx] = payload

    return [r if r is not None else {"error": "missing"} for r in results]


@shared_task(name="franklin.tasks.enrich_report", bind=True, max_retries=2)
def enrich_report(self, report_id: str) -> dict[str, Any]:
    """Enrich a single Report by ID with Franklin data."""
    # Imported here so the task module stays import-safe at Django boot,
    # before app registry is populated.
    from reports.models import EnrichmentStatus, Report

    try:
        report = Report.objects.get(id=report_id)
    except Report.DoesNotExist:
        logger.info("enrich_report: report %s no longer exists", report_id)
        return {"status": "missing"}

    if report.report_type not in ENRICHABLE_TYPES:
        report.enrichment_status = EnrichmentStatus.NONE
        report.save(update_fields=["enrichment_status"])
        return {"status": "skipped", "reason": "not enrichable"}

    parsed = report.parsed_data or {}
    variants = (parsed.get("report") or {}).get("variants") or []
    if not variants:
        report.enrichment_status = EnrichmentStatus.READY
        report.enrichment_data = {"variants": [], "summary": "no variants in report"}
        report.enrichment_updated_at = timezone.now()
        report.save(update_fields=[
            "enrichment_status", "enrichment_data", "enrichment_updated_at",
        ])
        return {"status": "empty"}

    report.enrichment_status = EnrichmentStatus.RUNNING
    report.save(update_fields=["enrichment_status"])

    try:
        enriched = _enrich_variants(variants)
        success_count = sum(
            1 for r in enriched
            if r and not r.get("error")
            and any(
                (sec or {}).get("status") == 200
                for sec in (r.get("sections") or {}).values()
            )
        )
        report.enrichment_data = {
            "reference": _DEFAULT_REFERENCE,
            "total": len(enriched),
            "successful": success_count,
            "variants": enriched,
        }
        report.enrichment_status = EnrichmentStatus.READY
        report.enrichment_error = ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("enrich_report failed for %s", report_id)
        report.enrichment_status = EnrichmentStatus.FAILED
        report.enrichment_error = str(exc)[:2000]
    report.enrichment_updated_at = timezone.now()
    report.save(update_fields=[
        "enrichment_status", "enrichment_data",
        "enrichment_error", "enrichment_updated_at",
    ])
    return {"status": report.enrichment_status, "variants": len(variants)}
