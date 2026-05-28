"""Business logic for running a report through its analyzer.

Kept synchronous here to minimise moving parts — swap in a Celery task
later by calling ``run_analysis.delay(report_id)``.

For every microbiome report (gut / oral / skin / vaginal) we also
eagerly build the Chroma vector index — same embedding / retrieval
pipeline used by the legacy Gradio app — so the first chat message
doesn't pay the embedding cost. Indexing is best-effort: if
``sentence-transformers`` / ``chromadb`` / ``pdfplumber`` aren't
installed, or the embedder can't be downloaded, we log a warning and
move on. The report is still usable; chat will build the index lazily
on first query or surface a clear error.
"""
from __future__ import annotations

import logging

from .analyzers import IMPLEMENTED_TYPES, analyze
from .models import EnrichmentStatus, Report, ReportStatus

log = logging.getLogger(__name__)


def _enqueue_enrichment(report: Report) -> None:
    """Kick off Franklin enrichment for wes/wgs reports.

    Best-effort: if the broker is unreachable the upload still succeeds,
    we just leave ``enrichment_status='pending'`` for the user to retry.
    """
    from franklin.tasks import ENRICHABLE_TYPES, enrich_report

    if report.report_type not in ENRICHABLE_TYPES:
        return
    report.enrichment_status = EnrichmentStatus.PENDING
    report.save(update_fields=["enrichment_status"])
    try:
        enrich_report.delay(str(report.id))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "enrich_report enqueue failed for %s (%s: %s); the report is "
            "still uploaded — retry via POST /chatbot/report/data/<id>/enrich/",
            report.id, type(exc).__name__, exc,
        )


def _index_for_rag(report: Report) -> None:
    """Build the Chroma vector store for a microbiome report (best-effort)."""
    if report.report_type not in IMPLEMENTED_TYPES:
        return
    try:
        # Imported lazily so the upload path works even when the
        # embedding / vector-store deps aren't installed.
        from chatbot.pipeline import index_report

        result = index_report(report)
        log.info(
            "RAG index built for %s report %s: %s",
            report.report_type, report.id, result,
        )
    except Exception as exc:  # noqa: BLE001 — indexing is non-fatal
        log.warning(
            "RAG index build skipped for %s %s (%s: %s); chat will build "
            "it lazily on first query.",
            report.report_type, report.id, type(exc).__name__, exc,
        )


def run_analysis(report: Report) -> Report:
    """Run the structured analyzer and eagerly build the RAG index.

    Never raises — failures are written to ``report.status`` /
    ``report.error_message`` so callers always see a persisted outcome.
    """
    report.status = ReportStatus.PROCESSING
    report.save(update_fields=["status"])
    try:
        result = analyze(report.report_type, report.file.path)
        report.parsed_data = result
        report.status = ReportStatus.READY
        report.error_message = ""
    except Exception as exc:  # noqa: BLE001 — surface failure in status
        log.exception("Report analysis failed for %s", report.id)
        report.status = ReportStatus.FAILED
        report.error_message = str(exc)[:2000]
    report.save()

    # Eagerly build the RAG index once the structured analyzer has
    # succeeded. Done outside the try/except above so indexing failures
    # don't flip a successful analysis into "failed".
    if report.status == ReportStatus.READY:
        _index_for_rag(report)
        _enqueue_enrichment(report)

    return report
