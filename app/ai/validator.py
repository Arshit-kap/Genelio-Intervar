"""Post-hoc HGVS hallucination validator.

Strips fabricated HGVS tokens from LLM-generated answers by cross-checking
against the actual variant rows returned by the executor.

Adapted from interval_02/chatbot/agentic/validator.py.

Flow:
  1. Extract every GENE:NM_XXXXXX:exonN:c.XXX[:p.XXX] token from the answer.
  2. Cross-check each against the query result rows (gene + c. notation).
  3. Replace fabricated tokens with a [⚠️ unverified: GENE] marker.
  4. Append a footer warning when tokens were caught.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


# Matches GENE:NM_NNNNNN(.N)?:exon\d+:c....(:p....)?
_HGVS_RE = re.compile(
    r"\b([A-Z][A-Z0-9\-]{1,9})"            # gene symbol (group 1)
    r"\s*[:·]\s*"
    r"NM_\d+(?:\.\d+)?"                     # transcript
    r":exon\d+"                             # exon
    r":(c\.[A-Za-z0-9_>+\-*]+(?:_[A-Za-z0-9_>+\-*]+)?)"  # c. notation (group 2)
    r"(?::(p\.[A-Za-z0-9_*]+))?"            # p. notation (group 3, optional)
)

# Sub-pattern for parsing AAChange.refGene column entries.
_PATIENT_HGVS_RE = re.compile(
    r"([A-Z][A-Z0-9\-]{1,9})"
    r":NM_\d+(?:\.\d+)?"
    r":exon\d+"
    r":(c\.[A-Za-z0-9_>+\-*]+(?:_[A-Za-z0-9_>+\-*]+)?)"
)


def _build_patient_index(rows: list[dict[str, Any]]) -> tuple[set[str], dict[str, set[str]]]:
    """Build (known_genes, gene_to_cnotations) from the executor's result rows.

    Handles our SQLite column names: "Ref.Gene" and "AAChange.refGene".
    """
    known_genes: set[str] = set()
    gene_to_cdot: dict[str, set[str]] = {}

    for row in rows:
        gene = (row.get("Ref.Gene") or "").strip()
        if gene:
            for g in gene.split(","):
                g = g.strip().upper()
                if g:
                    known_genes.add(g)

        aac = row.get("AAChange.refGene") or ""
        for entry in aac.split(","):
            m = _PATIENT_HGVS_RE.match(entry.strip())
            if m:
                g, c = m.group(1).upper(), m.group(2)
                gene_to_cdot.setdefault(g, set()).add(c)
                known_genes.add(g)

    return known_genes, gene_to_cdot


def validate_answer(answer: str, rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Strip fabricated HGVS tokens from an LLM answer.

    Args:
        answer: The raw LLM-generated answer text.
        rows:   The actual variant rows the executor returned (used as ground truth).

    Returns:
        (cleaned_answer, fabricated_tokens)
        cleaned_answer has unverified HGVS tokens replaced with a visible marker.
        fabricated_tokens is a list of the raw token strings that were replaced.
    """
    if not answer:
        return answer, []

    known_genes, gene_to_cdot = _build_patient_index(rows)

    fabricated: list[str] = []
    out_parts: list[str] = []
    last_end = 0

    for m in _HGVS_RE.finditer(answer):
        out_parts.append(answer[last_end:m.start()])
        full = m.group(0)
        gene  = m.group(1).upper()
        c_dot = m.group(2)

        # Tiered verification:
        # 1. Strong match — gene in patient AND c. notation matches → keep
        # 2. Weak match  — gene in patient but no c. index → keep (avoid false positives)
        # 3. Reject      — gene not in patient at all → replace with marker
        if gene in gene_to_cdot and c_dot in gene_to_cdot[gene]:
            out_parts.append(full)
        elif gene in known_genes and gene not in gene_to_cdot:
            # Gene is real; can't verify the exact c. notation → keep token
            out_parts.append(full)
        else:
            reason = (
                f"variant not in result set for {gene}"
                if gene in known_genes
                else f"gene {gene} not in result set"
            )
            log.warning("validator: stripped fabricated HGVS %r (%s)", full, reason)
            fabricated.append(full)
            out_parts.append(f"[⚠️ unverified: {gene} — variant not in your report]")

        last_end = m.end()

    out_parts.append(answer[last_end:])
    cleaned = "".join(out_parts)

    if fabricated:
        n = len(fabricated)
        plural = "" if n == 1 else "s"
        cleaned += (
            f"\n\n> ⚠️ **Auto-validator note:** "
            f"{n} HGVS variant token{plural} in this reply did not match any "
            "row in the query result and {'has' if n == 1 else 'have'} been marked as unverified. "
            "Only variants from the actual query results should be acted on."
        ).replace(
            "{'has' if n == 1 else 'have'}",
            "has" if n == 1 else "have",
        )

    return cleaned, fabricated
