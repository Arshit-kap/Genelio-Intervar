"""Shared parser for BioAro WES / WGS reports.

Both report types share the same template — a Client Information block on
page 2, a Summary + variant table on page 3+, narrative gene-disease info
below it, and Methodology / Limitations on the trailing pages. The only
structural difference is that WES tables include an extra "Inherited from"
column.

Output shape mirrors the microbiome analyzers (status / site / report /
analysis_context / structured_summary) so the chatbot pipeline can treat
every report type uniformly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def analyze_genomic(pdf_path: str | Path, *, site: str) -> dict:
    """Parse a BioAro WES or WGS PDF.

    ``site`` should be ``"wes"`` or ``"wgs"`` — used as the ``site`` field in
    the output and to phrase the structured summary.
    """
    import pdfplumber

    path = str(pdf_path)
    pages_text: list[str] = []
    variant_tables: list[list[list[str]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages_text.append((page.extract_text() or "").strip())
            for table in page.extract_tables() or []:
                if _looks_like_variant_table(table):
                    variant_tables.append(table)

    full_text = "\n\n".join(pages_text)
    patient = _parse_patient_info(full_text)
    test_type = _parse_test_type(full_text) or site.upper()
    summary_text = _parse_summary(full_text)
    counts = _parse_counts(full_text)
    variants = _parse_variants(variant_tables)
    gene_info = _parse_gene_info(full_text)

    report = {
        "patient": patient,
        "test": {"type": test_type},
        "summary": summary_text,
        "counts": counts,
        "variants": variants,
        "gene_info": gene_info,
    }
    return {
        "status": "ok",
        "site": site,
        "report": report,
        "analysis_context": _build_analysis_context(report, site=site),
        "structured_summary": _build_structured_summary(report, site=site),
    }


# ---------------------------------------------------------------------------
# Patient information block (page 2)
# ---------------------------------------------------------------------------


_PATIENT_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "gender": re.compile(r"Gender\s+([A-Za-z]+)"),
    "dob": re.compile(r"Date\s+of\s+Birth\s+([0-9]{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4})"),
    # IDs vary across reports: P####, BA#####, etc. — match alphanumeric.
    "id": re.compile(r"\bID\s+([A-Z]{1,4}\d{3,})\b"),
    "collection_date": re.compile(
        r"Collection\s+Date\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
    ),
    "report_date": re.compile(
        r"Report\s+Date\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
    ),
    "received_date": re.compile(
        r"Received\s+[Dd]ate[\s\S]{0,80}?(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
    ),
}

# Primary: page-3 running header — "<Name> ID: <ID> Collection Date: ... Test: WES/WGS".
_NAME_HEADER_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z\.\s\-']+?)\s+ID:\s+[A-Z]{1,4}\d+\s+Collection\s+Date:",
    re.MULTILINE,
)
# Fallback: Client Information block — "Name <value> Sample Type" or "Name <value>\n".
_NAME_CLIENT_INFO_RE = re.compile(
    r"^Name\s+(?P<name>[A-Z][A-Za-z\.\s\-']+?)\s+Sample\s+Type",
    re.MULTILINE,
)


def _parse_patient_info(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, pat in _PATIENT_FIELD_PATTERNS.items():
        m = pat.search(text)
        if m:
            out[key] = m.group(1).strip()
    for pat in (_NAME_HEADER_RE, _NAME_CLIENT_INFO_RE):
        m = pat.search(text)
        if m:
            out["name"] = re.sub(r"\s+", " ", m.group("name")).strip()
            break
    # Sample type — appears next to "Blood and <date>" or similar.
    sample_match = re.search(r"(Blood|Saliva|Buccal[^\n]*?)\s+\d{1,2}\s+[A-Za-z]{3,9}", text)
    if sample_match:
        out["sample_type"] = sample_match.group(1).strip()
    return out


def _parse_test_type(text: str) -> str | None:
    m = re.search(r"Test:\s*(WES|WGS)", text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Summary paragraph
# ---------------------------------------------------------------------------


def _parse_summary(text: str) -> str:
    """Return the natural-language summary that sits between Summary and Findings."""
    m = re.search(
        r"Summary\s*\n(?P<body>.+?)(?:\n\s*Findings\b|\n\s*Disease\s+results\b)",
        text,
        re.DOTALL,
    )
    if not m:
        return ""
    body = re.sub(r"\s*\n\s*", " ", m.group("body")).strip()
    return body


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def _parse_counts(text: str) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "positive_disease_reports": 0,
        "carrier_reports": 0,
        "acmg_secondary_findings": "",
    }
    m = re.search(
        r"Disease\s+results[\s\S]{0,80}?Positive\s+Reports?:\s*(\d+)", text
    )
    if m:
        counts["positive_disease_reports"] = int(m.group(1))
    m = re.search(
        r"Carrier\s+results[\s\S]{0,80}?Positive\s+Reports?:\s*(\d+)", text
    )
    if m:
        counts["carrier_reports"] = int(m.group(1))
    m = re.search(
        r"ACMG\s+Secondary\s+Findings:\s*([^\n]+)", text
    )
    if m:
        counts["acmg_secondary_findings"] = m.group(1).strip()
    return counts


# ---------------------------------------------------------------------------
# Variant table parsing
# ---------------------------------------------------------------------------


_REQUIRED_VARIANT_HEADERS = {"gene", "disease", "variant", "zygosity", "classif"}


def _looks_like_variant_table(table: list[list[str | None]]) -> bool:
    if not table or not table[0]:
        return False
    # Collapse all whitespace so PDF line-breaks like "Classificati\non" still
    # match — pdfplumber sometimes splits header words across vertical rows.
    header_blob = re.sub(r"\s+", "", " ".join(_clean(c) for c in table[0])).lower()
    return all(needle in header_blob for needle in _REQUIRED_VARIANT_HEADERS)


def _clean(value: str | None) -> str:
    """Collapse whitespace and stitch back word fragments split across PDF lines.

    BioAro PDFs frequently break a word over two table rows, which pdfplumber
    surfaces as e.g. ``"Classificati\\non"`` or ``"Hypothyroidis\\nm"``. We
    stitch only when the trailing fragment is at the end of the cell — that
    structural signal distinguishes a hyphenated word break from a real two
    word phrase like ``"Wilson's disease\\non chromosome 13"`` (followed by
    more text), which we leave alone.
    """
    if value is None:
        return ""
    # Newline-broken fragments at end-of-cell only: `"Classificati\non"` → `"Classification"`.
    stitched = re.sub(
        r"([A-Za-z]{3,}[a-z])\n([a-z]{1,3})(?=\s*$)",
        r"\1\2",
        value,
    )
    return re.sub(r"\s+", " ", stitched.replace("\n", " ").strip())


def _parse_variants(tables: list[list[list[str | None]]]) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [_clean(c).lower() for c in table[0]]
        index = {name: i for i, name in enumerate(header)}

        def col(row: list[str | None], *needles: str) -> str:
            for needle in needles:
                for name, i in index.items():
                    if needle in name:
                        return _clean(row[i] if i < len(row) else "")
            return ""

        for row in table[1:]:
            if not any(_clean(c) for c in row):
                continue
            variant_field = col(row, "variant")
            genomic_blob = col(row, "genomic")
            genomic_loc, transcript = _split_genomic_location(genomic_blob)
            classification = col(row, "classification")
            variant = {
                "gene": col(row, "gene"),
                "disease": col(row, "disease"),
                "mode_of_inheritance": col(row, "mode of inheritance", "inheritan"),
                "variant": variant_field,
                "genomic_location": genomic_loc,
                "transcript": transcript,
                "zygosity": col(row, "zygosity"),
                "classification": classification,
                "is_carrier": "carrier" in classification.lower(),
            }
            inherited_from = col(row, "inherited from", "inherite")
            if inherited_from:
                variant["inherited_from"] = inherited_from
            variants.append(variant)
    return variants


def _split_genomic_location(blob: str) -> tuple[str, str]:
    """`chr10:98427230; NM_000195` → (`chr10:98427230`, `NM_000195`)."""
    if not blob:
        return "", ""
    parts = re.split(r"[;,]", blob, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return blob.strip(), ""


# ---------------------------------------------------------------------------
# Gene-disease narrative info
# ---------------------------------------------------------------------------


_GENE_INFO_BLOCK_RE = re.compile(
    r"Gene-Disease\s+information:\s*(?P<body>.+?)(?:\n\s*BioAro\b|\Z)",
    re.DOTALL,
)
_GENE_HEADER_RE = re.compile(
    r"^(?P<gene>[A-Z][A-Z0-9\-]{1,15})\s*\(?(?P<full>[^)\n]+?)\)?\s*$",
    re.MULTILINE,
)
_PROTEIN_FN_RE = re.compile(
    r"Protein\s+Function:\s*(?P<body>.+?)(?=\n\s*(?:Clinical\s+Phenotype|BioAro|$))",
    re.DOTALL,
)
_CLINICAL_PHENO_RE = re.compile(
    r"Clinical\s+Phenotype[^:]*:\s*(?P<body>.+?)(?=\n\s*(?:[A-Z][A-Z0-9\-]{1,15}\s*\(|BioAro|Understanding\s+Your\s+Results|$))",
    re.DOTALL,
)


def _parse_gene_info(text: str) -> list[dict[str, str]]:
    block_match = _GENE_INFO_BLOCK_RE.search(text)
    if not block_match:
        return []
    body = block_match.group("body")

    # Split at gene-name headers — each header sits on its own line, e.g.
    # "IRS4 Insulin Receptor Substrate 4" or "MEGF10 (Multiple EGF...)".
    chunks = re.split(
        r"\n(?=[A-Z][A-Z0-9\-]{1,15}\s+(?:\(|[A-Z][a-z]))", body
    )
    out: list[dict[str, str]] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        header_line, _, rest = chunk.partition("\n")
        gene_match = re.match(
            r"(?P<gene>[A-Z][A-Z0-9\-]{1,15})\s*\(?(?P<full>[^)]+?)\)?\s*$",
            header_line.strip(),
        )
        if not gene_match:
            continue
        entry: dict[str, str] = {
            "gene": gene_match.group("gene"),
            "full_name": gene_match.group("full").strip(),
        }
        pf = _PROTEIN_FN_RE.search(rest)
        if pf:
            entry["protein_function"] = re.sub(r"\s+", " ", pf.group("body")).strip()
        cp = _CLINICAL_PHENO_RE.search(rest)
        if cp:
            entry["clinical_phenotype"] = re.sub(
                r"\s+", " ", cp.group("body")
            ).strip()
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Structured-context renderers (consumed by chatbot.pipeline)
# ---------------------------------------------------------------------------


def _build_analysis_context(report: dict[str, Any], *, site: str) -> str:
    lines: list[str] = []
    test_type = report.get("test", {}).get("type") or site.upper()
    lines.append(f"REPORT TYPE: {test_type}")

    patient = report.get("patient") or {}
    if patient:
        lines.append("PATIENT:")
        for k in ("name", "id", "gender", "dob", "collection_date", "report_date", "sample_type"):
            v = patient.get(k)
            if v:
                lines.append(f"  - {k}: {v}")

    summary = report.get("summary")
    if summary:
        lines.append("\nSUMMARY:")
        lines.append(f"  {summary}")

    counts = report.get("counts") or {}
    lines.append("\nCOUNTS:")
    lines.append(f"  - positive disease reports: {counts.get('positive_disease_reports', 0)}")
    lines.append(f"  - carrier reports: {counts.get('carrier_reports', 0)}")
    if counts.get("acmg_secondary_findings"):
        lines.append(f"  - ACMG secondary findings: {counts['acmg_secondary_findings']}")

    variants = report.get("variants") or []
    lines.append(f"\nVARIANTS ({len(variants)}):")
    if not variants:
        lines.append("  (none reported)")
    for i, v in enumerate(variants, 1):
        lines.append(
            f"  [{i}] {v.get('gene', '?')} | {v.get('disease', '?')} | "
            f"{v.get('mode_of_inheritance', '?')} | "
            f"variant={v.get('variant', '?')} | "
            f"loc={v.get('genomic_location', '?')} | "
            f"transcript={v.get('transcript', '?')} | "
            f"zygosity={v.get('zygosity', '?')} | "
            f"classification={v.get('classification', '?')}"
            + (f" | inherited_from={v['inherited_from']}" if v.get("inherited_from") else "")
            + (" | CARRIER" if v.get("is_carrier") else "")
        )

    gene_info = report.get("gene_info") or []
    if gene_info:
        lines.append("\nGENE-DISEASE INFO:")
        for g in gene_info:
            lines.append(f"  - {g.get('gene', '?')} ({g.get('full_name', '')})")
            if g.get("protein_function"):
                lines.append(f"      protein_function: {g['protein_function']}")
            if g.get("clinical_phenotype"):
                lines.append(f"      clinical_phenotype: {g['clinical_phenotype']}")

    return "\n".join(lines)


def _build_structured_summary(report: dict[str, Any], *, site: str) -> str:
    test_type = report.get("test", {}).get("type") or site.upper()
    counts = report.get("counts") or {}
    variants = report.get("variants") or []
    pos = counts.get("positive_disease_reports", 0)
    carriers = counts.get("carrier_reports", 0)
    parts = [
        f"{test_type} report with {len(variants)} reported variant(s):",
        f"{pos} disease finding(s), {carriers} carrier finding(s).",
    ]
    if variants:
        gene_str = ", ".join(v.get("gene", "?") for v in variants)
        parts.append(f"Genes: {gene_str}.")
    return " ".join(parts)
