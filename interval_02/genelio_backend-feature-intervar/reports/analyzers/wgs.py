"""WGS (Whole Genome Sequencing) analyzer — BioAro template."""

from __future__ import annotations

from pathlib import Path

from ._genomic import analyze_genomic


def analyze(pdf_path: str | Path) -> dict:
    return analyze_genomic(pdf_path, site="wgs")
