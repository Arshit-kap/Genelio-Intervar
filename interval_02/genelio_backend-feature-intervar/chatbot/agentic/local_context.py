"""Local-first context readers for the clinical_csv chat path.

Spec §1 + §5: "The CSV row carries pre-computed columns. Always look
locally before calling any external API." This module is the single
place that knows how to read those pre-computed columns. Everything
else (orchestrator, category renderers, external_apis) goes through
this layer so the local-first contract is enforced by construction.

Columns consulted here (per ``reports.analyzers.clinical_csv``):

  Mode_of_Inheritance   AD | AR | XLD | XLR | mitochondrial | …
  OMIM_ID               OMIM phenotype/gene IDs (pipe-joined when many)
  MONDO_ID              MONDO disease IDs (pipe-joined)
  HPO_ID                HPO phenotype IDs (pipe-joined)
  ClinVar_Disease       free-text disease name(s)
  ClinVar_Disease_DB    cross-db links (MedGen, OMIM, SNOMED_CT)
  Zygosity              Heterozygous | Homozygous | Hemizygous
  Carrier_Status        YES = carrier of a recessive allele
  Secondary_Finding     YES = ACMG SF list incidental finding
  Actionable            YES = established clinical action exists

All readers return ``None``/empty when the cell is missing — the
caller is responsible for falling back to the external-API layer.
"""
from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Inheritance pattern
# ---------------------------------------------------------------------------

# Canonical normalisation map. Multiple upstream sources spell the same
# pattern several ways ("AD" vs "Autosomal Dominant" vs "autosomal_dominant")
# and the patient-facing explanation paragraph depends on getting the
# canonical key right.
_INHERITANCE_CANONICAL: dict[str, str] = {
    "ad": "Autosomal Dominant",
    "autosomal dominant": "Autosomal Dominant",
    "autosomal_dominant": "Autosomal Dominant",
    "ar": "Autosomal Recessive",
    "autosomal recessive": "Autosomal Recessive",
    "autosomal_recessive": "Autosomal Recessive",
    "xld": "X-Linked Dominant",
    "x-linked dominant": "X-Linked Dominant",
    "x linked dominant": "X-Linked Dominant",
    "xlr": "X-Linked Recessive",
    "x-linked recessive": "X-Linked Recessive",
    "x linked recessive": "X-Linked Recessive",
    "xl": "X-Linked",
    "x-linked": "X-Linked",
    "x linked": "X-Linked",
    "mt": "Mitochondrial",
    "mitochondrial": "Mitochondrial",
    "maternal": "Mitochondrial",
    "y": "Y-Linked",
    "y-linked": "Y-Linked",
    "yl": "Y-Linked",
    "multifactorial": "Multifactorial",
    "complex": "Multifactorial",
}


def inheritance_pattern(row: dict[str, Any]) -> str | None:
    """Return the canonical inheritance pattern, or None if absent.

    Spec §5 (Inheritance Resolution): "Local Mode_of_Inheritance column
    first; OMIM clinicalSynopsis fallback; HPO inheritance subtree
    fallback." This function covers the first hop.
    """
    raw = (row.get("Mode_of_Inheritance") or "").strip()
    if not raw:
        return None
    key = raw.lower().strip()
    if key in _INHERITANCE_CANONICAL:
        return _INHERITANCE_CANONICAL[key]
    # Best-effort: contains-match for compound strings like
    # "Autosomal Dominant; rarely Autosomal Recessive"
    for token, canonical in _INHERITANCE_CANONICAL.items():
        if token in key and len(token) >= 3:
            return canonical
    return raw  # surface whatever the column said — better than dropping it


# Patient-friendly significance text from PDF §E1.
INHERITANCE_SIGNIFICANCE: dict[str, str] = {
    "Autosomal Dominant": (
        "One pathogenic copy is enough to cause disease. 50% chance per "
        "child. Family members are often affected across generations."
    ),
    "Autosomal Recessive": (
        "Both copies of the gene must be variant. Heterozygous carriers "
        "(one copy) are usually unaffected; homozygous or compound "
        "heterozygous (two copies) are affected. 25% recurrence risk "
        "per child if both parents are carriers."
    ),
    "X-Linked Dominant": (
        "A variant on the X chromosome causes disease in both sexes. "
        "Affected fathers pass it to all daughters but none of their sons."
    ),
    "X-Linked Recessive": (
        "Males with one variant copy are affected (hemizygous); "
        "heterozygous females are typically carriers."
    ),
    "X-Linked": (
        "Variant on the X chromosome — risk depends on sex of the carrier "
        "and the specific pattern (dominant vs recessive)."
    ),
    "Mitochondrial": (
        "Maternal inheritance only — all children of an affected mother "
        "inherit the variant; can show heteroplasmy (mixed mutant/wild "
        "mitochondria)."
    ),
    "Y-Linked": "Father to all sons; never to daughters.",
    "Multifactorial": (
        "Driven by a combination of genetic and environmental factors — "
        "single-variant risk modelling does not apply cleanly."
    ),
}


# ---------------------------------------------------------------------------
# Cross-reference ID readers (OMIM / MONDO / HPO / Orphanet)
# ---------------------------------------------------------------------------

_ID_SEP_RE = re.compile(r"[|;,\s]+")


def _split_ids(raw: str | None) -> list[str]:
    """Split a pipe/comma/semicolon-joined ID cell into clean tokens.

    Drops empty fragments and obvious sentinels like ``.``.
    """
    if not raw:
        return []
    out: list[str] = []
    for tok in _ID_SEP_RE.split(str(raw)):
        tok = tok.strip()
        if not tok or tok == ".":
            continue
        out.append(tok)
    return out


def omim_ids(row: dict[str, Any]) -> list[str]:
    """Local OMIM IDs for this variant (gene + phenotype mixed)."""
    return _split_ids(row.get("OMIM_ID"))


def mondo_ids(row: dict[str, Any]) -> list[str]:
    """Local MONDO disease IDs for this variant."""
    return _split_ids(row.get("MONDO_ID"))


def hpo_ids(row: dict[str, Any]) -> list[str]:
    """Local HPO phenotype IDs for this variant.

    Spec §2 Symptom/phenotype-based: "Use the local HPO_ID, OMIM_ID,
    MONDO_ID columns. If those are sparse, fall back to HPO API."
    """
    return _split_ids(row.get("HPO_ID"))


def clinvar_disease_names(row: dict[str, Any]) -> list[str]:
    """Free-text disease names ClinVar associates with the variant."""
    raw = (row.get("ClinVar_Disease") or "").strip()
    if not raw or raw == ".":
        return []
    # ClinVar uses ``|`` to separate alternative disease names.
    return [n.strip() for n in raw.split("|") if n.strip() and n.strip() != "."]


# ---------------------------------------------------------------------------
# Clinical flag readers
# ---------------------------------------------------------------------------

def _yes(row: dict[str, Any], col: str) -> bool:
    return (row.get(col) or "").upper() == "YES"


def is_carrier(row: dict[str, Any]) -> bool:
    """Spec §F1: filter ``Carrier_Status == YES``."""
    return _yes(row, "Carrier_Status")


def is_secondary_finding(row: dict[str, Any]) -> bool:
    """Spec §F2: pre-computed ACMG SF list flag — local DB only."""
    return _yes(row, "Secondary_Finding")


def is_primary_finding(row: dict[str, Any]) -> bool:
    return _yes(row, "Primary_Finding")


def is_actionable(row: dict[str, Any]) -> bool:
    return _yes(row, "Actionable")


def zygosity(row: dict[str, Any]) -> str | None:
    """Normalised zygosity — Heterozygous | Homozygous | Hemizygous."""
    z = (row.get("Zygosity") or "").strip()
    if not z or z == ".":
        return None
    low = z.lower()
    if low.startswith("het"):
        return "Heterozygous"
    if low.startswith("hom"):
        return "Homozygous"
    if low.startswith("hemi"):
        return "Hemizygous"
    return z


# ---------------------------------------------------------------------------
# Reproductive-risk paragraph builder (used by E1, E2, F1, C1)
# ---------------------------------------------------------------------------

def reproductive_paragraph(
    inheritance: str | None, zyg: str | None,
) -> str | None:
    """One paragraph of patient-conditional inheritance interpretation.

    Spec §E1: render a paragraph CONDITIONAL on the patient's actual
    zygosity ("Since you have one copy, you're a carrier…" rather than
    a generic recitation of inheritance rules). Returns ``None`` when
    we don't have enough info to say anything responsible.
    """
    if not inheritance:
        return None
    z = (zyg or "").lower()
    inh = inheritance

    if inh == "Autosomal Recessive":
        if "het" in z:
            return (
                "Since you carry one copy of this variant, you are a "
                "**carrier** — typically unaffected yourself, but the "
                "variant can be passed to children. If your partner is "
                "also a carrier of a pathogenic variant in the same gene, "
                "each child has a 25% chance of being affected."
            )
        if "hom" in z:
            return (
                "You carry two copies of this variant (homozygous). "
                "Autosomal recessive conditions typically express clinical "
                "features in homozygous individuals."
            )

    if inh == "Autosomal Dominant" and "het" in z:
        return (
            "A single variant copy is generally sufficient to cause an "
            "autosomal-dominant condition. Each child has a 50% chance "
            "of inheriting this variant."
        )

    if inh == "X-Linked Recessive":
        if "hemi" in z:
            return (
                "As a hemizygous male with the variant on your X chromosome, "
                "you would be expected to express the condition. All your "
                "daughters become carriers; no sons inherit the X-linked "
                "variant from you."
            )
        if "het" in z:
            return (
                "As a heterozygous female (one X-linked variant copy), you "
                "are typically a carrier — generally unaffected, though "
                "some X-linked recessive conditions show variable "
                "expression in carriers."
            )

    if inh == "Mitochondrial":
        return (
            "Mitochondrial variants are inherited from the mother. All "
            "children of an affected mother inherit the variant, though "
            "expression can vary because of heteroplasmy (the mix of "
            "mutant and wild-type mitochondria differs between cells)."
        )

    return None
