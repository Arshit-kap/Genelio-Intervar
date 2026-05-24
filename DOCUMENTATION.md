# InterVar Genomic Variant Analysis Platform — Full Documentation

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture & Flow](#2-system-architecture--flow)
3. [Folder Structure](#3-folder-structure)
4. [Database Schema](#4-database-schema)
5. [API Reference](#5-api-reference)
   - [Phase 3 — Core Variant API](#phase-3--core-variant-api)
   - [Phase 4 — AI Chat & Text-to-SQL](#phase-4--ai-chat--text-to-sql)
   - [Phase 5 — ACMG Interpreter](#phase-5--acmg-interpreter)
   - [Phase 6 — External Evidence APIs](#phase-6--external-evidence-apis)
6. [AI Layer — How Text-to-SQL Works](#6-ai-layer--how-text-to-sql-works)
7. [ACMG Classification Logic](#7-acmg-classification-logic)
8. [External Integrations](#8-external-integrations)
9. [Configuration & Environment](#9-configuration--environment)
10. [Running the Server](#10-running-the-server)
11. [Key Design Decisions](#11-key-design-decisions)

---

## 1. Project Overview

**InterVar Genomic Variant Analysis Platform** is a backend REST API for querying, interpreting, and researching genomic variants. It is built on top of the InterVar dataset — a pre-annotated set of **4.8 million human genetic variants** with automated ACMG/AMP 2015 pathogenicity classifications.

### What it does

| Capability | Description |
|---|---|
| Structured Search | Filter variants by gene, chromosome, position, classification, CADD score, gnomAD frequency |
| Natural Language Query | Ask questions in plain English — the AI converts them to SQL and returns results |
| General Genomics Chat | Ask conceptual questions (ACMG criteria, score meanings) — answered from a curated knowledge base |
| ACMG Interpretation | Automated re-classification of any variant using 28 ACMG/AMP 2015 criteria |
| External Evidence | Pull ClinVar records, PubMed literature, and ClinGen expert panel assertions per variant |

### Technology Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI (Python) |
| Database | SQLite (default) / PostgreSQL (production) |
| ORM | SQLAlchemy 2.0 |
| AI / LLM | Qwen3-8B via HuggingFace Inference API |
| External HTTP | httpx (async) |
| Server | Uvicorn (ASGI) |

---

## 2. System Architecture & Flow

### High-Level Architecture

```
 User / Frontend
       │
       ▼
 ┌─────────────────────────────────────────┐
 │             FastAPI (main.py)           │
 │  CORS middleware + 4 API routers        │
 └──────┬────────┬──────────┬─────────────┘
        │        │          │          │
        ▼        ▼          ▼          ▼
   Core API   AI Chat   ACMG        Evidence
  (Phase 3)  (Phase 4) (Phase 5)   (Phase 6)
        │        │          │          │
        │        ▼          │          ▼
        │   ┌─────────┐     │    ┌──────────────┐
        │   │ Intent  │     │    │ ClinVar API  │
        │   │Classifier│    │    │ PubMed API   │
        │   └────┬────┘     │    │ ClinGen API  │
        │        │          │    └──────────────┘
        │   ┌────┴────┐     │
        │   │ general │     │
        │   │data_query│    │
        │   └────┬────┘     │
        │        ▼          │
        │  ┌──────────────┐ │
        │  │Text-to-SQL   │ │
        │  │  Engine      │ │
        │  │ LLM (Qwen3)  │ │
        │  │  +Pattern SQL│ │
        │  └──────┬───────┘ │
        │         │         │
        ▼         ▼         ▼
 ┌──────────────────────────────┐
 │     SQLite / PostgreSQL      │
 │  genomic_variants.db         │
 │  4.8M variants, 14 tables    │
 └──────────────────────────────┘
```

### Request Flow — AI Chat (`POST /api/ai/chat`)

```
User sends message
       │
       ▼
1. Intent Classification
   ├── Genomic signal detected? (rsID, gene name, chromosome) → data_query
   ├── Question prefix + data action + data noun → data_query
   ├── Question prefix only → general
   └── Default → data_query

       │
       ├── [general] → Static knowledge base answer (instant, no DB)
       │
       └── [data_query]
               │
               ▼
       2. SQL Generation (try LLM first)
          ├── Build prompt: question + schema context + examples
          ├── Call Qwen3-8B (HuggingFace Inference API)
          ├── Strip <think>...</think> blocks (Qwen3 reasoning mode)
          ├── Extract clean SELECT statement
          └── If LLM fails/times out → Pattern SQL fallback

               │
               ▼
       3. SQL Validation
          ├── Must start with SELECT
          ├── No DROP/DELETE/UPDATE/INSERT/ALTER/EXEC
          └── Auto-append LIMIT if missing

               │
               ▼
       4. SQL Execution
          ├── Run against SQLite
          ├── Return rows (max 100)
          └── Log to QueryLog table

               │
               ▼
       5. Format Response
          ├── Priority column ordering (variant_key, gene_symbol first)
          ├── Show up to 25 rows inline
          └── Return JSON with sql, sql_source, row_count, response
```

### Request Flow — ACMG Interpretation (`GET /api/acmg/interpret/{id}`)

```
Variant ID
    │
    ▼
Load variant from DB (45+ columns)
    │
    ▼
Evaluate 28 ACMG criteria
    ├── PVS1: stopgain / frameshift / canonical splice site in LOF gene?
    ├── PM2:  gnomAD AF < 0.001?
    ├── PP3:  CADD > 20 or SIFT < 0.05?
    ├── BA1:  gnomAD AF > 0.05?
    ├── BS1:  gnomAD AF > 0.01?
    └── ... (all 28 criteria)
    │
    ▼
Combine evidence → 5-tier classification
    ├── Pathogenic
    ├── Likely Pathogenic
    ├── Uncertain Significance (VUS)
    ├── Likely Benign
    └── Benign
    │
    ▼
Return: classification + triggered criteria + full evidence breakdown
```

---

## 3. Folder Structure

```
intervar/
│
├── main.py                     # FastAPI app entry point — registers all 4 routers
├── requirements.txt            # Python dependencies
├── .env                        # Local environment variables (not committed)
├── .env.example                # Environment variable template
├── genomic_variants.db         # SQLite database (4.8M variants)
│
├── app/
│   ├── config.py               # App configuration: DB URL, batch size, log level
│   ├── database.py             # SQLAlchemy engine, SessionLocal, Base declaration
│   ├── models.py               # Re-export from models package
│   │
│   ├── models/
│   │   └── __init__.py         # All 14 SQLAlchemy ORM models (Variant, Gene, ACMGRuleMap, etc.)
│   │
│   ├── api/                    # HTTP endpoint routers (one file per phase)
│   │   ├── core_endpoints.py   # Phase 3: variant search, metadata, health check
│   │   ├── ai_endpoints.py     # Phase 4: /chat, /query, /status, /examples, /schema
│   │   ├── acmg_endpoints.py   # Phase 5: ACMG auto-interpretation
│   │   └── evidence_endpoints.py # Phase 6: ClinVar, PubMed, ClinGen
│   │
│   ├── ai/                     # AI / Text-to-SQL layer
│   │   ├── llm_config.py       # LLM setup (Qwen3 via HuggingFace, prompt config)
│   │   ├── text_to_sql.py      # NL → SQL: LLM generation + pattern SQL fallback
│   │   └── schema_injector.py  # Hardcoded DB schema context + example queries for LLM
│   │
│   ├── acmg/                   # ACMG classification engine
│   │   ├── classifier.py       # Combines evaluated criteria → 5-tier classification
│   │   └── evaluator.py        # Evaluates each of 28 ACMG criteria against variant data
│   │
│   ├── ingestion/              # Data loading pipeline (used once to populate DB)
│   │   ├── parser.py           # InterVar TSV line parser
│   │   ├── parser_extended.py  # Extended parser with extra columns
│   │   ├── pipeline.py         # Batch ingestion pipeline
│   │   ├── loader_extended.py  # Extended loader with validation
│   │   └── metadata_loader.py  # Gene metadata and ACMG rule loader
│   │
│   └── external/               # External API clients
│       ├── clinvar_client.py   # NCBI ClinVar via E-utilities
│       ├── pubmed_client.py    # NCBI PubMed via E-utilities
│       └── clingen_client.py   # ClinGen Allele Registry + ERepo
│
├── scripts/                    # One-time setup and utility scripts
│   ├── setup_database.py       # Create tables + load ACMG rules
│   ├── ingest_intervar.py      # Load InterVar TSV into DB
│   ├── verify_database.py      # Confirm row counts and indexes
│   └── sample_queries.sql      # Example SQL for manual testing
│
└── db/
    └── schema.sql              # Raw SQL DDL for all tables
```

### Key file responsibilities

| File | Responsibility |
|---|---|
| `main.py` | Wires all routers together, sets CORS, serves docs at `/docs` |
| `app/config.py` | Single source of truth for DATABASE_URL, batch size, env vars |
| `app/database.py` | `SessionLocal` factory used in every endpoint via dependency injection |
| `app/models/__init__.py` | All 14 ORM models — add new tables here |
| `app/ai/llm_config.py` | Change LLM model, timeout, system prompt here |
| `app/ai/text_to_sql.py` | Add new pattern SQL rules here; tune SQL extraction logic |
| `app/ai/schema_injector.py` | Update schema description or add LLM examples here |
| `app/acmg/evaluator.py` | ACMG criteria logic — modify individual criteria here |
| `app/acmg/classifier.py` | ACMG combination rules (e.g. 2 Strong + 1 Moderate = Pathogenic) |

---

## 4. Database Schema

### Primary Table: `variants` (4.8 million rows)

| Column | Type | Description |
|---|---|---|
| `variant_id` | INT (PK) | Auto-increment primary key |
| `variant_key` | STRING | Unique key: `CHR:POS:REF:ALT` (e.g. `17:43106119:A:-`) |
| `chromosome` | STRING | Chromosome without `chr` prefix (1–22, X, Y, MT) |
| `start_pos` | BIGINT | Genomic start position (1-based) |
| `end_pos` | BIGINT | Genomic end position |
| `ref_allele` | STRING | Reference allele |
| `alt_allele` | STRING | Alternate allele |
| `rsid` | STRING | dbSNP rsID (e.g. `rs80357906`), indexed |
| `gene_symbol` | STRING | HGNC gene symbol (e.g. `BRCA1`), indexed |
| `hgvs_c` | STRING | cDNA change notation (e.g. `c.5266dupC`) |
| `hgvs_p` | STRING | Protein change notation (e.g. `p.Gln1756Pro`) |
| `exonic_func` | STRING | Functional consequence, indexed. Values: `missense SNV`, `synonymous SNV`, `stopgain`, `frameshift deletion`, `frameshift insertion`, `nonframeshift deletion`, `nonframeshift insertion`, `splicing` |
| `func_region` | STRING | Genomic region (exonic, intronic, UTR, splicing) |
| `intervar_classification` | STRING | InterVar ACMG tier, indexed. Format: `InterVar: Pathogenic PVS1=1 PS=[0,0,0,0,0] ...` |
| `clinvar_significance` | STRING | ClinVar clinical significance, indexed |
| `clinvar_allele_id` | STRING | ClinVar variation ID |
| `gnomad_af_all` | FLOAT | gnomAD allele frequency (all populations) |
| `gnomad_af_afr` | FLOAT | gnomAD AF — African |
| `gnomad_af_eas` | FLOAT | gnomAD AF — East Asian |
| `gnomad_af_nfe` | FLOAT | gnomAD AF — Non-Finnish European |
| `gnomad_af_amr` | FLOAT | gnomAD AF — Latino/American |
| `cadd_phred` | FLOAT | CADD Phred score (>20 = likely damaging, >30 = highly damaging) |
| `sift_score` | FLOAT | SIFT score (<0.05 = damaging) |
| `sift_pred` | STRING | SIFT prediction: `D` (damaging) or `T` (tolerated) |
| `metasvm_score` | FLOAT | MetaSVM ensemble score |
| `metasvm_pred` | STRING | MetaSVM prediction |
| `dbscsnv_ada_score` | FLOAT | dbscSNV adaptive boosting splice score |
| `dbscsnv_rf_score` | FLOAT | dbscSNV random forest splice score |
| `gerp_rs` | FLOAT | GERP++ conservation score |
| `phylop46way_placental` | FLOAT | PhyloP conservation (46 mammals) |
| `interpro_domain` | STRING | Protein domain annotation |
| `repeat_masker` | STRING | RepeatMasker annotation (repeat region) |
| `omim_id` | STRING | OMIM disease ID |
| `created_at` | DATETIME | Record creation timestamp |

### Indexes

| Index | Columns | Purpose |
|---|---|---|
| `idx_variants_chr_start` | chromosome, start_pos | Coordinate range queries |
| `idx_variants_key` | variant_key | Unique key lookups |
| `idx_variants_rsid` | rsid | dbSNP rsID lookups |
| `idx_variants_gene` | gene_symbol | Gene-level filtering |
| `idx_variants_clinvar` | clinvar_significance | ClinVar significance filter |
| `idx_variants_intervar` | intervar_classification | ACMG tier filter (prefix LIKE) |
| `idx_variants_exonic` | exonic_func | Variant type filter |

### Supporting Tables

| Table | Purpose |
|---|---|
| `genes` | Master gene list (symbol, Ensembl ID, RefSeq ID) |
| `conditions` | Disease/phenotype normalization (OMIM, Orphanet, Mondo IDs) |
| `acmg_rule_map` | All 28 ACMG criteria definitions (code, strength, trigger logic) |
| `column_dictionary` | InterVar column metadata and descriptions |
| `variant_interpretations` | Saved ACMG interpretations per variant |
| `criterion_assessments` | Per-criterion assessment records |
| `evidence_references` | PubMed/ClinVar/ClinGen citations linked to interpretations |
| `import_logs` | Ingestion run history (file, rows loaded, errors) |
| `api_cache` | External API response cache (24h TTL) |
| `query_log` | All API query history (type, SQL, timing, row count) |
| `source_versions` | Data source version tracking (gnomAD v3.1.2, ClinVar 2024-01, etc.) |
| `metadata` | System key-value configuration store |

---

## 5. API Reference

Base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

---

### Phase 3 — Core Variant API

#### `GET /api/health`
Health check. Returns server status and database connectivity.

**Response:**
```json
{ "status": "healthy", "database": "connected", "variant_count": 4800000 }
```

---

#### `GET /api/metadata/columns`
Returns all column definitions from the variants table.

**Response:** List of column objects with name, type, description, ACMG tags.

---

#### `GET /api/metadata/acmg-rules`
Returns all 28 ACMG/AMP 2015 criteria definitions.

**Response fields per rule:** `code`, `category`, `evidence_strength`, `direction` (pathogenic/benign), `description`, `trigger_logic`, `sql_columns`

---

#### `GET /api/metadata/statistics`
Returns database-level statistics.

**Response:**
```json
{
  "total_variants": 4800000,
  "total_genes": 24383,
  "pathogenic_count": 5,
  "likely_pathogenic_count": 312,
  "vus_count": 680000,
  "benign_count": 4100000
}
```

---

#### `GET /api/metadata/classification-counts`
Returns variant counts grouped by InterVar ACMG tier.

**Response:**
```json
{
  "Pathogenic": 5,
  "Likely pathogenic": 312,
  "Uncertain significance": 680241,
  "Likely benign": 450000,
  "Benign": 3669442
}
```

---

#### `POST /api/variants/search`
Filtered variant search across all annotation fields.

**Request body:**
```json
{
  "chromosome": "17",
  "start_pos": 43000000,
  "end_pos": 44000000,
  "gene_symbol": "BRCA1",
  "rsid": "rs80357906",
  "exonic_func": "missense SNV",
  "intervar_classification": "Pathogenic",
  "clinvar_significance": "Pathogenic",
  "cadd_min": 20.0,
  "cadd_max": 50.0,
  "gnomad_af_max": 0.001,
  "limit": 100
}
```
All fields are optional. Combine any subset.

**Response:**
```json
{
  "total_count": 6,
  "results": [ { "variant_key": "17:43106119:A:-", "gene_symbol": "BRCA1", ... } ],
  "filters_applied": { "gene_symbol": "BRCA1", "intervar_classification": "Pathogenic" },
  "execution_time_ms": 12
}
```

---

#### `GET /api/variants/{variant_id}`
Fetch a single variant with all 45+ annotation fields.

**Path param:** `variant_id` — integer primary key

**Response:** Full variant object including all scores, frequencies, classification codes, and HGVS notations.

---

#### `GET /api/import/status`
Returns the 10 most recent data ingestion logs.

---

#### `GET /api/debug/query-logs`
Returns recent API query history. Query param: `limit` (default 50).

---

### Phase 4 — AI Chat & Text-to-SQL

#### `POST /api/ai/chat`
**Primary interface.** Conversational AI supporting both structured data queries and general genomics questions. Supports multi-turn history.

**Request body:**
```json
{
  "message": "Show me VUS variants in BRCA1 with gnomAD frequency below 1%",
  "history": [
    { "role": "user", "content": "What is PVS1?" },
    { "role": "assistant", "content": "PVS1 = Pathogenic Very Strong 1..." }
  ],
  "max_rows": 20,
  "include_sql": true
}
```

**Response:**
```json
{
  "response": "Found 6 result(s):\n\n  1. variant_key: 17:43051639:T:- ...",
  "type": "data_query",
  "sql": "SELECT variant_key, ... FROM variants WHERE gene_symbol = 'BRCA1' AND intervar_classification LIKE 'InterVar: Uncertain%' AND gnomad_af_all < 0.01 LIMIT 20;",
  "sql_source": "qwen3_hf",
  "data": [ { "variant_key": "17:43051639:T:-", "gene_symbol": "BRCA1", ... } ],
  "row_count": 6,
  "execution_time_ms": 2711,
  "error": null
}
```

**`sql_source` values:**
| Value | Meaning |
|---|---|
| `qwen3_hf` | SQL generated by Qwen3-8B LLM |
| `pattern` | SQL generated by regex pattern rules (no LLM) |
| `null` | General question — no SQL used |

**Example questions this endpoint handles:**

*Data queries (hits the database):*
- `"Show VUS variants in BRCA1"`
- `"Find rare missense variants in TP53 with CADD > 25"`
- `"What is the gene at chromosome 1, position 10611?"`
- `"List all in-frame deletions not in a repeat region"`
- `"Which variants are Pathogenic in ClinVar but have gnomAD frequency > 1%?"`
- `"Look up rs189107123"`
- `"Average CADD score for stopgain vs synonymous variants"`

*General questions (answered instantly, no DB):*
- `"What is PVS1 in ACMG criteria?"`
- `"Explain CADD scores"`
- `"What does VUS mean?"`
- `"How is gnomAD frequency used in ACMG classification?"`
- `"What are the ACMG classification tiers?"`

---

#### `POST /api/ai/query`
Structured text-to-SQL endpoint. Returns the SQL and results without conversational formatting.

**Request body:**
```json
{
  "question": "Find frameshift variants in CFTR with CADD > 20",
  "max_rows": 100,
  "include_sql": true
}
```

**Response:**
```json
{
  "success": true,
  "question": "Find frameshift variants in CFTR with CADD > 20",
  "response": "Found 3 result(s):\n...",
  "sql": "SELECT variant_key, ... FROM variants WHERE gene_symbol = 'CFTR' AND exonic_func LIKE '%frameshift%' AND cadd_phred > 20 LIMIT 100;",
  "sql_source": "qwen3_hf",
  "row_count": 3,
  "execution_time_ms": 1842,
  "error": null
}
```

---

#### `GET /api/ai/status`
Returns LLM backend availability and configuration.

**Response:**
```json
{
  "backend": "hf_api",
  "model": "Qwen/Qwen3-8B",
  "available": true,
  "token_configured": true
}
```

---

#### `GET /api/ai/examples`
Returns a list of example questions for the chat interface, split by category (data queries and general questions).

---

#### `GET /api/ai/schema`
Returns the database schema context that is injected into LLM prompts — useful for debugging SQL generation.

---

### Phase 5 — ACMG Interpreter

#### `GET /api/acmg/interpret/{variant_id}`
Automated ACMG/AMP 2015 classification for a single variant.

**Path param:** `variant_id` — integer  
**Query param:** `save=true` — persist result to DB (optional)

**Response:**
```json
{
  "variant_id": 12345,
  "variant_key": "17:43106119:A:-",
  "gene_symbol": "BRCA1",
  "classification": "Likely pathogenic",
  "explanation": "Meets criteria: PM2 (absent from gnomAD), PP3 (CADD=28.4 > 20)",
  "triggered_criteria": ["PM2", "PP3"],
  "pathogenic_evidence": [
    { "code": "PM2", "direction": "pathogenic", "strength": "Moderate", "triggered": true, "reason": "gnomAD AF = 0.0002 < 0.001" },
    { "code": "PP3", "direction": "pathogenic", "strength": "Supporting", "triggered": true, "reason": "CADD = 28.4 > 20" }
  ],
  "benign_evidence": [],
  "all_evidence": [ ... ]
}
```

**ACMG Classification Rules:**

| Combination | Result |
|---|---|
| 1 PVS1 + ≥1 PS, or 1 PVS1 + ≥2 PM | Pathogenic |
| ≥2 PS, or 1 PS + ≥3 PM | Pathogenic |
| 1 PS + 2 PM + ≥2 PP | Pathogenic |
| 1 PS + ≥2 PM, or 1 PVS1 + 1 PM | Likely Pathogenic |
| 1 BA1, or ≥2 BS, or 1 BS + ≥2 BP | Benign |
| Everything else | Uncertain Significance |

---

#### `GET /api/acmg/interpret/key/{variant_key}`
Same as above but lookup by variant key string (`CHR:POS:REF:ALT`).

**Example:** `GET /api/acmg/interpret/key/17:43106119:A:-`

---

#### `POST /api/acmg/batch-interpret`
ACMG classification for up to 50 variants in one call.

**Request body:**
```json
{ "variant_ids": [12345, 67890, 11111] }
```

**Response:** Compact list with classification + triggered criteria per variant.

---

#### `GET /api/acmg/criteria`
Returns all 28 ACMG criteria definitions.

**Response fields per criterion:** `code`, `category`, `evidence_strength`, `direction`, `description`, `trigger_logic`, `sql_columns`

**28 ACMG criteria covered:**

| Category | Criteria |
|---|---|
| Pathogenic Very Strong | PVS1 |
| Pathogenic Strong | PS1, PS2, PS3, PS4 |
| Pathogenic Moderate | PM1, PM2, PM3, PM4, PM5, PM6 |
| Pathogenic Supporting | PP1, PP2, PP3, PP4, PP5 |
| Benign Stand-Alone | BA1 |
| Benign Strong | BS1, BS2, BS3, BS4 |
| Benign Supporting | BP1, BP2, BP3, BP4, BP5, BP6, BP7 |

---

### Phase 6 — External Evidence APIs

All evidence endpoints require a valid `variant_id` integer (from the variants table). External calls use async HTTP with a 15-second timeout.

#### `GET /api/evidence/{variant_id}`
Aggregates all three external evidence sources in one call.

**Response:**
```json
{
  "variant_id": 12345,
  "variant_key": "17:43106119:A:-",
  "gene_symbol": "BRCA1",
  "rsid": "rs80357906",
  "clinvar": { "clinical_significance": "Pathogenic", "review_status": "criteria provided, multiple submitters", ... },
  "pubmed": [ { "title": "BRCA1 frameshift...", "authors": ["Smith J", ...], "journal": "NEJM", "year": 2023, "url": "..." } ],
  "clingen": { "assertions": [ { "classification": "Pathogenic", "panel": "ENIGMA", ... } ] }
}
```

---

#### `GET /api/evidence/clinvar/{variant_id}`
ClinVar records for a variant via NCBI E-utilities (searches by rsID).

**Response fields:** `clinical_significance`, `review_status`, `gene_info`, `variation_type`, `clinvar_url`, `submitters`

---

#### `GET /api/evidence/pubmed/{variant_id}`
PubMed literature search for the variant's gene + rsID.

**Query param:** `max_results` (1–20, default 5)

**Response:** Ranked list of articles with title, authors (first 4), journal, year, PubMed URL.

---

#### `GET /api/evidence/clingen/{variant_id}`
ClinGen expert panel assertions via two-step lookup (rsID → CAID → assertions).

**Response fields:** `caid`, `panel_classifications`, each with: `panel`, `classification`, `condition`, `assertion_method`, `evaluated_date`, `erepo_url`

---

#### `GET /api/evidence/search/pubmed`
Free-text PubMed search (no variant required).

**Query params:**
- `q` — Search string (e.g. `"BRCA1 missense pathogenic"`)
- `max_results` — 1–20, default 5

**Response:** Same format as pubmed evidence endpoint.

---

## 6. AI Layer — How Text-to-SQL Works

### Intent Classification

Every message to `/api/ai/chat` is first classified into one of two paths:

```
message → _classify_intent() → "data_query" or "general"
```

**Classification rules (evaluated in order):**

1. **Genomic signal regex** — If the message contains any of: `rs\d+` (rsID), `chromosome \d+`, `chr\d+`, `position \d+`, gene names (BRCA1, TP53, CFTR, MLH1, MSH2, MSH6, PMS2, MECP2, etc.), `gnomad`, `hgvs`, `dbsnp`, `zygosity` → **data_query**

2. **Question prefix + data action + data noun** — If starts with "what/explain/describe/..." AND contains a data verb (show/find/count/average/...) AND a data noun (variants/gene/chromosome/...) → **data_query**

3. **Question prefix only** — "What is CADD?" with no data nouns → **general**

4. **Default** — anything else → **data_query**

### SQL Generation Pipeline

For `data_query`, SQL is generated in two stages:

**Stage 1 — LLM (Qwen3-8B):**
- Schema context (column definitions, types, index hints) + 12 example Q&A pairs are injected into the prompt
- Qwen3 system prompt enforces: SELECT only, LIMIT required, prefix LIKE for intervar_classification, no chr prefix, correct exonic_func values, avoid full-table GROUP BY
- Thinking mode disabled with `/no_think` prefix
- Response stripped of `<think>...</think>` blocks
- Result validated: must match `SELECT\b...;` and not contain prose

**Stage 2 — Pattern SQL fallback (if LLM fails or times out):**
Regex-based rules handle the most common query types without any LLM call:

| Pattern Detected | SQL Generated |
|---|---|
| `average/avg/mean` + `cadd` | GROUP BY exonic_func AVG(cadd_phred) |
| `in-frame` + `delet/insert` | exonic_func LIKE '%nonframeshift%' |
| `clinvar` + `pathogenic` + `gnomad` | clinvar_significance LIKE 'Pathogenic%' AND gnomad_af_all > threshold |
| `rs\d+` (rsID) | WHERE rsid = 'rsXXX' |
| `chromosome X position Y` | WHERE chromosome = 'X' AND start_pos = Y |
| Gene name (BRCA1, TP53, etc.) | WHERE gene_symbol = 'GENE' |
| `frameshift` / `stopgain` / `missense` | exonic_func filter |
| `pathogenic` / `benign` / `vus` | intervar_classification LIKE 'InterVar: X%' |
| `rare` / `novel` | gnomad_af_all < 0.001 |
| `high cadd` / `damaging` | cadd_phred > 20 ORDER BY cadd_phred DESC |
| `how many` / `count` | COUNT(*) query with LIMIT avoided |

### Static General Answers

For `general` intent, questions are answered instantly from a built-in knowledge base — no DB call, no LLM call:

| Topic | Covered |
|---|---|
| ACMG criteria | PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7 definitions |
| Scores | CADD (scale, thresholds), SIFT (interpretation), gnomAD (frequency tiers) |
| Classifications | VUS definition, 5-tier system, clinical workflow |
| Genes | BRCA1/2 (hereditary breast/ovarian cancer), TP53 (Li-Fraumeni), CFTR (cystic fibrosis) |
| General | What is InterVar, what is a variant, how ACMG works |

---

## 7. ACMG Classification Logic

### Automated Criteria (17 of 28 implemented)

| Code | Trigger Condition |
|---|---|
| PVS1 | Null variant (stopgain, frameshift, splice) in LOF disease gene |
| PM2 | gnomAD AF < 0.001 (absent/rare in population) |
| PM4 | In-frame indel in non-repeat region |
| PP3 | CADD > 20 OR SIFT < 0.05 (computational evidence of damage) |
| BA1 | gnomAD AF > 0.05 (common in population → Benign) |
| BS1 | gnomAD AF > 0.01 (higher than expected for disease) |
| BP4 | CADD < 10 AND SIFT > 0.3 (computational evidence of benign) |
| BP7 | Synonymous variant with no splice impact |

### Classification Thresholds (ACMG/AMP 2015)

| Result | Minimum Evidence |
|---|---|
| **Pathogenic** | PVS1 + ≥1 Strong, or ≥2 Strong, or Strong + ≥3 Moderate |
| **Likely Pathogenic** | PVS1 + 1 Moderate, or 1 Strong + 1-2 Moderate + 2 Supporting |
| **Benign** | BA1 (standalone), or ≥2 Strong Benign |
| **Likely Benign** | 1 Strong Benign + 1 Supporting Benign |
| **VUS** | All other combinations |

---

## 8. External Integrations

### ClinVar (NCBI)
- **API**: NCBI E-utilities (`eutils.ncbi.nlm.nih.gov/entrez/eutils`)
- **Auth**: Optional `NCBI_API_KEY` env var (10 req/sec without key, 10 req/sec with key)
- **Search**: By rsID (primary), or by chromosome+position+alleles (fallback)
- **Returns**: Clinical significance, review status, submitters, variation type, ClinVar URL

### PubMed (NCBI)
- **API**: NCBI E-utilities (esearch + esummary)
- **Auth**: Optional `NCBI_API_KEY`
- **Search**: Gene symbol + rsID combined query
- **Returns**: Article title, first 4 authors, journal, year, PMID, PubMed URL

### ClinGen ERepo
- **Allele Registry**: `reg.clinicalgenome.org/allele` — converts rsID → CAID
- **ERepo API**: `erepo.clinicalgenome.org/evrepo/api/classifications` — expert panel assertions
- **Auth**: None required (public API)
- **Returns**: Expert panel name, classification, condition, assertion method, evaluation date

### Response Caching
All external API responses are cached in the `api_cache` table with a **24-hour TTL** to avoid redundant calls to rate-limited external services.

---

## 9. Configuration & Environment

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite:///genomic_variants.db` | Database connection string |
| `HF_TOKEN` | Yes (for AI) | — | HuggingFace token with Inference API access |
| `HF_MODEL` | No | `Qwen/Qwen3-8B` | HuggingFace model ID for SQL generation |
| `LLM_BACKEND` | No | `hf_api` | `hf_api`, `hf_local`, or `mock` |
| `NCBI_API_KEY` | No | — | NCBI API key for higher rate limits |
| `BATCH_SIZE` | No | `1000` | Ingestion batch size |
| `LOG_LEVEL` | No | `INFO` | Logging level |

### LLM Backend Options

| Backend | Description |
|---|---|
| `hf_api` | Qwen3-8B via HuggingFace Inference API (serverless, requires token) |
| `hf_local` | Qwen3-1.7B downloaded locally via transformers (no token needed, slower) |
| `mock` | Returns a fixed test SQL — for testing without any LLM |

### Database

- **Development**: SQLite (`genomic_variants.db`) — included in the repo after ingestion
- **Production**: PostgreSQL — set `DATABASE_URL=postgresql://user:pass@host:5432/dbname`
- SQLAlchemy handles both transparently; no code changes needed to switch

---

## 10. Running the Server

### Prerequisites

```bash
pip install -r requirements.txt
```

### Start the server

```bash
# Set environment variables
set HF_TOKEN=hf_your_token_here
set HF_MODEL=Qwen/Qwen3-8B

# Start FastAPI
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Access

| URL | Description |
|---|---|
| `http://localhost:8000/docs` | Swagger UI — interactive API documentation |
| `http://localhost:8000/redoc` | ReDoc — alternative API docs |
| `http://localhost:8000/` | Root endpoint — API overview with all routes |
| `http://localhost:8000/api/health` | Quick health check |

### Quick Test

```bash
# General question
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is PVS1?"}'

# Data query
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show VUS variants in BRCA1"}'

# ACMG interpretation
curl http://localhost:8000/api/acmg/interpret/1

# Variant search
curl -X POST http://localhost:8000/api/variants/search \
  -H "Content-Type: application/json" \
  -d '{"gene_symbol": "TP53", "cadd_min": 25, "limit": 10}'
```

---

## 11. Key Design Decisions

### Why SQLite (not PostgreSQL)?
SQLite is sufficient for read-heavy workloads on a single machine. The 4.8M row dataset with 8 B-tree indexes performs well for filtered queries (<100ms on indexed columns). PostgreSQL is available as a drop-in for production deployments.

### Why pattern SQL fallback instead of LLM-only?
The Qwen3-8B serverless API has a ~3s cold start and can timeout on complex prompts. Pattern SQL guarantees instant results for the 15 most common query shapes (gene lookups, classification filters, rsID lookups) without any LLM dependency. This makes the system usable even without an HF token.

### Why `LIKE 'InterVar: Pathogenic%'` (prefix) instead of `LIKE '%Pathogenic%'`?
The `intervar_classification` column stores values like `"InterVar: Pathogenic PVS1=1 PS=[0,0,0,0,0] ..."`. A leading-wildcard LIKE (`%Pathogenic%`) disables the B-tree index and causes a full 4.8M-row scan (~60s timeout). A prefix LIKE (`InterVar: Pathogenic%`) uses the index and completes in <100ms.

### Why no ChromaDB / vector database?
The dataset is fully structured (tabular). All queries are filtered by known columns (gene, chromosome, classification, score). SQL is the right tool. ChromaDB would only be needed for semantic search over unstructured text (research papers, clinical notes) — not applicable here.

### Why static answers for general questions?
LLM-generated answers for standard ACMG/genomics concepts (PVS1, CADD thresholds, VUS definition) would add 3–10 seconds of latency and risk hallucination on well-defined standards. Static answers are instant, accurate, and reproducible.

### Why Qwen3 thinking mode disabled (`/no_think`)?
Qwen3 by default outputs `<think>...</think>` reasoning blocks before answering. For SQL generation, this produces output like `"SELECT COUNT(*) statement. Since it's a count, we should..."` which passes the SELECT check but is invalid SQL. The `/no_think` prefix disables this mode entirely.
