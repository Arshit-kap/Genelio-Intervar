"""
Genomic Q&A Engine — FastAPI Entry Point
Combines all phases:
  Phase 3: Core variant search endpoints
  Phase 4: Text-to-SQL with Qwen3 (HuggingFace)
  Phase 5: ACMG/AMP 2015 interpretation engine
  Phase 6: External evidence (ClinVar, PubMed, ClinGen)

Run:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000
  Open: http://localhost:8000/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Phase 3: Core endpoints (existing) ────────────────────────────────────────
from app.api.core_endpoints import app as core_app

# ── Phase 4-6: New routers ────────────────────────────────────────────────────
from app.api.ai_endpoints       import router as ai_router
from app.api.acmg_endpoints     import router as acmg_router
from app.api.evidence_endpoints import router as evidence_router

# ── Build combined app ─────────────────────────────────────────────────────────
app = FastAPI(
    title="Genomic Q&A Engine",
    description="""
## Genomic Variant Interpretation System

Query millions of genomic variants from the InterVar dataset using:
- **Natural language** (Qwen3 AI text-to-SQL)
- **Direct filters** (gene, position, classification, frequency)
- **ACMG/AMP 2015** automated variant interpretation
- **External evidence** from ClinVar, PubMed, and ClinGen ERepo

### Quick Start
1. `GET /api/health` — check server status
2. `POST /api/ai/query` — ask a question in plain English
3. `POST /api/variants/search` — filter variants directly
4. `GET /api/acmg/interpret/{id}` — get ACMG classification
5. `GET /api/evidence/{id}` — fetch ClinVar + PubMed evidence

### Setup Qwen3 LLM
```
set HF_TOKEN=hf_your_token_here
```
Get a free token at: https://huggingface.co/settings/tokens
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers from existing core app (skip the old root "/" handler)
for route in core_app.routes:
    if hasattr(route, "path") and route.path == "/":
        continue
    app.routes.append(route)

# Register new phase routers
app.include_router(ai_router)
app.include_router(acmg_router)
app.include_router(evidence_router)


@app.get("/", tags=["Root"])
async def root():
    """API overview and quick-start guide."""
    return {
        "name":    "Genomic Q&A Engine",
        "version": "2.0.0",
        "docs":    "http://localhost:8000/docs",
        "phases": {
            "phase_3_core_api": {
                "GET  /api/health":                   "Server health check",
                "GET  /api/metadata/statistics":      "Variant database statistics",
                "GET  /api/metadata/columns":         "InterVar column definitions",
                "GET  /api/metadata/acmg-rules":      "All 28 ACMG 2015 criteria",
                "POST /api/variants/search":          "Filter variants (gene, chr, CADD, etc.)",
                "GET  /api/variants/{id}":            "Single variant full detail",
                "POST /api/import/status":            "Data ingestion history",
                "GET  /api/debug/query-logs":         "Query performance logs",
            },
            "phase_4_text_to_sql": {
                "POST /api/ai/chat":     "Conversational AI — general + database questions (main interface)",
                "POST /api/ai/query":    "Natural language → SQL (structured output)",
                "GET  /api/ai/status":   "LLM backend status",
                "GET  /api/ai/examples": "Example questions for chat",
                "GET  /api/ai/schema":   "DB schema injected into LLM",
            },
            "phase_5_acmg": {
                "GET  /api/acmg/interpret/{id}":      "ACMG/AMP 2015 interpretation",
                "GET  /api/acmg/interpret/key/{key}": "Interpret by variant key",
                "POST /api/acmg/batch-interpret":     "Batch ACMG (up to 50)",
                "GET  /api/acmg/criteria":            "All ACMG criteria definitions",
            },
            "phase_6_evidence": {
                "GET  /api/evidence/{id}":            "All external evidence",
                "GET  /api/evidence/clinvar/{id}":    "ClinVar records",
                "GET  /api/evidence/pubmed/{id}":     "PubMed literature",
                "GET  /api/evidence/clingen/{id}":    "ClinGen expert assertions",
                "GET  /api/evidence/search/pubmed":   "Free PubMed search",
            },
        },
        "setup_llm": {
            "step_1": "Get free HuggingFace token: https://huggingface.co/settings/tokens",
            "step_2": "Set environment variable: set HF_TOKEN=hf_...",
            "step_3": "Restart server — Qwen3-7B will be used automatically",
            "model":  "Qwen/Qwen3-7B (HuggingFace Inference API)",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
