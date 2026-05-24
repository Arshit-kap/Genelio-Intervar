"""
Phase 5: ACMG Interpretation API Endpoints
Automated ACMG/AMP 2015 variant classification.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Variant, VariantInterpretation, CriterionAssessment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/acmg", tags=["ACMG Interpretation"])


# ── Response models ────────────────────────────────────────────────────────────

class EvidenceItem(BaseModel):
    code:      str
    direction: str
    strength:  str
    triggered: bool
    reason:    str

class ACMGResult(BaseModel):
    variant_id:           int
    variant_key:          str
    gene_symbol:          Optional[str]
    intervar_classification: Optional[str]
    acmg_classification:  str
    explanation:          str
    triggered_criteria:   List[str]
    pathogenic_criteria:  List[str]
    benign_criteria:      List[str]
    pathogenic_count:     int
    benign_count:         int
    all_evidence:         List[EvidenceItem]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/interpret/{variant_id}", response_model=ACMGResult,
            summary="ACMG/AMP 2015 interpretation for a single variant")
async def interpret_variant(variant_id: int, save: bool = False):
    """
    Run automated ACMG/AMP 2015 criteria evaluation on a stored variant.

    Returns:
    - **acmg_classification**: Pathogenic | Likely pathogenic | Uncertain significance |
      Likely benign | Benign
    - **triggered_criteria**: ACMG evidence codes that fired (e.g. PVS1, PM2, PP3)
    - **all_evidence**: Full evidence breakdown for all 17 automated criteria

    Set **save=true** to persist the interpretation to the database.
    """
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")

        variant_dict = _variant_to_dict(variant)

        from app.acmg.evaluator import evaluate_variant
        from app.acmg.classifier import classify

        criteria = evaluate_variant(variant_dict)
        result   = classify(criteria)

        if save:
            _save_interpretation(db, variant, result, criteria)

        return ACMGResult(
            variant_id=variant.variant_id,
            variant_key=variant.variant_key,
            gene_symbol=variant.gene_symbol,
            intervar_classification=variant.intervar_classification,
            acmg_classification=result["classification"],
            explanation=result["explanation"],
            triggered_criteria=result["triggered_criteria"],
            pathogenic_criteria=result["pathogenic_criteria"],
            benign_criteria=result["benign_criteria"],
            pathogenic_count=result["pathogenic_count"],
            benign_count=result["benign_count"],
            all_evidence=[EvidenceItem(**e) for e in result["all_evidence"]],
        )
    finally:
        db.close()


@router.get("/interpret/key/{variant_key:path}", response_model=ACMGResult,
            summary="ACMG interpretation by variant key (CHR:POS:REF:ALT)")
async def interpret_by_key(variant_key: str):
    """Look up variant by key string and run ACMG interpretation."""
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_key == variant_key).first()
        if not variant:
            raise HTTPException(status_code=404,
                                detail=f"Variant key '{variant_key}' not found")
        return await interpret_variant.__wrapped__(variant.variant_id, save=False) \
            if hasattr(interpret_variant, "__wrapped__") \
            else await _interpret_core(db, variant)
    finally:
        db.close()

    # Redirect to the main function
    return await interpret_variant(variant.variant_id, save=False)


async def _interpret_core(db, variant) -> ACMGResult:
    from app.acmg.evaluator import evaluate_variant
    from app.acmg.classifier import classify
    vd       = _variant_to_dict(variant)
    criteria = evaluate_variant(vd)
    result   = classify(criteria)
    return ACMGResult(
        variant_id=variant.variant_id,
        variant_key=variant.variant_key,
        gene_symbol=variant.gene_symbol,
        intervar_classification=variant.intervar_classification,
        acmg_classification=result["classification"],
        explanation=result["explanation"],
        triggered_criteria=result["triggered_criteria"],
        pathogenic_criteria=result["pathogenic_criteria"],
        benign_criteria=result["benign_criteria"],
        pathogenic_count=result["pathogenic_count"],
        benign_count=result["benign_count"],
        all_evidence=[EvidenceItem(**e) for e in result["all_evidence"]],
    )


@router.post("/batch-interpret", summary="Batch ACMG interpretation for multiple variants")
async def batch_interpret(variant_ids: List[int]):
    """
    Batch ACMG interpretation for up to 50 variants at once.
    Returns a list of interpretations.
    """
    if len(variant_ids) > 50:
        raise HTTPException(status_code=400,
                            detail="Maximum 50 variants per batch request")
    db = SessionLocal()
    results = []
    try:
        from app.acmg.evaluator import evaluate_variant
        from app.acmg.classifier import classify
        for vid in variant_ids:
            variant = db.query(Variant).filter(Variant.variant_id == vid).first()
            if not variant:
                results.append({"variant_id": vid, "error": "Not found"})
                continue
            vd       = _variant_to_dict(variant)
            criteria = evaluate_variant(vd)
            res      = classify(criteria)
            results.append({
                "variant_id":          vid,
                "variant_key":         variant.variant_key,
                "gene_symbol":         variant.gene_symbol,
                "acmg_classification": res["classification"],
                "triggered_criteria":  res["triggered_criteria"],
            })
        return {"count": len(results), "results": results}
    finally:
        db.close()


@router.get("/criteria", summary="List all 28 ACMG/AMP criteria")
async def list_criteria():
    """Return all ACMG/AMP 2015 criteria with descriptions from the database."""
    db = SessionLocal()
    try:
        from app.models import ACMGRuleMap
        rules = db.query(ACMGRuleMap).order_by(ACMGRuleMap.criterion_code).all()
        return [
            {
                "code":               r.criterion_code,
                "category":           r.criterion_category,
                "evidence_strength":  r.evidence_strength,
                "direction":          "pathogenic" if r.pathogenic_direction else "benign",
                "description":        r.description,
                "trigger_logic":      r.threshold_logic,
                "sql_columns":        r.sql_columns,
            }
            for r in rules
        ]
    finally:
        db.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _variant_to_dict(v: Variant) -> Dict[str, Any]:
    return {
        "exonic_func":          v.exonic_func,
        "func_region":          v.func_region,
        "clinvar_significance": v.clinvar_significance,
        "gnomad_af_all":        float(v.gnomad_af_all) if v.gnomad_af_all else None,
        "cadd_phred":           v.cadd_phred,
        "sift_score":           v.sift_score,
        "metasvm_score":        v.metasvm_score,
        "dbscsnv_ada_score":    v.dbscsnv_ada_score,
        "dbscsnv_rf_score":     v.dbscsnv_rf_score,
        "gerp_rs":              v.gerp_rs,
        "interpro_domain":      v.interpro_domain,
        "repeat_masker":        v.repeat_masker,
    }


def _save_interpretation(db, variant: Variant, result: Dict, criteria) -> None:
    try:
        interp = VariantInterpretation(
            variant_id=variant.variant_id,
            source="acmg_automated",
            classification=result["classification"],
            assertion_method="ACMG/AMP 2015 automated",
            produced_at_utc=datetime.utcnow(),
            version_note=result["explanation"],
        )
        db.add(interp)
        db.flush()
        for r in criteria:
            if r.triggered:
                assess = CriterionAssessment(
                    interpretation_id=interp.interpretation_id,
                    criterion_code=r.code,
                    evidence_strength=r.strength.value,
                    description=r.reason,
                    is_met=True,
                )
                db.add(assess)
        db.commit()
    except Exception as e:
        logger.warning(f"Could not save interpretation: {e}")
        db.rollback()
