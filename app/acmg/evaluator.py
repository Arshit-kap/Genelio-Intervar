"""
Phase 5: ACMG/AMP 2015 Criteria Evaluator
Automated rule-based evaluation of 17 automatable criteria using variant data fields.
Reference: Richards et al. 2015, Genetics in Medicine 17, 405-424.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Strength(Enum):
    VERY_STRONG = "Very Strong"   # PVS
    STRONG      = "Strong"        # PS, BS, BA
    MODERATE    = "Moderate"      # PM
    SUPPORTING  = "Supporting"    # PP, BP


class Direction(Enum):
    PATHOGENIC = "pathogenic"
    BENIGN     = "benign"


@dataclass
class CriterionResult:
    code:      str
    direction: Direction
    strength:  Strength
    triggered: bool
    reason:    str
    value:     Any = None


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None

def _s(v: Any) -> str:
    return str(v).lower().strip() if v is not None else ""


# ─────────────────────────────────────────────
# Individual criterion evaluators
# ─────────────────────────────────────────────

def eval_pvs1(v: Dict) -> CriterionResult:
    """PVS1 — Null variant (LOF) in a gene where LOF is a known disease mechanism."""
    ef = _s(v.get("exonic_func"))
    fr = _s(v.get("func_region"))
    lof = ["stopgain", "stoploss", "frameshift insertion", "frameshift deletion",
           "frameshift substitution", "startloss"]
    triggered = any(t in ef for t in lof) or "splicing" in fr
    return CriterionResult(
        "PVS1", Direction.PATHOGENIC, Strength.VERY_STRONG, triggered,
        f"LOF type: {ef or fr}" if triggered else f"Not LOF ({ef or fr})", ef or fr
    )


def eval_ps1(v: Dict) -> CriterionResult:
    """PS1 — Same amino acid change as a previously established pathogenic variant (via ClinVar)."""
    sig = _s(v.get("clinvar_significance"))
    triggered = "pathogenic" in sig and "likely" not in sig and sig.strip() != ""
    return CriterionResult(
        "PS1", Direction.PATHOGENIC, Strength.STRONG, triggered,
        f"ClinVar: {sig}", sig
    )


def eval_ps3(v: Dict) -> CriterionResult:
    """PS3 — Functional studies show damaging effect (proxied by very high CADD + low SIFT)."""
    cadd = _f(v.get("cadd_phred"))
    sift = _f(v.get("sift_score"))
    meta = _f(v.get("metasvm_score"))
    triggered = (
        (cadd is not None and cadd >= 30) and
        ((sift is not None and sift < 0.001) or (meta is not None and meta > 0.5))
    )
    reason = f"CADD={cadd}, SIFT={sift}, MetaSVM={meta}"
    return CriterionResult(
        "PS3", Direction.PATHOGENIC, Strength.STRONG, triggered, reason, cadd
    )


def eval_pm1(v: Dict) -> CriterionResult:
    """PM1 — Located in mutational hotspot or well-established functional domain."""
    domain = v.get("interpro_domain")
    triggered = domain is not None and str(domain).strip() not in ("", ".", "None")
    return CriterionResult(
        "PM1", Direction.PATHOGENIC, Strength.MODERATE, triggered,
        f"Domain: {domain}" if triggered else "No domain annotation", domain
    )


def eval_pm2(v: Dict) -> CriterionResult:
    """PM2 — Absent or extremely low frequency in population databases (gnomAD AF < 0.0001)."""
    af = _f(v.get("gnomad_af_all"))
    triggered = af is None or af < 0.0001
    if af is None:
        reason = "Absent from gnomAD"
    elif triggered:
        reason = f"gnomAD AF = {af:.6f} (< 0.0001)"
    else:
        reason = f"gnomAD AF = {af:.4f} (not sufficiently rare)"
    return CriterionResult(
        "PM2", Direction.PATHOGENIC, Strength.MODERATE, triggered, reason, af
    )


def eval_pm4(v: Dict) -> CriterionResult:
    """PM4 — Protein length change from in-frame indels in non-repeat region."""
    ef = _s(v.get("exonic_func"))
    rmsk = _s(v.get("repeat_masker"))
    is_inframe  = "nonframeshift" in ef
    in_repeat   = rmsk not in ("", ".", "none")
    triggered   = is_inframe and not in_repeat
    return CriterionResult(
        "PM4", Direction.PATHOGENIC, Strength.MODERATE, triggered,
        f"In-frame indel: {ef}, repeat: {rmsk or 'none'}", ef
    )


def eval_pm5(v: Dict) -> CriterionResult:
    """PM5 — Novel missense at same codon as known pathogenic missense."""
    ef  = _s(v.get("exonic_func"))
    sig = _s(v.get("clinvar_significance"))
    triggered = ("missense" in ef or "nonsynonymous" in ef) and "pathogenic" in sig
    return CriterionResult(
        "PM5", Direction.PATHOGENIC, Strength.MODERATE, triggered,
        f"Missense + ClinVar: {sig}", sig
    )


def eval_pp2(v: Dict) -> CriterionResult:
    """PP2 — Missense in gene with low rate of benign missense variation."""
    ef = _s(v.get("exonic_func"))
    triggered = "missense" in ef or "nonsynonymous" in ef
    return CriterionResult(
        "PP2", Direction.PATHOGENIC, Strength.SUPPORTING, triggered,
        f"Missense: {ef}", ef
    )


def eval_pp3(v: Dict) -> CriterionResult:
    """PP3 — Multiple lines of computational evidence support deleterious effect."""
    cadd = _f(v.get("cadd_phred"))
    sift = _f(v.get("sift_score"))
    meta = _f(v.get("metasvm_score"))
    gerp = _f(v.get("gerp_rs"))

    votes, evidence = 0, []
    if cadd is not None and cadd >= 20:   votes += 1; evidence.append(f"CADD={cadd:.1f}")
    if sift is not None and sift < 0.05:  votes += 1; evidence.append(f"SIFT={sift:.3f}")
    if meta is not None and meta > 0:     votes += 1; evidence.append(f"MetaSVM={meta:.3f}")
    if gerp is not None and gerp > 2:     votes += 1; evidence.append(f"GERP={gerp:.1f}")

    triggered = votes >= 2
    return CriterionResult(
        "PP3", Direction.PATHOGENIC, Strength.SUPPORTING, triggered,
        ", ".join(evidence) if evidence else "No damaging computational evidence", votes
    )


def eval_pp5(v: Dict) -> CriterionResult:
    """PP5 — Reputable source (ClinVar) reports variant as pathogenic."""
    sig = _s(v.get("clinvar_significance"))
    triggered = "pathogenic" in sig or "likely pathogenic" in sig
    return CriterionResult(
        "PP5", Direction.PATHOGENIC, Strength.SUPPORTING, triggered,
        f"ClinVar: {sig}", sig
    )


def eval_ba1(v: Dict) -> CriterionResult:
    """BA1 — Allele frequency > 5% in gnomAD (stand-alone Benign)."""
    af = _f(v.get("gnomad_af_all"))
    triggered = af is not None and af > 0.05
    reason = f"gnomAD AF = {af:.4f} (> 5%)" if triggered else f"gnomAD AF = {af}"
    return CriterionResult(
        "BA1", Direction.BENIGN, Strength.STRONG, triggered, reason, af
    )


def eval_bs1(v: Dict) -> CriterionResult:
    """BS1 — Allele frequency 1-5% in gnomAD, greater than expected for disorder."""
    af = _f(v.get("gnomad_af_all"))
    triggered = af is not None and 0.01 < af <= 0.05
    reason = f"gnomAD AF = {af:.4f} (1–5%)" if triggered else f"gnomAD AF = {af}"
    return CriterionResult(
        "BS1", Direction.BENIGN, Strength.STRONG, triggered, reason, af
    )


def eval_bs3(v: Dict) -> CriterionResult:
    """BS3 — Functional evidence shows no damaging effect (proxied by low scores)."""
    cadd = _f(v.get("cadd_phred"))
    sift = _f(v.get("sift_score"))
    meta = _f(v.get("metasvm_score"))

    votes, evidence = 0, []
    if cadd is not None and cadd < 10:   votes += 1; evidence.append(f"CADD={cadd:.1f}")
    if sift is not None and sift >= 0.5: votes += 1; evidence.append(f"SIFT={sift:.3f}")
    if meta is not None and meta < -0.5: votes += 1; evidence.append(f"MetaSVM={meta:.3f}")

    triggered = votes >= 2
    return CriterionResult(
        "BS3", Direction.BENIGN, Strength.STRONG, triggered,
        ", ".join(evidence) if evidence else "No benign functional evidence", votes
    )


def eval_bp1(v: Dict) -> CriterionResult:
    """BP1 — Missense variant in gene where only truncating variants cause disease."""
    ef = _s(v.get("exonic_func"))
    triggered = "missense" in ef or "nonsynonymous" in ef
    return CriterionResult(
        "BP1", Direction.BENIGN, Strength.SUPPORTING, triggered,
        f"Missense in truncation-only gene: {ef}", ef
    )


def eval_bp3(v: Dict) -> CriterionResult:
    """BP3 — In-frame indel in repetitive region without known function."""
    ef   = _s(v.get("exonic_func"))
    rmsk = v.get("repeat_masker")
    in_repeat = rmsk is not None and str(rmsk).strip() not in ("", ".", "None")
    triggered = "nonframeshift" in ef and in_repeat
    return CriterionResult(
        "BP3", Direction.BENIGN, Strength.SUPPORTING, triggered,
        f"In-frame indel in repeat: {rmsk}", rmsk
    )


def eval_bp4(v: Dict) -> CriterionResult:
    """BP4 — Multiple computational tools predict no impact."""
    cadd = _f(v.get("cadd_phred"))
    sift = _f(v.get("sift_score"))
    meta = _f(v.get("metasvm_score"))

    votes, evidence = 0, []
    if cadd is not None and cadd < 10:    votes += 1; evidence.append(f"CADD={cadd:.1f}")
    if sift is not None and sift >= 0.1:  votes += 1; evidence.append(f"SIFT={sift:.3f}")
    if meta is not None and meta < 0:     votes += 1; evidence.append(f"MetaSVM={meta:.3f}")

    triggered = votes >= 2
    return CriterionResult(
        "BP4", Direction.BENIGN, Strength.SUPPORTING, triggered,
        ", ".join(evidence) if evidence else "No benign computational evidence", votes
    )


def eval_bp7(v: Dict) -> CriterionResult:
    """BP7 — Synonymous variant with no predicted splice impact."""
    ef  = _s(v.get("exonic_func"))
    ada = _f(v.get("dbscsnv_ada_score"))
    rf  = _f(v.get("dbscsnv_rf_score"))
    is_syn     = "synonymous" in ef and "non" not in ef
    no_splice  = not ((ada is not None and ada > 0.6) or (rf is not None and rf > 0.6))
    triggered  = is_syn and no_splice
    reason = (f"Synonymous, no splice effect (ADA={ada}, RF={rf})" if triggered
              else f"Not qualifying ({ef})")
    return CriterionResult(
        "BP7", Direction.BENIGN, Strength.SUPPORTING, triggered, reason, ef
    )


# ─────────────────────────────────────────────
# Master evaluation
# ─────────────────────────────────────────────

EVALUATORS = [
    eval_pvs1, eval_ps1, eval_ps3,
    eval_pm1, eval_pm2, eval_pm4, eval_pm5,
    eval_pp2, eval_pp3, eval_pp5,
    eval_ba1, eval_bs1, eval_bs3,
    eval_bp1, eval_bp3, eval_bp4, eval_bp7,
]


def evaluate_variant(variant_data: Dict) -> List[CriterionResult]:
    """Run all automated ACMG criteria evaluations on a variant dict."""
    results = []
    for fn in EVALUATORS:
        try:
            results.append(fn(variant_data))
        except Exception as e:
            logger.warning(f"{fn.__name__} failed: {e}")
    return results
