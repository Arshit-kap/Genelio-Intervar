"""Oral-microbiome analyzer.

Thin wrapper around :mod:`_microbiome` — the generic parser handles 100%
of the structural work; this module just pins site metadata and the
published SDIV healthy range for the oral cavity (1.2 – 3.0).
"""
from __future__ import annotations

from ._microbiome import SiteConfig, build_analysis_context, parse_microbiome_report

CONFIG = SiteConfig(
    site="oral",
    display="Oral Microbiome",
    # Source: report text — "typically falling within a healthy range of 1.2 to 3".
    healthy_diversity_range=(1.2, 3.0),
)


def analyze(pdf_path: str) -> dict:
    """Run the generic parser, add the analysis-context text block."""
    report = parse_microbiome_report(pdf_path, CONFIG)
    return {
        "status": "ok",
        "site": CONFIG.site,
        "report": report,
        "analysis_context": build_analysis_context(report),
    }
