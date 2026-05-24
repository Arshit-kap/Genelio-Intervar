"""
Phase 6: External Evidence API Endpoints
ClinVar, PubMed, and ClinGen ERepo integrations.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Variant, APICache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evidence", tags=["External Evidence"])


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{variant_id}", summary="All external evidence for a variant")
async def get_all_evidence(variant_id: int):
    """
    Aggregate evidence from ClinVar, PubMed and ClinGen ERepo for one variant.
    Results are cached in the api_cache table (TTL 24 h).
    """
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")

        from app.external.clinvar_client import search_clinvar_by_rsid
        from app.external.pubmed_client import search_variant_literature
        from app.external.clingen_client import get_clingen_assertions

        clinvar = await search_clinvar_by_rsid(variant.rsid) if variant.rsid else {"message": "No rsid"}
        pubmed  = await search_variant_literature(gene=variant.gene_symbol, rsid=variant.rsid)
        clingen = await get_clingen_assertions(rsid=variant.rsid) if variant.rsid else {"message": "No rsid"}

        return {
            "variant_id":   variant_id,
            "variant_key":  variant.variant_key,
            "gene_symbol":  variant.gene_symbol,
            "rsid":         variant.rsid,
            "clinvar":      clinvar,
            "pubmed":       pubmed,
            "clingen":      clingen,
        }
    finally:
        db.close()


@router.get("/clinvar/{variant_id}", summary="ClinVar records for a variant")
async def get_clinvar(variant_id: int):
    """Fetch ClinVar records from NCBI for a stored variant."""
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        if not variant.rsid:
            return {"variant_id": variant_id, "message": "No rsid — cannot query ClinVar",
                    "variant_key": variant.variant_key}

        from app.external.clinvar_client import search_clinvar_by_rsid
        result = await search_clinvar_by_rsid(variant.rsid)
        return {"variant_id": variant_id, "variant_key": variant.variant_key, **result}
    finally:
        db.close()


@router.get("/pubmed/{variant_id}", summary="PubMed literature for a variant/gene")
async def get_pubmed(variant_id: int, max_results: int = Query(5, ge=1, le=20)):
    """Fetch relevant PubMed articles for the variant's gene and rsID."""
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")

        from app.external.pubmed_client import search_variant_literature
        result = await search_variant_literature(
            gene=variant.gene_symbol, rsid=variant.rsid, max_results=max_results
        )
        return {"variant_id": variant_id, "variant_key": variant.variant_key,
                "gene_symbol": variant.gene_symbol, **result}
    finally:
        db.close()


@router.get("/clingen/{variant_id}", summary="ClinGen expert panel assertions")
async def get_clingen(variant_id: int):
    """
    Fetch ClinGen ERepo expert panel variant assertions.
    Uses 2-step: rsID → CAID → assertions.
    """
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        if not variant.rsid:
            return {"variant_id": variant_id, "message": "No rsid — cannot query ClinGen",
                    "variant_key": variant.variant_key}

        from app.external.clingen_client import get_clingen_assertions
        result = await get_clingen_assertions(rsid=variant.rsid)
        return {"variant_id": variant_id, "variant_key": variant.variant_key, **result}
    finally:
        db.close()


@router.get("/search/pubmed", summary="Free-text PubMed search")
async def pubmed_free_search(
    q: str = Query(..., description="Search query e.g. 'BRCA1 pathogenic variant'"),
    max_results: int = Query(5, ge=1, le=20)
):
    """Direct PubMed search — useful for gene or phenotype queries."""
    from app.external.pubmed_client import search_pubmed
    return await search_pubmed(q, max_results)
