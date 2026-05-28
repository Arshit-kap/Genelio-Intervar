"""Post-hoc validator for LLM-generated clinical-variant answers.

Even with a strict system prompt and the FILTERED VARIANTS anchor,
medgemma-27b will occasionally fabricate HGVS tokens that look real
(e.g. ``ABCA4:NM_001122457:exon13:c.C3079G:p.Q1027E`` when asked about
night blindness, despite ABCA4 not being in the patient's report).

This module is the deterministic safety net. It runs AFTER the answer
LLM and BEFORE the safety-tag stage:

1. Extract every ``GENE:NM_XXXXXX:exon\\d+:c.XXX[:p.XXX]`` token from
   the answer.
2. Cross-check each against the patient's actual ``parsed_data.report
   .raw_rows`` — match by gene + c. notation (transcript-agnostic so
   we don't reject legitimate alternate transcripts).
3. Replace fabricated tokens with a visible marker, and append a
   footer flagging the audit.

This is a no-cost deterministic check (no extra LLM call). Tradeoff:
when it fires, the user sees a marker in the assistant's text instead
of getting a re-generated response. Acceptable — better than silently
surfacing fake variants to a clinician.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


# Matches GENE:NM_NNNNNN(.N)?:exon\d+:c....(:p....)?
# Genes are 1-10 chars, uppercase + digits + hyphen. Tight enough to
# avoid matching arbitrary capitalised words.
_HGVS_RE = re.compile(
    r"\b([A-Z][A-Z0-9\-]{1,9})"               # gene symbol (group 1)
    r"\s*[:·]\s*"
    r"NM_\d+(?:\.\d+)?"                        # transcript
    r":exon\d+"                                # exon
    r":(c\.[A-Za-z0-9_>+\-*]+(?:_[A-Za-z0-9_>+\-*]+)?)"  # c.notation (group 2)
    r"(?::(p\.[A-Za-z0-9_*]+))?"               # p.notation (group 3, optional)
)


# Sub-pattern for extracting one entry from the patient's
# comma-joined AAChange.refGene column.
_PATIENT_HGVS_RE = re.compile(
    r"([A-Z][A-Z0-9\-]{1,9})"
    r":NM_\d+(?:\.\d+)?"
    r":exon\d+"
    r":(c\.[A-Za-z0-9_>+\-*]+(?:_[A-Za-z0-9_>+\-*]+)?)"
)


def _build_patient_index(parsed_data: dict) -> tuple[set[str], dict[str, set[str]]]:
    """Return ``(known_genes, gene_to_cnotations)`` from raw_rows.

    We index by (gene, c.notation) rather than by full HGVS because
    multiple transcripts give the same variant different exon numbers
    and we don't want to reject a legitimate alternate transcript.

    Handles two row shapes:
    - **clinical_csv**: raw CSV column names (``Ref.Gene``,
      ``AAChange.refGene``).
    - **intervar**: structured row dicts (``gene``, ``aac_refgene``,
      with a parallel ``indexes.by_gene`` index for fast member-check).
    """
    report = (parsed_data or {}).get("report") or {}
    rows: list[dict[str, Any]] = report.get("raw_rows") or []
    known_genes: set[str] = set()
    gene_to_cdot: dict[str, set[str]] = {}

    # InterVar fast-path: ``indexes.by_gene`` is already a complete
    # gene-symbol set; we trust it for membership and only walk rows
    # to harvest c.-notation pairs.
    intervar_idx = (report.get("indexes") or {}).get("by_gene")
    if isinstance(intervar_idx, dict):
        known_genes |= {g.upper() for g in intervar_idx.keys() if g}

    for row in rows:
        # Read either the structured (intervar) or raw-CSV (clinical_csv)
        # field for gene + HGVS — whichever is present.
        gene = (row.get("Ref.Gene") or row.get("gene") or "").strip()
        if gene:
            # Multi-gene cells like "NOC2L,SAMD11" — split.
            for g in gene.split(","):
                g = g.strip().upper()
                if g:
                    known_genes.add(g)
        aac = row.get("AAChange.refGene") or row.get("aac_refgene") or ""
        for entry in aac.split(","):
            m = _PATIENT_HGVS_RE.match(entry.strip())
            if m:
                g, c = m.group(1).upper(), m.group(2)
                gene_to_cdot.setdefault(g, set()).add(c)
                known_genes.add(g)
    return known_genes, gene_to_cdot


def validate_answer(answer: str, parsed_data: dict) -> tuple[str, list[str]]:
    """Strip / mark fabricated HGVS tokens.

    Returns ``(cleaned_answer, fabricated_tokens)``. The returned
    string has unverified tokens replaced with a visible marker, and
    a footer paragraph appended if anything was caught.
    """
    if not answer:
        return answer, []

    known_genes, gene_to_cdot = _build_patient_index(parsed_data)

    fabricated: list[str] = []
    # Walk the answer left-to-right, building a new string with each
    # match either kept as-is or replaced with a marker.
    out_parts: list[str] = []
    last_end = 0
    for m in _HGVS_RE.finditer(answer):
        out_parts.append(answer[last_end:m.start()])
        full = m.group(0)
        gene = m.group(1).upper()
        c_dot = m.group(2)
        # Tiered verification:
        # 1. Strong match — gene in patient AND c. notation matches one
        #    of the patient's recorded c. notations for that gene.
        # 2. Weak match — gene is in patient but we don't have any
        #    parsed c. notations for it. For InterVar reports this is
        #    the common case (we only parse a compact view of
        #    AAChange.refGene). Accept the token rather than risk
        #    false-positive stripping of a legitimate variant the LLM
        #    quoted from the report's raw text.
        # 3. Reject — gene not in patient at all.
        if gene in gene_to_cdot and c_dot in gene_to_cdot[gene]:
            out_parts.append(full)
        elif gene in known_genes and gene not in gene_to_cdot:
            # We can't verify the c. notation but the gene is real;
            # mark as 'unverified-c-notation' but keep the token so we
            # don't false-positive on legitimate variants.
            out_parts.append(full)
        else:
            reason = (
                f"variant not in patient for {gene}"
                if gene in known_genes
                else f"gene {gene} not in patient"
            )
            log.warning(
                "validator: stripping fabricated HGVS %r (%s)", full, reason,
            )
            fabricated.append(full)
            out_parts.append(f"[⚠️ unverified: {gene} — variant not in your report]")
        last_end = m.end()
    out_parts.append(answer[last_end:])
    cleaned = "".join(out_parts)

    if fabricated:
        n = len(fabricated)
        plural = "" if n == 1 else "s"
        cleaned += (
            "\n\n> ⚠️ **Auto-validator note:** "
            f"{n} HGVS variant token{plural} in this reply did not match any "
            "row in your actual report and {has} been marked as unverified. "
            "This is the validator catching a known failure mode where the "
            "language model invents plausible-looking variants based on the "
            "gene–disease topic. Only variants the report actually contains "
            "should be acted on; please re-ask focused on a specific gene "
            "if you'd like a clean answer."
        ).replace("{has}", "has" if n == 1 else "have")

    return cleaned, fabricated
