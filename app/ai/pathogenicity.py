"""6-bucket ClinVar + InterVar combined pathogenicity rule.

Adapted from interval_02/chatbot/agentic/tools.py for the SQLite InterVar
column schema used in patient_variants.db.

Column names as returned by SQL queries on our DB:
  "Ref.Gene"                          gene symbol
  "Func.refGene"                      variant function region
  "clinvar: Clinvar"                  ClinVar significance (prefix "clinvar: ")
  "InterVar: InterVar and Evidence"   InterVar ACMG verdict
  CADD_phred                          CADD damage score
  Freq_gnomAD_genome_ALL              gnomAD allele frequency
  Freq_esp6500siv2_all                ESP6500 allele frequency
  Freq_1000g2015aug_all               1000G allele frequency
  PVS1                                ACMG PVS1 flag (YES/NO)
  BA1                                 ACMG BA1 stand-alone-benign flag (YES/NO)

Buckets (in surfacing priority order):
  1  STRONG_PATHOGENIC      Both ClinVar and InterVar agree pathogenic
  2  CLINVAR_PATHOGENIC     ClinVar pathogenic only (human-curated)
  3  INTERVAR_PATHOGENIC    InterVar pathogenic only (algorithmic)
  4  CONFLICTING_PATHOGENIC Conflicting ClinVar + InterVar pathogenic
  5  VUS_HIGH_SCORE         VUS with CADD>20 and rare
  99 DROP                   Everything else (benign/common/noise)
"""
from __future__ import annotations

from enum import Enum
from typing import Any

# ── Thresholds ────────────────────────────────────────────────────────────────
_FREQ_DROP_THRESHOLD = 0.05   # BA1 frequency — benign + >5% → DROP
_VUS_CADD_THRESHOLD  = 20.0   # minimum CADD for VUS_HIGH_SCORE
_VUS_RARE_THRESHOLD  = 0.01   # maximum gnomAD for VUS_HIGH_SCORE ("rare")


# ── Bucket definition ─────────────────────────────────────────────────────────

class Bucket(str, Enum):
    """Named pathogenicity buckets — per interval_02 spec §3."""
    STRONG_PATHOGENIC      = "STRONG_PATHOGENIC"
    CLINVAR_PATHOGENIC     = "CLINVAR_PATHOGENIC"
    INTERVAR_PATHOGENIC    = "INTERVAR_PATHOGENIC"
    CONFLICTING_PATHOGENIC = "CONFLICTING_PATHOGENIC"
    VUS_HIGH_SCORE         = "VUS_HIGH_SCORE"
    DROP                   = "DROP"


BUCKET_PRIORITY: dict[Bucket, int] = {
    Bucket.STRONG_PATHOGENIC:      1,
    Bucket.CLINVAR_PATHOGENIC:     2,
    Bucket.INTERVAR_PATHOGENIC:    3,
    Bucket.CONFLICTING_PATHOGENIC: 4,
    Bucket.VUS_HIGH_SCORE:         5,
    Bucket.DROP:                   99,
}

BUCKET_LABEL: dict[Bucket, str] = {
    Bucket.STRONG_PATHOGENIC:
        "Strong evidence (both ClinVar and InterVar agree pathogenic)",
    Bucket.CLINVAR_PATHOGENIC:
        "ClinVar-supported pathogenic (human-curated)",
    Bucket.INTERVAR_PATHOGENIC:
        "Algorithm-predicted pathogenic (InterVar/ACMG)",
    Bucket.CONFLICTING_PATHOGENIC:
        "Conflicting ClinVar — InterVar leans pathogenic (interpret with caution)",
    Bucket.VUS_HIGH_SCORE:
        "Uncertain significance, but high computational pathogenicity scores",
    Bucket.DROP:
        "Benign / common variant (not currently classified as disease-causing)",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    """Best-effort float coercion; '.' / empty → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in (".", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _max_freq(row: dict[str, Any]) -> float:
    """Highest allele-frequency from any frequency column."""
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
    """Decode ClinVar significance → (pathogenic, likely_pathogenic, conflicting, benign).

    Our DB stores ClinVar values with a 'clinvar: ' prefix and trailing space, e.g.:
      'clinvar: Pathogenic '
      'clinvar: Likely_pathogenic '
      'clinvar: Conflicting_interpretations_of_pathogenicity '
      'clinvar: Benign '
    """
    raw = (row.get("clinvar: Clinvar") or "").lower().strip()
    # Strip the 'clinvar: ' prefix for simpler matching
    cv = raw.removeprefix("clinvar: ").strip()

    conflicting = "conflicting" in cv
    path = (
        cv.startswith("pathogenic")
        and "likely" not in cv
        and not conflicting
    )
    likely_path = (
        ("likely_pathogenic" in cv or "likely pathogenic" in cv)
        and not conflicting
    )
    benign = ("benign" in cv) and ("pathogenic" not in cv)
    return path, likely_path, conflicting, benign


def _intervar_status(row: dict[str, Any]) -> tuple[bool, bool]:
    """Decode InterVar verdict → (pathogenic, likely_pathogenic).

    Our DB stores InterVar values with an 'InterVar: ' prefix, e.g.:
      'InterVar: Pathogenic PVS1=1, PS1=0, ...'
      'InterVar: Likely pathogenic PVS1=0, PS1=1, ...'
    PVS1 column ('YES'/'NO') is also checked as a standalone strong signal.
    """
    raw = (row.get("InterVar: InterVar and Evidence") or "").lower().strip()
    iv = raw.removeprefix("intervar: ").strip()

    pvs1 = (row.get("PVS1") or "").upper().strip() == "YES"
    iv_path   = iv.startswith("pathogenic") and "likely" not in iv
    iv_likely = "likely pathogenic" in iv
    return (iv_path or pvs1, iv_likely)


# ── Main bucketing function ───────────────────────────────────────────────────

def pathogenicity_bucket(row: dict[str, Any]) -> Bucket:
    """Assign one of 6 buckets to a variant row.

    Follows spec §3:
    1. BA1=YES or Benign + freq>5% → DROP
    2. Both agree pathogenic → STRONG_PATHOGENIC
    3. ClinVar pathogenic only (no conflicting) → CLINVAR_PATHOGENIC
    4. Conflicting ClinVar but InterVar pathogenic → CONFLICTING_PATHOGENIC
    5. InterVar pathogenic only → INTERVAR_PATHOGENIC
    6. VUS + CADD>20 + rare → VUS_HIGH_SCORE
    7. Everything else → DROP
    """
    cv_path, cv_likely, cv_conflicting, cv_benign = _clinvar_status(row)
    iv_path, iv_likely = _intervar_status(row)
    max_freq = _max_freq(row)

    # BA1 stand-alone benign flag
    if (row.get("BA1") or "").upper().strip() == "YES":
        return Bucket.DROP

    # Benign + common frequency → DROP
    if cv_benign and max_freq > _FREQ_DROP_THRESHOLD:
        return Bucket.DROP
    iv_str = (row.get("InterVar: InterVar and Evidence") or "").lower()
    iv_benign = "benign" in iv_str and "pathogenic" not in iv_str
    if iv_benign and max_freq > _FREQ_DROP_THRESHOLD:
        return Bucket.DROP

    clinvar_is_pathogenic   = cv_path or cv_likely
    intervar_is_pathogenic  = iv_path or iv_likely

    # Both agree → strong
    if clinvar_is_pathogenic and intervar_is_pathogenic:
        return Bucket.STRONG_PATHOGENIC

    # ClinVar only (no conflict)
    if clinvar_is_pathogenic and not cv_conflicting:
        return Bucket.CLINVAR_PATHOGENIC

    # Conflicting ClinVar but InterVar pathogenic
    if cv_conflicting and intervar_is_pathogenic:
        return Bucket.CONFLICTING_PATHOGENIC

    # InterVar only
    if intervar_is_pathogenic:
        return Bucket.INTERVAR_PATHOGENIC

    # VUS + high scores + rare
    func = (row.get("Func.refGene") or "").lower()
    looks_coding = (not func) or func in ("exonic", "splicing", "exonic;splicing")
    cadd = _f(row.get("CADD_phred"))
    rare = max_freq < _VUS_RARE_THRESHOLD

    if (
        looks_coding
        and not cv_benign
        and not iv_benign
        and cadd is not None
        and cadd > _VUS_CADD_THRESHOLD
        and rare
    ):
        return Bucket.VUS_HIGH_SCORE

    return Bucket.DROP


def is_clinically_interesting(row: dict[str, Any]) -> bool:
    """True iff the bucket rule would surface this row (not DROP)."""
    return pathogenicity_bucket(row) is not Bucket.DROP


def sort_key(row: dict[str, Any]) -> tuple[int, float]:
    """Sort key: (bucket_priority, -cadd) — most significant first."""
    bucket = pathogenicity_bucket(row)
    cadd = _f(row.get("CADD_phred")) or 0.0
    return (BUCKET_PRIORITY[bucket], -cadd)


def rank_rows(rows: list[dict[str, Any]], max_results: int = 20) -> list[dict[str, Any]]:
    """Sort rows by pathogenicity bucket + CADD, cap at max_results.

    Attaches '_bucket' and '_bucket_label' fields to each row for LLM context.
    """
    annotated = []
    for row in rows:
        b = pathogenicity_bucket(row)
        row = dict(row)  # shallow copy so we don't mutate caller's data
        row["_bucket"] = b.value
        row["_bucket_label"] = BUCKET_LABEL[b]
        annotated.append(row)

    annotated.sort(key=lambda r: sort_key(r))
    return annotated[:max_results]
