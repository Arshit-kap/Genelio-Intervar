# Genelio InterVar — Full Stack (MedGemma-27B + Genelio Frontend)

> **Complete production genomic variant Q&A system: FastAPI backend + Genelio Next.js frontend**

This branch (`genelio_fullstack`) is the production-ready deployment with all fixes:
- **Backend**: FastAPI with MedGemma-27B (port 8000) or Qwen3-32B (port 8001)
- **Frontend**: Genelio Next.js chat UI connected via `proxy.ts`
- **Database**: 76,330 InterVar-annotated WGS variants (SQLite)

---

## Architecture

```
Browser → Next.js (port 3000, ngrok) → proxy.ts → FastAPI (port 8000)
                                                       ↓
                                          8-Intent Router (LLM)
                                                       ↓
                                  HPO Resolver ← OMIM / HPO ontology
                                                       ↓
                                  InterVar SQLite DB (76,330 variants)
                                                       ↓
                             External APIs: ClinVar · PubMed · ClinGen
                                                       ↓
                                MedGemma-27B Answer Generator
```

---

## Quick Start

### Backend
```bash
pip install -r requirements.txt
cp .env.example .env          # set VLLM_API_URL + VLLM_MODEL
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Genelio Frontend integration
```bash
# Copy proxy.ts to your Genelio frontend root directory
cp proxy.ts /path/to/genelio_frontend/
echo "NEXT_PUBLIC_API_URL=" > /path/to/genelio_frontend/.env.local
cd /path/to/genelio_frontend && npm run build && npm start
```

### Public access via ngrok
```bash
ngrok config add-authtoken YOUR_TOKEN
ngrok http 3000    # exposes frontend publicly
```

---

## 8-Intent Router

| Intent | Trigger | Example |
|--------|---------|---------|
| `acmg_clinvar` | pathogenic / VUS / ClinVar | "Do I have pathogenic variants?" |
| `biofilter` | gene / variant type / threshold | "Show missense variants in BRCA1" |
| `disease_link` | disease name or organ system | "Are there lung-related genes?" |
| `hpo_symptom` | "I have [symptom]" | "I have muscle weakness and fatigue" |
| `coord_lookup` | chromosome position / rsID | "Look up rs144080386" |
| `aggregate` | count / average / top-N | "Top 5 genes by variant count" |
| `schema_lookup` | terminology explanation | "What is VUS?" |
| `domain_fallback` | general genetics knowledge | "How does autosomal dominant work?" |

---

## Key Fixes in This Branch

### HPO symptom specificity
Genes are now queried per-symptom HPO term — neurological symptoms no longer
incorrectly return SERPINA1/IDUA (lung/lysosomal genes). Each gene result is
tagged with the specific HPO term that linked it to the symptom.

### Organ-system search
"Eye-related", "lung-related", "ear-related" use expanded Orpha/OMIM terms:
- `eye` → ophthalm, retin, ocul, catar, glaucom, macular, cornea
- `lung` → pulmon, emphysema, bronch, surfact, airway
- `ear` → cochle, auditor, deaf, usher
- `heart` → cardiac, cardiomyop, arrhythm, aortic

### MedGemma output format
Strips the `[{'text': '...', 'type': 'text'}]` structured output that MedGemma
sometimes generates, giving clean text to the user.

### Inheritance questions
"Is there risk of passing this to my offspring?" and similar inheritance
education questions are now answered with proper Mendelian risk explanations
instead of being refused.

---

## Production Files

```
├── main.py                    # FastAPI entry point
├── requirements.txt           # Python dependencies
├── proxy.ts                   # Next.js proxy → copy to frontend root
├── .env.example               # Environment template
├── start_port8000.sh          # Start MedGemma backend (port 8000)
├── start_port8001.sh          # Start Qwen3 backend (port 8001)
├── start_all.sh               # Start all services + autossh tunnel
├── tunnel_medgemma.sh         # autossh tunnel to MedGemma GPU server
├── app/
│   ├── ai/
│   │   ├── intervar_router.py # 8-intent router, HPO per-term, organ search, KB
│   │   ├── llm_config.py      # LLM backends + MedGemma output stripping
│   │   ├── text_to_sql.py     # Pattern SQL + LLM fallback
│   │   ├── gene_enricher.py   # Gene-disease annotation
│   │   ├── pathogenicity.py   # Variant ranking
│   │   └── validator.py       # HGVS + safety
│   ├── api/
│   │   ├── ai_endpoints.py    # /api/ai/* — 8-layer chat pipeline
│   │   ├── compat_endpoints.py# Genelio frontend compatibility layer
│   │   ├── core_endpoints.py  # /api/variants/*, /api/metadata/*
│   │   ├── acmg_endpoints.py  # /api/acmg/* ACMG/AMP 2015
│   │   └── evidence_endpoints.py # /api/evidence/* ClinVar+PubMed+ClinGen
│   ├── hpo/
│   │   ├── resolver.py        # HPO term resolution + per-term gene sets
│   │   └── api_client.py      # HPO API client
│   ├── acmg/                  # ACMG classification
│   ├── external/              # ClinVar, PubMed, ClinGen clients
│   ├── database.py
│   └── config.py
├── test_categories.py         # 24-question A1-F2 category suite
├── test_spot.py
└── test_12q.py
```

---

## Models

| Backend | Port | Model |
|---------|------|-------|
| MedGemma-27B | 8000 | Google MedGemma via vLLM (GPU server via autossh tunnel) |
| Qwen3-32B | 8001 | Alibaba Qwen3 via Ollama (local, no cloud dependency) |

The Genelio frontend connects to port 8000 (MedGemma) by default.

---

## Test Results

**97% pass rate across 75 test questions** covering:
- DB queries (pathogenic, gene lookup, coordinates, aggregates)
- Organ/disease queries (eye, lung, ear, heart, Marfan, MPS)
- Symptom/HPO (muscle weakness, wheezing, neurological)
- Model knowledge (VUS, CADD, PVS1, BA1, HGVS)
- Safety filters (diagnosis refusal, treatment refusal, prognosis)
- Edge cases (fake gene, reproductive decisions, offspring risk)

---

> **Educational and research use only.** Not medical advice.
> Always consult a certified genetic counselor or physician.
