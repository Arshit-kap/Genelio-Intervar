"""Skin-microbiome analyzer.

Thin wrapper around :mod:`_microbiome`. Uses the SDIV healthy range
published in the skin report (2.34 – 3.5) and the site's own keystone
terminology ("skin protecting species").
"""
from __future__ import annotations

from ._microbiome import SiteConfig, build_analysis_context, parse_microbiome_report

CONFIG = SiteConfig(
    site="skin",
    display="Skin Microbiome",
    # Source: report text — "SDIV for a healthy skin microbiome reference
    # cohort is in the range of 2.34 to 3.5".
    healthy_diversity_range=(2.34, 3.5),
    keystone_keywords=("keystone", "protecting", "protective", "commensal"),
)


def analyze(pdf_path: str) -> dict:
    report = parse_microbiome_report(pdf_path, CONFIG)
    return {
        "status": "ok",
        "site": CONFIG.site,
        "report": report,
        "analysis_context": build_analysis_context(report),
    }
