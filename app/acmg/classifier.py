"""
Phase 5: ACMG/AMP 2015 Variant Classifier
Combines triggered criteria into a 5-tier classification.
Reference: Richards et al. 2015, Genetics in Medicine, Table 5.
"""
from typing import Any, Dict, List
from app.acmg.evaluator import CriterionResult, Direction, Strength


def classify(criteria: List[CriterionResult]) -> Dict[str, Any]:
    """
    Apply ACMG 2015 combining rules and return a classification result dict.
    """
    path   = [r for r in criteria if r.triggered and r.direction == Direction.PATHOGENIC]
    benign = [r for r in criteria if r.triggered and r.direction == Direction.BENIGN]

    pvs = sum(1 for r in path   if r.strength == Strength.VERY_STRONG)
    ps  = sum(1 for r in path   if r.strength == Strength.STRONG)
    pm  = sum(1 for r in path   if r.strength == Strength.MODERATE)
    pp  = sum(1 for r in path   if r.strength == Strength.SUPPORTING)

    ba1 = any(r.code == "BA1"   for r in benign)
    bs  = sum(1 for r in benign if r.strength == Strength.STRONG and r.code != "BA1")
    bp  = sum(1 for r in benign if r.strength == Strength.SUPPORTING)

    # ── BA1 stand-alone (overrides everything per ACMG 2015) ────────────────
    if ba1:
        return _result("Benign", "BA1 stand-alone: gnomAD AF > 5%", path, benign, criteria)

    # ── Conflict check (must run before all other benign rules) ─────────────
    # Any pathogenic evidence + any benign evidence (excluding BA1) = VUS
    non_ba1_benign = [r for r in benign if r.code != "BA1"]
    if path and non_ba1_benign:
        return _result(
            "Uncertain significance",
            f"Conflicting evidence: {len(path)} pathogenic indicator(s), {len(non_ba1_benign)} benign indicator(s)",
            path, benign, criteria
        )

    # ── Pathogenic rules ─────────────────────────────────────────────────────
    if pvs >= 1:
        if ps >= 1:
            return _result("Pathogenic", "PVS1 + 1+ Strong pathogenic", path, benign, criteria)
        if pm >= 2:
            return _result("Pathogenic", "PVS1 + 2+ Moderate pathogenic", path, benign, criteria)
        if pm >= 1 and pp >= 1:
            return _result("Pathogenic", "PVS1 + 1 PM + 1 PP", path, benign, criteria)
        if pp >= 2:
            return _result("Pathogenic", "PVS1 + 2+ Supporting pathogenic", path, benign, criteria)

    if ps >= 2:
        return _result("Pathogenic", f"2+ Strong pathogenic (PS={ps})", path, benign, criteria)

    if ps >= 1 and pm >= 3:
        return _result("Pathogenic", "1 PS + 3+ PM", path, benign, criteria)
    if ps >= 1 and pm >= 2 and pp >= 2:
        return _result("Pathogenic", "1 PS + 2 PM + 2+ PP", path, benign, criteria)
    if ps >= 1 and pm >= 1 and pp >= 4:
        return _result("Pathogenic", "1 PS + 1 PM + 4+ PP", path, benign, criteria)

    # ── Likely Pathogenic rules ───────────────────────────────────────────────
    if pvs >= 1 and pm >= 1:
        return _result("Likely pathogenic", "PVS1 + 1 PM", path, benign, criteria)
    if ps >= 1 and pm >= 1:
        return _result("Likely pathogenic", f"1 PS + Moderate support (PM={pm})", path, benign, criteria)
    if ps >= 1 and pp >= 2:
        return _result("Likely pathogenic", "1 PS + 2+ Supporting pathogenic", path, benign, criteria)
    if pm >= 3:
        return _result("Likely pathogenic", f"3+ PM ({pm})", path, benign, criteria)
    if pm >= 2 and pp >= 2:
        return _result("Likely pathogenic", "2 PM + 2+ PP", path, benign, criteria)
    if pm >= 1 and pp >= 4:
        return _result("Likely pathogenic", "1 PM + 4+ PP", path, benign, criteria)

    # ── Benign rules (only reached when no pathogenic evidence present) ───────
    if bs >= 2:
        return _result("Benign", f">=2 Strong benign criteria (BS={bs})", path, benign, criteria)

    if bs >= 1 and bp >= 1:
        return _result("Likely benign", "1 Strong + 1 Supporting benign", path, benign, criteria)

    if bp >= 2:
        return _result("Likely benign", f">=2 Supporting benign (BP={bp})", path, benign, criteria)

    return _result(
        "Uncertain significance",
        "Insufficient evidence for definitive classification",
        path, benign, criteria
    )


def _result(
    classification: str,
    explanation: str,
    path: List[CriterionResult],
    benign: List[CriterionResult],
    all_criteria: List[CriterionResult],
) -> Dict[str, Any]:
    return {
        "classification":      classification,
        "explanation":         explanation,
        "triggered_criteria":  [r.code for r in all_criteria if r.triggered],
        "pathogenic_criteria": [r.code for r in path],
        "benign_criteria":     [r.code for r in benign],
        "pathogenic_count":    len(path),
        "benign_count":        len(benign),
        "all_evidence": [
            {
                "code":      r.code,
                "direction": r.direction.value,
                "strength":  r.strength.value,
                "triggered": r.triggered,
                "reason":    r.reason,
            }
            for r in all_criteria
        ],
    }
