"""
PHASE 3: Core API Endpoints
FastAPI server for direct SQL queries and variant search
"""

from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import logging
import time
from datetime import datetime
from app.database import SessionLocal
from app.models import (
    Variant, Gene, Condition, ColumnDictionary, ACMGRuleMap,
    QueryLog, ImportLog, VariantInterpretation
)
from app.ingestion.loader_extended import InterVarLoader
import json

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Genomic Q&A Engine",
    description="Text-to-SQL genomic variant interpretation system",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class VariantFilter(BaseModel):
    """Variant search filters"""
    chromosome: Optional[str] = None
    start_pos_min: Optional[int] = None
    start_pos_max: Optional[int] = None
    gene_symbol: Optional[str] = None
    rsid: Optional[str] = None
    exonic_func: Optional[str] = None
    clinvar_significance: Optional[str] = None
    intervar_classification: Optional[str] = None
    cadd_phred_min: Optional[float] = None
    cadd_phred_max: Optional[float] = None
    gnomad_af_max: Optional[float] = None
    limit: int = 100

class VariantResponse(BaseModel):
    """Variant with minimal fields"""
    variant_id: int
    variant_key: str
    chromosome: str
    start_pos: int
    ref_allele: str
    alt_allele: str
    gene_symbol: Optional[str]
    rsid: Optional[str]
    cadd_phred: Optional[float]
    clinvar_significance: Optional[str]
    intervar_classification: Optional[str]
    gnomad_af_all: Optional[str]

class ColumnDictResponse(BaseModel):
    """Column dictionary entry"""
    column_name: str
    column_type: str
    description: str
    data_domain: str
    acmg_rule_tags: Optional[str]
    example_values: Optional[str]

class ACMGRuleResponse(BaseModel):
    """ACMG rule map entry"""
    criterion_code: str
    criterion_category: str
    evidence_strength: str
    description: str
    trigger_columns: Optional[str]
    pathogenic_direction: bool

class QueryLogEntry(BaseModel):
    """Query log entry for tracking"""
    query_id: Optional[int] = None
    query_type: str
    query_text: str
    result_count: int
    execution_time_ms: float
    executed_at: datetime

class VariantSearchResponse(BaseModel):
    """Response from variant search"""
    total_count: int
    results: List[VariantResponse]
    filters_applied: Dict[str, Any]
    execution_time_ms: float

# ============================================================================
# HEALTH & METADATA ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/api/metadata/columns", response_model=List[ColumnDictResponse])
async def get_columns():
    """
    Get all column definitions
    Returns complete column dictionary
    """
    db = SessionLocal()
    try:
        columns = db.query(ColumnDictionary).all()
        return [
            ColumnDictResponse(
                column_name=col.column_name,
                column_type=col.column_type,
                description=col.description,
                data_domain=col.data_domain,
                acmg_rule_tags=col.acmg_rule_tags,
                example_values=col.example_values
            )
            for col in columns
        ]
    finally:
        db.close()

@app.get("/api/metadata/acmg-rules", response_model=List[ACMGRuleResponse])
async def get_acmg_rules():
    """
    Get all ACMG evidence codes
    Returns 28 ACMG 2015 criteria
    """
    db = SessionLocal()
    try:
        rules = db.query(ACMGRuleMap).all()
        return [
            ACMGRuleResponse(
                criterion_code=rule.criterion_code,
                criterion_category=rule.criterion_category,
                evidence_strength=rule.evidence_strength,
                description=rule.description,
                trigger_columns=rule.sql_columns,
                pathogenic_direction=rule.pathogenic_direction
            )
            for rule in rules
        ]
    finally:
        db.close()

@app.get("/api/metadata/statistics")
async def get_statistics():
    """Get database statistics."""
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_fetch_statistics)


def _fetch_statistics() -> Dict[str, Any]:
    from sqlalchemy import text as sql_text
    db = SessionLocal()
    result: Dict[str, Any] = {"timestamp": datetime.now().isoformat()}
    try:
        # MAX(rowid) is O(log n) via B-tree, much faster than COUNT(*)
        result["variant_count"]   = db.execute(sql_text("SELECT MAX(variant_id) FROM variants")).scalar() or 0
        result["gene_count"]      = db.execute(sql_text("SELECT MAX(gene_id) FROM genes")).scalar() or 0
        result["condition_count"] = db.execute(sql_text("SELECT MAX(condition_id) FROM conditions")).scalar() or 0
        result["classification_counts_url"] = "/api/metadata/classification-counts"
    except Exception as e:
        result["db_note"] = f"DB error: {str(e)[:120]}"
    finally:
        db.close()
    return result

@app.get("/api/metadata/classification-counts")
async def get_classification_counts():
    """
    Count variants per ACMG classification tier.
    Runs LIKE queries on 4.8M rows — may take 30-60s without the intervar_class index.
    Create the index first with: CREATE INDEX idx_variants_intervar_class ON variants(intervar_classification)
    """
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_fetch_classification_counts)


def _fetch_classification_counts() -> Dict[str, Any]:
    from sqlalchemy import text as sql_text
    db = SessionLocal()
    result: Dict[str, Any] = {"timestamp": datetime.now().isoformat()}
    try:
        for prefix, label in [
            ("InterVar: Pathogenic ",           "pathogenic"),
            ("InterVar: Likely pathogenic",     "likely_pathogenic"),
            ("InterVar: Benign ",               "benign"),
            ("InterVar: Likely benign",         "likely_benign"),
            ("InterVar: Uncertain significance","vus"),
        ]:
            result[label] = db.execute(
                sql_text("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE :p"),
                {"p": f"{prefix}%"}
            ).scalar()
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        db.close()
    return result


# ============================================================================
# VARIANT SEARCH ENDPOINTS
# ============================================================================

@app.post("/api/variants/search", response_model=VariantSearchResponse)
async def search_variants(filters: VariantFilter):
    """
    Search variants with flexible filters.

    Example: {"gene_symbol": "BRCA1", "cadd_phred_min": 20, "limit": 100}
    """
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_do_variant_search, filters)


def _do_variant_search(filters: "VariantFilter") -> "VariantSearchResponse":
    from sqlalchemy import text as sql_text
    db = SessionLocal()
    start_time = time.time()
    try:
        query = db.query(Variant)

        if filters.chromosome:
            query = query.filter(Variant.chromosome == filters.chromosome)
        if filters.start_pos_min:
            query = query.filter(Variant.start_pos >= filters.start_pos_min)
        if filters.start_pos_max:
            query = query.filter(Variant.start_pos <= filters.start_pos_max)
        if filters.gene_symbol:
            # Exact match first (index-friendly), then fallback to prefix scan
            gene = filters.gene_symbol.upper()
            query = query.filter(Variant.gene_symbol == gene)
        if filters.rsid:
            query = query.filter(Variant.rsid == filters.rsid)
        if filters.exonic_func:
            query = query.filter(Variant.exonic_func == filters.exonic_func)
        if filters.clinvar_significance:
            query = query.filter(
                Variant.clinvar_significance.ilike(f"{filters.clinvar_significance}%")
            )
        if filters.intervar_classification:
            query = query.filter(
                Variant.intervar_classification.ilike(f"InterVar: {filters.intervar_classification}%")
            )
        if filters.cadd_phred_min is not None:
            query = query.filter(Variant.cadd_phred >= filters.cadd_phred_min)
        if filters.cadd_phred_max is not None:
            query = query.filter(Variant.cadd_phred <= filters.cadd_phred_max)
        if filters.gnomad_af_max is not None:
            query = query.filter(
                (Variant.gnomad_af_all.is_(None)) | (Variant.gnomad_af_all <= filters.gnomad_af_max)
            )

        results = query.limit(filters.limit).all()
        # Approximate count: only do a full count if result set is small
        total_count = len(results) if len(results) < filters.limit else -1

        variant_responses = [
            VariantResponse(
                variant_id=v.variant_id,
                variant_key=v.variant_key,
                chromosome=v.chromosome,
                start_pos=v.start_pos,
                ref_allele=v.ref_allele,
                alt_allele=v.alt_allele,
                gene_symbol=v.gene_symbol,
                rsid=v.rsid,
                cadd_phred=v.cadd_phred,
                clinvar_significance=v.clinvar_significance,
                intervar_classification=v.intervar_classification,
                gnomad_af_all=str(v.gnomad_af_all) if v.gnomad_af_all else None,
            )
            for v in results
        ]

        elapsed_ms = (time.time() - start_time) * 1000
        try:
            db.add(QueryLog(
                query_type="variant_search",
                query_text=str(filters.dict()),
                result_count=len(variant_responses),
                execution_time_ms=elapsed_ms,
                executed_at=datetime.now(),
            ))
            db.commit()
        except Exception:
            pass

        return VariantSearchResponse(
            total_count=total_count,
            results=variant_responses,
            filters_applied=filters.dict(),
            execution_time_ms=elapsed_ms,
        )
    finally:
        db.close()

@app.get("/api/variants/{variant_id}")
async def get_variant(variant_id: int):
    """Get single variant with all fields"""
    db = SessionLocal()
    try:
        variant = db.query(Variant).filter(Variant.variant_id == variant_id).first()
        
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        
        return {
            "variant_id": variant.variant_id,
            "variant_key": variant.variant_key,
            "chromosome": variant.chromosome,
            "start_pos": variant.start_pos,
            "end_pos": variant.end_pos,
            "ref_allele": variant.ref_allele,
            "alt_allele": variant.alt_allele,
            "rsid": variant.rsid,
            "gene_symbol": variant.gene_symbol,
            "clinvar_significance": variant.clinvar_significance,
            "intervar_classification": variant.intervar_classification,
            "cadd_phred": variant.cadd_phred,
            "sift_score": variant.sift_score,
            "metasvm_score": variant.metasvm_score,
            "gnomad_af_all": float(variant.gnomad_af_all) if variant.gnomad_af_all else None,
            "gerp_rs": variant.gerp_rs,
            "phylop46way_placental": variant.phylop46way_placental,
            "raw_data": variant.raw_row_json if variant.raw_row_json else {}
        }
    finally:
        db.close()

# ============================================================================
# IMPORT ENDPOINTS
# ============================================================================

@app.post("/api/import/status")
async def get_import_status():
    """Get recent import logs"""
    db = SessionLocal()
    try:
        imports = db.query(ImportLog).order_by(
            ImportLog.import_start.desc()
        ).limit(10).all()
        
        return [
            {
                "log_id": imp.log_id,
                "source_file": imp.source_file,
                "total_rows": imp.total_rows,
                "inserted_rows": imp.inserted_rows,
                "error_rows": imp.error_rows,
                "import_status": imp.import_status,
                "import_start": imp.import_start.isoformat() if imp.import_start else None,
                "import_end": imp.import_end.isoformat() if imp.import_end else None
            }
            for imp in imports
        ]
    finally:
        db.close()

# ============================================================================
# DEBUG ENDPOINTS
# ============================================================================

@app.get("/api/debug/query-logs")
async def get_query_logs(limit: int = Query(50, ge=1, le=1000)):
    """Get recent query logs"""
    db = SessionLocal()
    try:
        logs = db.query(QueryLog).order_by(
            QueryLog.executed_at.desc()
        ).limit(limit).all()
        
        return [
            {
                "query_id": log.query_id,
                "query_type": log.query_type,
                "result_count": log.result_count,
                "execution_time_ms": log.execution_time_ms,
                "executed_at": log.executed_at.isoformat() if log.executed_at else None
            }
            for log in logs
        ]
    finally:
        db.close()

# ============================================================================
# ROOT
# ============================================================================

@app.get("/")
async def root():
    """API documentation"""
    return {
        "name": "Genomic Q&A Engine",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "health": "GET /api/health",
            "columns": "GET /api/metadata/columns",
            "acmg_rules": "GET /api/metadata/acmg-rules",
            "statistics": "GET /api/metadata/statistics",
            "search": "POST /api/variants/search",
            "variant": "GET /api/variants/{id}",
            "import_status": "GET /api/import/status"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
