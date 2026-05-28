"""Vaginal-microbiome analyzer.

Thin wrapper around :mod:`_microbiome`. Uses the SDIV healthy range
published in the vaginal report (0.2 – 1.01), which differs sharply from
the other three sites — a healthy vaginal microbiome is dominated by
Lactobacillus and therefore has LOW diversity.
"""
from __future__ import annotations

from ._microbiome import SiteConfig, build_analysis_context, parse_microbiome_report

CONFIG = SiteConfig(
    site="vaginal",
    display="Vaginal Microbiome",
    # Source: report text — "SDIV for a healthy vaginal microbiome reference
    # cohort is in the range of 0.2 to 1.01".
    healthy_diversity_range=(0.2, 1.01),
)


def analyze(pdf_path: str) -> dict:
    report = parse_microbiome_report(pdf_path, CONFIG)
    return {
        "status": "ok",
        "site": CONFIG.site,
        "report": report,
        "analysis_context": build_analysis_context(report),
    }
