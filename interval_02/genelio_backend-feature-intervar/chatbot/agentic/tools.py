"""Pure-Python tools the orchestrator can call.

Each tool is deterministic, takes a Report + tool-specific args, and
returns either structured data or a short text block. None of these
make LLM calls; that's the orchestrator's job. Keeping the tools side-
effect-free makes testing easy and the agent flow predictable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from reports.analyzers.clinical_csv import COLUMN_REFERENCE

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Combined ClinVar + InterVar pathogenicity rule
# (from §3 "Combined Pathogenicity Rule" of the multi-API integration spec)
#
# The bioinformatician spec is explicit that the previous "either source
# alone determines surfacing" was wrong — it surfaced VUS rows as if
# they were actionable and conflated InterVar-only calls with
# expert-curated ClinVar calls. The new rule:
#
#   Both agree pathogenic              → STRONG_PATHOGENIC      (1)
#   ClinVar pathogenic only            → CLINVAR_PATHOGENIC     (2)
#   InterVar pathogenic only           → INTERVAR_PATHOGENIC    (3)
#   Conflicting ClinVar, InterVar path → CONFLICTING_PATHOGENIC (4)
#   Both VUS BUT CADD>20 AND REVEL>0.5 AND rare → VUS_HIGH_SCORE (5)
#   Benign + freq>5%                   → DROP
#   Anything else                      → DROP (de-prioritised by default;
#                                              callers can opt in with
#                                              apply_pathogenicity_filter
#                                              =False to see everything)
#
# Callers branch on Bucket for response wording — "Strong evidence" vs
# "ClinVar-supported" vs "Algorithm-predicted" vs "Conflicting (caveat)"
# vs "VUS with high computational support" — because the same gene/HGVS
# means very different things clinically depending on which bucket it
# falls into.
# ----------------------------------------------------------------------


class Bucket(str, Enum):
    """Named pathogenicity buckets — per §3 of the spec."""
    STRONG_PATHOGENIC = "STRONG_PATHOGENIC"
    CLINVAR_PATHOGENIC = "CLINVAR_PATHOGENIC"
    INTERVAR_PATHOGENIC = "INTERVAR_PATHOGENIC"
    CONFLICTING_PATHOGENIC = "CONFLICTING_PATHOGENIC"
    VUS_HIGH_SCORE = "VUS_HIGH_SCORE"
    DROP = "DROP"


# Surfacing order — lower is surfaced first. Used as the primary sort
# key everywhere the orchestrator emits a ranked list.
BUCKET_PRIORITY: dict[Bucket, int] = {
    Bucket.STRONG_PATHOGENIC: 1,
    Bucket.CLINVAR_PATHOGENIC: 2,
    Bucket.INTERVAR_PATHOGENIC: 3,
    Bucket.CONFLICTING_PATHOGENIC: 4,
    Bucket.VUS_HIGH_SCORE: 5,
    Bucket.DROP: 99,
}

# Patient-facing labels used by category renderers. Single source of
# truth so the wording stays consistent across A1 / C1 / C2 / D1 / F1.
BUCKET_LABEL: dict[Bucket, str] = {
    Bucket.STRONG_PATHOGENIC: "Strong evidence (both ClinVar and InterVar agree pathogenic)",
    Bucket.CLINVAR_PATHOGENIC: "ClinVar-supported pathogenic (human-curated)",
    Bucket.INTERVAR_PATHOGENIC: "Algorithm-predicted pathogenic (InterVar/ACMG)",
    Bucket.CONFLICTING_PATHOGENIC: "Conflicting ClinVar — InterVar leans pathogenic (interpret with caution)",
    Bucket.VUS_HIGH_SCORE: "Uncertain significance, but high computational pathogenicity scores",
    Bucket.DROP: "(dropped by combined pathogenicity rule)",
}

# Allele-frequency threshold above which an "Either Benign + freq>5%"
# row is dropped wholesale (spec §3).
_FREQ_DROP_THRESHOLD = 0.05

# VUS surfacing thresholds (spec §3).
_VUS_CADD_THRESHOLD = 20.0
_VUS_REVEL_THRESHOLD = 0.5
_VUS_RARE_THRESHOLD = 0.01  # gnomAD ALL — "rare"


def _f(v: Any) -> float | None:
    """Best-effort float coercion; ``"."`` / empty → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in (".", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _max_freq(row: dict[str, Any]) -> float:
    """Return the highest allele-frequency we can read from the row.

    The CSV carries several frequency columns (gnomAD, ESP6500,
    1000 Genomes). For the "Either Benign + freq>5%" drop rule we take
    the maximum — if any cohort sees this variant at >5% it cannot be a
    rare disease allele.
    """
    cols = (
        "Freq_gnomAD_genome_ALL",
        "Freq_esp6500siv2_all",
        "Freq_1000g2015aug_all",
    )
    best = 0.0
    for c in cols:
        f = _f(row.get(c))
        if f is not None and f > best:
            best = f
    return best


def _clinvar_status(row: dict[str, Any]) -> tuple[bool, bool, bool, bool]:
    """Decode the ClinVar significance field into four booleans:

    (path, likely_path, conflicting, benign)
    """
    cv = (row.get("clinvar: Clinvar") or "").lower()
    conflicting = "conflicting" in cv
    # When ClinVar says Conflicting, the per-bucket spec is explicit
    # that the row is NOT counted as plain pathogenic — it's its own
    # bucket. So we don't OR conflicting into path/likely_path.
    path = cv.startswith("pathogenic") and "likely" not in cv and not conflicting
    likely_path = "likely_pathogenic" in cv and not conflicting
    benign = "benign" in cv and "pathogenic" not in cv
    return path, likely_path, conflicting, benign


def _intervar_status(row: dict[str, Any]) -> tuple[bool, bool]:
    """Decode the InterVar ACMG verdict into (path, likely_path).

    The clinical_csv column ``InterVar: InterVar and Evidence`` carries
    a string like ``"Pathogenic PVS1=1, PS1=0, ..."`` — we test
    case-insensitively on the verdict prefix.
    """
    iv = (row.get("InterVar: InterVar and Evidence") or "").lower()
    # InterVar's "Likely pathogenic" / "Pathogenic" verdicts; PVS1=YES
    # ACMG flag in the local column is also treated as InterVar pathogenic
    # (the CSV bakes the algorithmic ACMG call into per-criterion flags).
    iv_pathogenic_verdict = iv.startswith("pathogenic") and "likely" not in iv
    iv_likely_pathogenic_verdict = "likely pathogenic" in iv
    pvs1 = (row.get("PVS1") or "").upper() == "YES"
    return (iv_pathogenic_verdict or pvs1, iv_likely_pathogenic_verdict)


def pathogenicity_bucket(row: dict[str, Any]) -> Bucket:
    """Combined ClinVar + InterVar bucketing — see spec §3.

    Returns a :class:`Bucket`. Callers can either branch on the bucket
    (e.g. categories.py wording) or look up :data:`BUCKET_PRIORITY` for
    the numeric surfacing rank.

    Note on the ``func`` gate: previously we hard-skipped non-exonic /
    non-splicing variants. The new combined rule keeps that gate ONLY
    for the ``DROP``-when-uninteresting tail; an exonic/splicing
    requirement still applies to the VUS bucket (a non-exonic VUS is
    noise) but pathogenic ClinVar/InterVar calls survive even if they
    sit in a regulatory region — those calls are expert review, not
    computational guesses.
    """
    cv_path, cv_likely, cv_conflicting, cv_benign = _clinvar_status(row)
    iv_path, iv_likely = _intervar_status(row)
    max_freq = _max_freq(row)

    # ── Either Benign + freq>5% → DROP wholesale.
    if cv_benign and max_freq > _FREQ_DROP_THRESHOLD:
        return Bucket.DROP
    iv_benign = "benign" in (row.get("InterVar: InterVar and Evidence") or "").lower() \
        and "pathogenic" not in (row.get("InterVar: InterVar and Evidence") or "").lower()
    if iv_benign and max_freq > _FREQ_DROP_THRESHOLD:
        return Bucket.DROP

    clinvar_is_pathogenic = cv_path or cv_likely
    intervar_is_pathogenic = iv_path or iv_likely

    # ── Both agree → Strong evidence
    if clinvar_is_pathogenic and intervar_is_pathogenic:
        return Bucket.STRONG_PATHOGENIC

    # ── ClinVar only (and not contradicted by Conflicting)
    if clinvar_is_pathogenic and not cv_conflicting:
        return Bucket.CLINVAR_PATHOGENIC

    # ── Conflicting ClinVar but InterVar leans pathogenic
    if cv_conflicting and intervar_is_pathogenic:
        return Bucket.CONFLICTING_PATHOGENIC

    # ── InterVar only — surface even when ClinVar leans benign
    # (after the Benign + freq>5% drop has been applied above). Per
    # spec §3, the only freq-driven drop is the explicit Benign+>5%
    # rule; an InterVar-pathogenic call that dissents from a ClinVar
    # benign call is exactly the "Algorithm-predicted" bucket the
    # bioinformaticians want surfaced. The wording (Bucket.INTERVAR_
    # PATHOGENIC's label) already tells the patient ClinVar didn't
    # corroborate.
    if intervar_is_pathogenic:
        return Bucket.INTERVAR_PATHOGENIC

    # ── Both VUS / Uncertain → only surface when ALL three thresholds met.
    # The benign guard here is intentional: if either source already
    # called the variant (likely) benign, the row is NOT a VUS — it's a
    # negative call that lost the freq drop above only because freq<5%.
    func = (row.get("Func.refGene") or "").lower()
    looks_coding = (not func) or func in ("exonic", "splicing", "exonic;splicing")
    cadd = _f(row.get("CADD_phred"))
    revel = _f(row.get("REVEL_score"))
    rare = max_freq < _VUS_RARE_THRESHOLD
    if (
        looks_coding
        and not cv_benign and not iv_benign
        and cadd is not None and cadd > _VUS_CADD_THRESHOLD
        and revel is not None and revel > _VUS_REVEL_THRESHOLD
        and rare
    ):
        return Bucket.VUS_HIGH_SCORE

    return Bucket.DROP


def pathogenicity_priority(row: dict[str, Any]) -> int | None:
    """Back-compat shim — returns the numeric priority or ``None`` for DROP.

    Existing callers (and the InterVar orchestrator) expect this name +
    return shape. Internally it delegates to :func:`pathogenicity_bucket`.
    """
    b = pathogenicity_bucket(row)
    if b is Bucket.DROP:
        return None
    return BUCKET_PRIORITY[b]


# ----------------------------------------------------------------------
# Variant filtering
# ----------------------------------------------------------------------

@dataclass
class FilterResult:
    """Output of :func:`filter_variants` — kept structured for the prompt."""
    matched: list[dict[str, Any]]
    skipped: int          # how many variants were filtered out
    universe: int         # total variants in the report
    candidate_genes: list[str]  # the gene set we intersected with
    # Parallel list — one bucket per matched row, same order. None
    # when ``apply_pathogenicity_filter=False`` (no bucketing was done).
    buckets: list[Bucket] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.buckets is None:
            self.buckets = []


def filter_variants(
    parsed_data: dict[str, Any],
    *,
    gene_filter: Iterable[str] | None = None,
    variant_filter: str | None = None,
    flag_filter: str | None = None,
    apply_pathogenicity_filter: bool = True,
    max_results: int = 8,
) -> FilterResult:
    """Filter the patient's variant rows by gene / specific variant / flag.

    Args:
        parsed_data: ``Report.parsed_data`` from a clinical_csv report.
        gene_filter: optional set of gene symbols to intersect against
            ``Ref.Gene``.
        variant_filter: optional substring to match against the
            HGVS notation in ``AAChange.refGene``.
        flag_filter: optional column name to require ``=YES`` (e.g.
            ``"Actionable"``, ``"PVS1"``, ``"Secondary_Finding"``).
        apply_pathogenicity_filter: if True, also drop rows the
            pathogenicity decision tree marks as not-worth-surfacing.
        max_results: cap the matched list. Always returns ranked output.

    Returns ``FilterResult`` with matches in pathogenicity-priority
    order (most clinically significant first), tie-broken by CADD.
    """
    rows: list[dict[str, Any]] = (
        (parsed_data or {}).get("report", {}).get("raw_rows", []) or []
    )
    gene_set = {g.upper() for g in (gene_filter or [])} if gene_filter else None
    var_norm = variant_filter.strip().lower() if variant_filter else None
    flag_norm = flag_filter.strip() if flag_filter else None

    candidates: list[tuple[int, float, dict[str, Any], Bucket]] = []
    for row in rows:
        # Gene filter
        if gene_set is not None:
            if (row.get("Ref.Gene") or "").upper() not in gene_set:
                continue
        # Specific-variant filter (substring on AAChange.refGene)
        if var_norm:
            aac = (row.get("AAChange.refGene") or "").lower()
            if var_norm not in aac:
                continue
        # YES-flag filter
        if flag_norm:
            if (row.get(flag_norm) or "").upper() != "YES":
                continue

        # Combined pathogenicity bucketing
        bucket = pathogenicity_bucket(row)
        if apply_pathogenicity_filter and bucket is Bucket.DROP:
            continue
        prio = BUCKET_PRIORITY[bucket]

        cadd = _f(row.get("CADD_phred")) or 0.0
        candidates.append((prio, -cadd, row, bucket))

    candidates.sort(key=lambda x: (x[0], x[1]))
    picked = candidates[:max_results]
    matched = [c[2] for c in picked]
    buckets = [c[3] for c in picked]

    return FilterResult(
        matched=matched,
        skipped=len(rows) - len(matched),
        universe=len(rows),
        candidate_genes=sorted(gene_set) if gene_set else [],
        buckets=buckets,
    )


# ----------------------------------------------------------------------
# Renderers — turn structured output into compact text blocks for the
# answer-generator prompt. Tight enough not to blow the token budget.
# ----------------------------------------------------------------------

def render_variant(row: dict[str, Any]) -> str:
    """Single-variant compact text block."""
    lines = []
    head = " · ".join(filter(None, [
        row.get("Ref.Gene"),
        row.get("AAChange.refGene", "").split(",")[0].strip() if row.get("AAChange.refGene") else None,
        row.get("Consequence") or row.get("ExonicFunc.refGene"),
    ]))
    lines.append(f"• {head}")
    fields = [
        ("ClinVar", "clinvar: Clinvar"),
        ("Disease", "ClinVar_Disease"),
        ("Zygosity", "Zygosity"),
        ("Inheritance", "Mode_of_Inheritance"),
        ("CADD_phred", "CADD_phred"),
        ("REVEL", "REVEL_score"),
        ("Impact", "Impact"),
    ]
    for label, col in fields:
        v = row.get(col)
        if v and v != ".":
            lines.append(f"    {label}: {v}")
    acmg = [c for c in ("PVS1", "PM2", "PP3", "BA1")
            if (row.get(c) or "").upper() == "YES"]
    if acmg:
        lines.append(f"    ACMG MET: {', '.join(acmg)}")
    flags = [c for c in ("Primary_Finding", "Secondary_Finding",
                          "Carrier_Status", "Actionable",
                          "Pharmacogenomic_Association")
             if (row.get(c) or "").upper() == "YES" or
                (c == "Pharmacogenomic_Association" and row.get(c))]
    if flags:
        lines.append(f"    Clinical flags: {', '.join(flags)}")
    return "\n".join(lines)


def render_filter_result(fr: FilterResult) -> str:
    """Text block for the answer-generator prompt."""
    if not fr.matched:
        if fr.candidate_genes:
            return (
                f"FILTERED VARIANTS: none.\n"
                f"  Searched {fr.universe} patient variants against "
                f"{len(fr.candidate_genes)} candidate genes "
                f"(from HPO resolution). No matches passed the "
                f"combined ClinVar+InterVar pathogenicity filter."
            )
        return f"FILTERED VARIANTS: none. (Searched {fr.universe} variants.)"
    lines = [
        f"FILTERED VARIANTS ({len(fr.matched)} surfaced, "
        f"{fr.skipped} de-emphasized from {fr.universe} total):"
    ]
    if fr.candidate_genes:
        lines.append(
            f"  (intersected with HPO-derived gene set: "
            f"{', '.join(fr.candidate_genes[:15])}"
            f"{'…' if len(fr.candidate_genes) > 15 else ''})"
        )
    lines.append("")
    # Walk matched + buckets in lockstep so the LLM sees the named
    # evidence tier ("Strong evidence" / "Algorithm-predicted" / etc.)
    # alongside every row. This stops the model from collapsing five
    # different evidence levels into one undifferentiated list.
    buckets = fr.buckets or [Bucket.DROP] * len(fr.matched)
    for row, bucket in zip(fr.matched, buckets):
        lines.append(f"  [evidence tier: {BUCKET_LABEL[bucket]}]")
        lines.append(render_variant(row))
        lines.append("")
    return "\n".join(lines).rstrip()


def render_hpo_profile(hpo_terms: list[dict[str, Any]]) -> str:
    """Render the session phenotype profile for the prompt."""
    if not hpo_terms:
        return ""
    lines = ["SESSION HPO PROFILE (symptoms the user has reported):"]
    for t in hpo_terms:
        if not t.get("hpo_id"):
            continue
        lines.append(
            f"  • {t['hpo_id']} {t['name']} "
            f"(matched from {t.get('input_text', t.get('matched_via', '?'))!r}, "
            f"confidence={t.get('confidence', 0):.2f})"
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Schema lookup
# ----------------------------------------------------------------------

def lookup_column(name: str) -> str | None:
    """Return the one-line definition for a column, or None if unknown."""
    # Try exact, then case-insensitive
    if name in COLUMN_REFERENCE:
        return COLUMN_REFERENCE[name]
    lower = {k.lower(): v for k, v in COLUMN_REFERENCE.items()}
    return lower.get(name.lower())
