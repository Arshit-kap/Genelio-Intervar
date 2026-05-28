"""Per-report-type analyzer registry.

Each report type is served by a module exposing ``analyze(pdf_path) -> dict``.
Four body-site microbiomes (gut / oral / skin / vaginal) now have real
analyzers; WGS / WES remain stubs until those parsers are wired up.

Call sites go through :func:`analyze` which dispatches on ``report_type``
and returns a JSON-serialisable dict suitable for the Report model's
``parsed_data`` JSONField.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from reports.models import ReportType

from . import clinical_csv as _clinical_csv
from . import gut as _gut
from . import intervar as _intervar
from . import oral as _oral
from . import skin as _skin
from . import vaginal as _vaginal
from . import wes as _wes
from . import wgs as _wgs

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub analyzers for types without real parsing yet
# ---------------------------------------------------------------------------

def _stub_analyze(_: str) -> dict:
    """Fallback for report types without a real parser.

    The structure still includes ``status`` + ``report`` + ``analysis_context``
    so the chatbot pipeline can treat every report type uniformly — the stub
    just means the LLM will be told "no structured data available".
    """
    return {
        "status": "stub",
        "site": None,
        "report": {},
        "analysis_context": "",
        "message": "Analyzer not yet implemented for this report type.",
    }


# ---------------------------------------------------------------------------
# Gut adapter — wraps the legacy extractor so its output matches the shape
# returned by the other microbiome analyzers.
# ---------------------------------------------------------------------------

def _gut_analyze(pdf_path: str) -> dict:
    report = _gut.extract_all_tables(pdf_path)
    return {
        "status": "ok",
        "site": "gut",
        "report": report,
        "analysis_context": _gut.build_full_analysis_context(report),
        "structured_summary": _gut.build_structured_summary(report),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_Analyzer = Callable[[str], dict]

_REGISTRY: dict[str, _Analyzer] = {
    ReportType.GUT.value: _gut_analyze,
    ReportType.ORAL.value: _oral.analyze,
    ReportType.SKIN.value: _skin.analyze,
    ReportType.VAGINAL.value: _vaginal.analyze,
    ReportType.WGS.value: _wgs.analyze,
    ReportType.WES.value: _wes.analyze,
    ReportType.CLINICAL_CSV.value: _clinical_csv.analyze,
    ReportType.INTERVAR.value: _intervar.analyze,
}

#: Report types that have a real, non-stub analyzer. Used by the
#: ``/chatbot/report/types/`` catalogue endpoint to flag `implemented: true`.
IMPLEMENTED_TYPES: frozenset[str] = frozenset({
    ReportType.GUT.value,
    ReportType.ORAL.value,
    ReportType.SKIN.value,
    ReportType.VAGINAL.value,
    ReportType.WES.value,
    ReportType.WGS.value,
    ReportType.CLINICAL_CSV.value,
    ReportType.INTERVAR.value,
})


def analyze(report_type: str, pdf_path: str | Path) -> dict:
    """Dispatch to the analyzer registered for ``report_type``."""
    fn = _REGISTRY.get(report_type, _stub_analyze)
    return fn(str(pdf_path))


__all__ = ["analyze", "IMPLEMENTED_TYPES"]
