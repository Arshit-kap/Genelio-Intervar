# Genelio — Genomic Variant Q&A Engine

An AI-powered genomic variant interpretation system that lets clinicians and researchers query a patient's whole-genome sequencing (WGS) report in plain English. Built on a 34-column InterVar-annotated SQLite database, a 6-layer conversational pipeline, Qwen3-32B LLM, HPO symptom resolution, and a Gradio frontend.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Database Format](#database-format)
4. [6-Layer Pipeline](#6-layer-pipeline)
5. [Key Modifications (modification_1 branch)](#key-modifications-modification_1-branch)
6. [Setup & Installation](#setup--installation)
7. [Running the System](#running-the-system)
8. [API Reference](#api-reference)
9. [Configuration](#configuration)
10. [External APIs](#external-apis)
11. [HPO Symptom Resolution](#hpo-symptom-resolution)
12. [Gene Disease Enricher](#gene-disease-enricher)
13. [Deployment (Remote Server)](#deployment-remote-server)
14. [Example Queries](#example-queries)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    GRADIO FRONTEND (port 7860)                  │
│              gradio_app.py  →  http://localhost:8000            │
└──────────────────────────┬──────────────────────────────────────┘
                           │  HTTP POST /api/ai/chat
┌──────────────────────────▼──────────────────────────────────────┐
│                 FASTAPI BACKEND (port 8000)                     │
│                    main.py + app/                               │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              6-LAYER CHAT PIPELINE                         │ │
│  │  L0: Safety refuse          L1: Intent classify            │ │
│  │  L1.5: Opportunistic HPO    L2: HPO resolution             │ │
│  │  L3: Text-to-SQL engine     L4: Top-10 cap                 │ │
│  │  L5: LLM explanation        L6: Disclaimer                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ patient_     │  │ Qwen3-32B    │  │ External APIs        │  │
│  │ variants.db  │  │ via Ollama   │  │ ClinVar / PubMed     │  │
│  │ SQLite 76K   │  │ port 11434   │  │ NCBI eutils          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
intervar/
├── main.py                        # FastAPI application entry point
├── gradio_app.py                  # Gradio chat frontend
├── requirements.txt               # Python dependencies
├── .env.example                   # Environment variable template
├── .gitignore
│
├── app/
│   ├── config.py                  # Settings (DB URL, LLM backend, etc.)
│   ├── database.py                # SQLAlchemy engine + SessionLocal
│   ├── models.py                  # ORM models (QueryLog, etc.)
│   ├── utils.py                   # Shared utilities
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── llm_config.py          # LLM backend (Ollama/vLLM/HF) + system prompts
│   │   ├── text_to_sql.py         # NL → SQL engine (pattern SQL + LLM fallback)
│   │   ├── schema_injector.py     # DB schema + SQL examples injected into LLM prompt
│   │   └── gene_enricher.py       # ★ NEW: Disease association enricher (anti-hallucination)
│   │
│   ├── api/
│   │   ├── ai_endpoints.py        # POST /api/ai/chat  (6-layer pipeline)
│   │   ├── acmg_endpoints.py      # ACMG criteria endpoints
│   │   ├── core_endpoints.py      # Health, stats, variant lookup
│   │   └── evidence_endpoints.py  # External evidence endpoints
│   │
│   ├── hpo/
│   │   ├── __init__.py
│   │   ├── resolver.py            # HPO term resolution + gene ranking
│   │   └── data/
│   │       ├── hpo_terms.tsv.gz   # HPO ontology term → phenotype name
│   │       └── hpo_to_genes.tsv.gz # HPO term → associated genes
│   │
│   ├── acmg/
│   │   ├── __init__.py
│   │   ├── classifier.py          # ACMG/AMP 2015 classification logic
│   │   └── evaluator.py           # Evidence code evaluator
│   │
│   ├── external/
│   │   ├── __init__.py
│   │   ├── clinvar_client.py      # ClinVar NCBI eutils client
│   │   ├── clingen_client.py      # ClinGen Gene-Disease Validity client
│   │   └── pubmed_client.py       # PubMed NCBI eutils client
│   │
│   └── ingestion/
│       ├── __init__.py
│       ├── parser.py              # InterVar TSV parser
│       ├── parser_extended.py     # Extended parser (34-col format)
│       ├── loader_extended.py     # Batch loader into SQLite
│       ├── pipeline.py            # Full ingestion pipeline
│       └── normalizer_extended.py # Column normalizer
│
├── scripts/
│   ├── ingest_intervar.py         # Ingest InterVar TSV → patient_variants.db
│   ├── load_intervar.py           # Loader script
│   ├── setup_database.py          # Schema creation
│   ├── verify_database.py         # Post-ingestion verification
│   └── sample_queries.sql         # Example SQL queries
│
├── db/
│   └── schema.sql                 # SQLite schema definition
│
├── acmg_rules(Codes_as_per_intervar).csv  # ACMG evidence codes reference
└── add_indexes.py                 # Index creation for query performance
```

---

## Database Format

The system uses `patient_variants.db` — a SQLite database ingested from InterVar-annotated WGS output.

| Property | Value |
|----------|-------|
| File | `patient_variants.db` |
| Format | SQLite |
| Rows | ~76,330 variants |
| Columns | 34 (InterVar TSV format) |
| Primary table | `variants` |
| Source | `intervar_MG_100.filtered.txt` (InterVar output) |

### Key Columns

| Column | Type | Description |
|--------|------|-------------|
| `Chr` | TEXT | Chromosome (1–22, X, Y, MT) — no `chr` prefix |
| `Start`, `End` | INTEGER | Genomic coordinates |
| `Ref`, `Alt` | TEXT | Reference / alternate alleles |
| `"Ref.Gene"` | TEXT | HGNC gene symbol (double-quoted in SQL) |
| `"ExonicFunc.refGene"` | TEXT | Variant function: `nonsynonymous SNV`, `stopgain`, `frameshift deletion`, etc. |
| `"AAChange.refGene"` | TEXT | HGVS amino acid change notation |
| `"InterVar: InterVar and Evidence"` | TEXT | InterVar classification + evidence codes |
| `"clinvar: Clinvar"` | TEXT | ClinVar significance with `clinvar: ` prefix |
| `Freq_gnomAD_genome_ALL` | REAL | gnomAD overall allele frequency |
| `CADD_phred` | REAL | CADD deleteriousness score |
| `Otherinfo` | TEXT | Zygosity and genotype (het/hom) |
| `Orpha` | TEXT | Orphanet disease (pipe-delimited format) |
| `OMIM` | TEXT | OMIM gene ID |
| `Phenotype_MIM` | TEXT | OMIM phenotype IDs |

### Critical SQL Rules

```sql
-- Column names with dots/spaces MUST be double-quoted
WHERE "Ref.Gene" = 'BRCA1'
WHERE "ExonicFunc.refGene" = 'stopgain'

-- ClinVar values have a prefix + trailing space
WHERE "clinvar: Clinvar" LIKE 'clinvar: Pathogenic%'
  AND "clinvar: Clinvar" NOT LIKE 'clinvar: Conflicting%'

-- InterVar uses prefix LIKE (not %Pathogenic%)
WHERE "InterVar: InterVar and Evidence" LIKE 'InterVar: Pathogenic%'

-- Chr has NO 'chr' prefix
WHERE Chr = '17'    -- correct
WHERE Chr = 'chr17' -- WRONG

-- NULL handling: '.' values were ingested as NULL
WHERE CADD_phred IS NOT NULL  -- not != '.'
```

---

## 6-Layer Pipeline

Every message sent to `POST /api/ai/chat` passes through 6 layers:

```
User message
     │
     ▼
┌─── Layer 0: Safety Refuse ─────────────────────────────────────┐
│  Blocks: diagnosis, prognosis, treatment, reproductive,        │
│  self-harm queries → returns fixed safe response               │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 1: Intent Classification ─────────────────────────────┐
│  Classifies: hpo_query | anaphora_query | data_query | general │
│  Pattern matching on action verbs, genomic signals, pronouns   │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 1.5: Opportunistic HPO Enrichment ★ NEW ─────────────┐
│  Even when intent = data_query, if symptom words appear        │
│  (e.g. "I have seizures — show pathogenic variants") →         │
│  runs HPO resolution AND upgrades intent to hpo_query          │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 2: HPO Symptom Resolution ────────────────────────────┐
│  "I have muscle weakness and seizures" →                       │
│  Maps to HPO terms → ranked gene candidate pool (up to 300)   │
│  Asks one clarification if nothing resolves                    │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 3: Text-to-SQL Engine ────────────────────────────────┐
│  Pattern SQL (fast, deterministic) → LLM SQL (Qwen3 fallback)  │
│  Validates: SELECT only, LIMIT required, FROM variants, safe   │
│  Executes against patient_variants.db                          │
│  Gene enricher adds _diseases field to each row ★ NEW         │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 4: Top-10 Cap ─────────────────────────────────────────┐
│  Always caps patient-facing results at 10 rows                  │
│  Adds note: "Showing top 10 of N matches ordered by CADD"       │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 5: LLM Plain-English Explanation ─────────────────────┐
│  Qwen3-32B explains the SQL results in natural language        │
│  Injects: schema context, rows, _diseases field, history       │
│  MUST use _diseases for disease names — not training memory    │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─── Layer 6: Disclaimer Middleware ──────────────────────────────┐
│  Appends clinical disclaimer when response mentions:            │
│  pathogenic, ClinVar, InterVar, inheritance, ACMG codes, etc.  │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
ChatResponse { response, type, sql, sql_source, data, row_count }
```

---

## Key Modifications (`modification_1` branch)

### 1. Gene Disease Enricher — `app/ai/gene_enricher.py` ★ NEW FILE

Prevents LLM hallucination of disease associations. Previously the LLM used training-memory disease names instead of the patient's own DB columns (OMIM / Orphanet / Phenotype_MIM).

**Priority chain per gene:**
1. Orpha column in the current result row (pipe-delimited Orphanet format)
2. Cross-row DB lookup — pathogenic variant rows often have `NULL` Orpha, but other rows for the same gene carry full Orpha data
3. HPO gene API fallback (`https://hpo.jax.org/api/hpo/gene/{gene}`, cached)
4. `"No disease association in database"`

**Example — IDUA gene:**

| Before | After |
|--------|-------|
| LLM used training knowledge: *"Hurler syndrome"* | DB-sourced: *"Alpha-L-iduronidase deficiency; Mucopolysaccharidosis type I (MPS1)"* |

The Orpha format (`579|Alpha-L-iduronidase deficiency<br>MPS1<br>...|prevalence|inheritance|onset|OMIM`) is parsed with HTML tag stripping and up to 3 disease names extracted.

---

### 2. Classification Tier Breakdown — `app/ai/text_to_sql.py`

New pattern SQL that responds to "classification tier breakdown with gene count" queries:

```sql
SELECT
  CASE
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Pathogenic%'
      AND "clinvar: Clinvar" NOT LIKE 'clinvar: Conflicting%' THEN 'Pathogenic'
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Likely_pathogenic%'     THEN 'Likely Pathogenic'
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Pathogenic/Likely_pathogenic%'
                                                                   THEN 'Pathogenic/Likely Pathogenic'
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Benign%'                THEN 'Benign'
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Likely_benign%'         THEN 'Likely Benign'
    WHEN "clinvar: Clinvar" LIKE 'clinvar: Uncertain%'
      OR "clinvar: Clinvar" LIKE 'clinvar: Conflicting%'           THEN 'VUS/Conflicting'
    WHEN "InterVar: InterVar and Evidence" LIKE 'InterVar: Pathogenic%'
                                                                   THEN 'Pathogenic (InterVar)'
    WHEN "InterVar: InterVar and Evidence" LIKE 'InterVar: Likely pathogenic%'
                                                                   THEN 'Likely Pathogenic (InterVar)'
    ELSE 'Other/Unknown'
  END AS classification_tier,
  COUNT(DISTINCT "Ref.Gene") AS gene_count,
  COUNT(*) AS variant_count
FROM variants
GROUP BY classification_tier
ORDER BY variant_count DESC LIMIT 20;
```

---

### 3. Opportunistic HPO Enrichment — `app/api/ai_endpoints.py` (Layer 1.5)

HPO resolution now runs whenever symptom trigger words appear, even when primary intent is `data_query`.

```
Before: "I have seizures — show pathogenic variants" → data_query (HPO skipped)
After:  "I have seizures — show pathogenic variants" → hpo_query (HPO + variant lookup)
```

---

### 4. Intent Routing Fixes — `app/api/ai_endpoints.py`

| Fix | Detail |
|-----|--------|
| `_DATA_ACTION` | Added `average\|mean\|avg` → "average CADD score" routes to `data_query` |
| `_DATA_OBJECT` | Added `disease.caus\|disease-caus\|harmful\|dangerous` |
| `_PATIENT_REPORT` | Fixed regex: `disease.{0,2}caus` → `disease.{0,2}caus\w*` (word-boundary bug dropped "disease causing") |
| `_GENOMIC_SIGNAL` | Added `pathogenic\|disease.caus\|disease-caus` |

---

### 5. LLM System Prompt — `app/ai/llm_config.py`

Added Rule 5 to the explain system message:

```
CRITICAL — Disease associations:
Each row contains a '_diseases' field from the patient's own OMIM/Orphanet database.
You MUST use ONLY '_diseases' when stating what disease a gene causes.
NEVER use training memory for gene-disease links — it may be wrong or outdated.
If '_diseases' says 'MSMD due to complete ISG15 deficiency', report exactly that.
Do NOT substitute a more famous association from your training knowledge.
```

---

### 6. Schema Injector — `app/ai/schema_injector.py`

- Fixed ClinVar SQL example from wrong `LIKE '%Pathogenic%'` to correct prefix: `LIKE 'clinvar: Pathogenic%'`
- Added classification tier breakdown SQL example for LLM guidance
- Added `Phenotype_MIM` to `_COLS_FULL_CLINICAL` column list

---

### 7. Database URL Fix — `.env`

```
# Before (wrong — 4.8M row normalized schema, different column names)
SQLALCHEMY_DATABASE_URL=sqlite:///genomic_variants.db

# After (correct — 76K row InterVar format, 34 columns)
SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) with `qwen3:32b` (for LLM features)
- ~4 GB disk space for HPO data + SQLite DB

### 1. Clone the repository

```bash
git clone https://github.com/Arshit-kap/Genelio-Intervar.git
cd Genelio-Intervar
git checkout modification_1
```

### 2. Create environment

```bash
conda create -n intervar python=3.11 -y
conda activate intervar
pip install -r requirements.txt
```

Or with venv:

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Database
SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db

# LLM Backend
LLM_BACKEND=vllm_api
VLLM_API_URL=http://localhost:11434
VLLM_MODEL=qwen3:32b

# Optional HuggingFace token (for hf_api backend)
# HF_TOKEN=hf_...
```

### 4. Ingest InterVar data

```bash
python scripts/ingest_intervar.py \
    --input "intervar_MG_100.filtered.txt" \
    --db patient_variants.db

# Verify
python scripts/verify_database.py
# Expected: 76,330 rows | 34 columns | variants table OK
```

### 5. Create indexes

```bash
python add_indexes.py
```

Creates indexes on `Chr`, `"Ref.Gene"`, `"clinvar: Clinvar"`, `"InterVar: InterVar and Evidence"`, `CADD_phred`.

### 6. Start Ollama

```bash
ollama pull qwen3:32b
ollama serve   # port 11434
```

---

## Running the System

```bash
# Terminal 1 — Backend
uvicorn main:app --reload --port 8000

# Terminal 2 — Gradio frontend
python gradio_app.py
# Open http://localhost:7860
```

Or use the start scripts:

```bash
bash start_servers.sh      # Linux/Mac
.\start.ps1                # Windows PowerShell
```

---

## API Reference

Base URL: `http://localhost:8000`

### `POST /api/ai/chat`

Main conversational endpoint.

**Request body:**
```json
{
  "message": "Show pathogenic variants in BRCA1",
  "history": [
    {"role": "user", "content": "previous message"},
    {"role": "assistant", "content": "previous response"}
  ],
  "max_rows": 20,
  "include_sql": true
}
```

**Response:**
```json
{
  "response": "Found 3 pathogenic variants in BRCA1...",
  "type": "data_query",
  "sql": "SELECT ... FROM variants WHERE ...",
  "sql_source": "pattern",
  "data": [
    {
      "Ref.Gene": "BRCA1",
      "clinvar: Clinvar": "clinvar: Pathogenic ",
      "CADD_phred": 34.1,
      "_diseases": "Hereditary breast and ovarian cancer syndrome",
      ...
    }
  ],
  "row_count": 3,
  "execution_time_ms": 45.2
}
```

**Response `type` values:**

| Type | Description |
|------|-------------|
| `data_query` | SQL executed against patient DB |
| `hpo_query` | Symptom → HPO → gene → DB lookup |
| `hpo_clarification` | Asked for more specific symptom terms |
| `general` | General genomics Q&A (no DB query) |
| `safety_refuse` | Blocked (diagnosis / treatment request) |

**`sql_source` values:**

| Source | Description |
|--------|-------------|
| `pattern` | Deterministic regex-based SQL (fast) |
| `llm` | Qwen3-generated SQL (fallback) |
| `hpo_pattern` | HPO gene-list SQL |
| `external_api` | ClinVar / PubMed fallback (0 local rows) |

---

### `POST /api/ai/query`

Structured text-to-SQL (raw SQL + rows, no LLM explanation):

```json
{ "question": "Count variants by chromosome", "max_rows": 100, "include_sql": true }
```

---

### `GET /api/ai/status`

LLM backend status:

```json
{ "status": "ready", "llm_ready": true, "configured_backend": "vllm_api", "vllm_model": "qwen3:32b" }
```

---

### `POST /api/ai/reconnect`

Re-initialize LLM connection (call after Ollama restart without restarting backend).

---

### `GET /api/ai/examples`

Returns example questions for the chat UI.

---

### `GET /api/ai/schema`

Returns schema context injected into LLM prompts.

---

### `GET /api/core/stats`

Database statistics (row count, gene count, classification breakdown).

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SQLALCHEMY_DATABASE_URL` | `sqlite:///patient_variants.db` | SQLite database path |
| `LLM_BACKEND` | `vllm_api` | `vllm_api` / `hf_api` / `hf_local` / `mock` |
| `VLLM_API_URL` | `http://localhost:11434` | Ollama base URL |
| `VLLM_MODEL` | `qwen3:32b` | Ollama model name |
| `HF_TOKEN` | (unset) | HuggingFace token for `hf_api` |
| `LLM_TIMEOUT` | `45` | LLM request timeout (seconds) |

---

## External APIs

| API | Status | Trigger | Returns |
|-----|--------|---------|---------|
| **ClinVar** (NCBI eutils) | ✅ Active | rsID queries | Clinical significance, review status, ClinVar URL |
| **PubMed** (NCBI eutils) | ✅ Active | Any gene/rsID query | Top 3–5 publications with title, journal, PubMed URL |
| **ClinGen Gene-Disease** | ⚠️ Shell ready | Not yet wired | Expert panel curations (Definitive/Strong/Moderate/Limited) |

ClinVar and PubMed fire as supplements when variants are found locally, or as fallback when 0 local rows are returned.

---

## HPO Symptom Resolution

Maps plain-English symptoms to candidate disease genes using the Human Phenotype Ontology.

### Flow

```
"I have muscle weakness and seizures"
         │
         ▼
_extract_symptom_phrases()
  → ["muscle weakness", "seizures"]
         │
         ▼
resolve_many(phrases)          # app/hpo/resolver.py
  maps to HPO terms
  → HP:0003324 (muscle weakness) → 1,247 genes
  → HP:0001250 (seizures)        →   891 genes
         │
         ▼
rank_and_cap_genes(resolved, cap=300)
  specificity-ranked gene pool
  → ["SCN1A", "KCNQ2", "ALDH7A1", ...]
         │
         ▼
HPO SQL built and executed:
  WHERE "Ref.Gene" IN (<300 genes>)
    AND (ClinVar Pathogenic OR InterVar Pathogenic)
  ORDER BY CADD_phred DESC LIMIT 50
```

### HPO Data Files

| File | Description |
|------|-------------|
| `app/hpo/data/hpo_terms.tsv.gz` | HPO ID → term name + synonyms |
| `app/hpo/data/hpo_to_genes.tsv.gz` | HPO ID → HGNC gene symbols |

---

## Gene Disease Enricher

`app/ai/gene_enricher.py` prevents the LLM from hallucinating disease names.

### Problem

Without enrichment: LLM uses training-memory associations (often wrong, outdated, or for a different disease subtype).  
With enrichment: LLM uses the `_diseases` field populated exclusively from the patient's own DB columns.

### Priority Chain

```python
enrich_rows_with_diseases(rows, db)
```

For each unique gene in the result set:

| Step | Source | Example output |
|------|--------|----------------|
| 1 | Row `Orpha` column | `"Alpha-L-iduronidase deficiency; Mucopolysaccharidosis type I"` |
| 2 | Cross-row DB lookup (`Orpha` from any row of same gene) | Critical for pathogenic rows that have `NULL` Orpha |
| 3 | Row `Phenotype_MIM` column | `"OMIM phenotype(s): 607948;612278"` |
| 4 | Row `OMIM` column | `"OMIM gene: 606755"` |
| 5 | HPO gene API (cached) | `"Mucopolysaccharidosis type I; Scheie syndrome"` |
| 6 | Fallback | `"No disease association in database"` |

The `_diseases` field is added to each row dict and appears in the LLM explain prompt. The system prompt Rule 5 instructs the LLM: **"NEVER use training memory for gene-disease links."**

---

## Deployment (Remote Server)

| Component | Details |
|-----------|---------|
| Server | Ubuntu, `ubuntu@<server-ip>` |
| Conda env | `/ephemeral/conda_envs/intervar/` |
| Working dir | `/home/ubuntu/intervar/` |
| Backend | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| Gradio | `python gradio_app.py` (port 7860) |
| LLM | Ollama `qwen3:32b` on port 11434 |
| Database | `/home/ubuntu/intervar/patient_variants.db` |

### Deploy updated files

```bash
scp -i ubuntu_.pem app/ai/gene_enricher.py  ubuntu@<server>:/home/ubuntu/intervar/app/ai/
scp -i ubuntu_.pem app/ai/llm_config.py     ubuntu@<server>:/home/ubuntu/intervar/app/ai/
scp -i ubuntu_.pem app/ai/text_to_sql.py    ubuntu@<server>:/home/ubuntu/intervar/app/ai/
scp -i ubuntu_.pem app/ai/schema_injector.py ubuntu@<server>:/home/ubuntu/intervar/app/ai/
scp -i ubuntu_.pem app/api/ai_endpoints.py  ubuntu@<server>:/home/ubuntu/intervar/app/api/
```

### Restart backend

```bash
ssh -i ubuntu_.pem ubuntu@<server>
kill $(pgrep -u ubuntu -f 'uvicorn main:app')
cd /home/ubuntu/intervar
nohup /ephemeral/conda_envs/intervar/bin/python -m uvicorn main:app \
    --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 &

# Reconnect LLM
curl -X POST http://localhost:8000/api/ai/reconnect
```

### Verify

```bash
curl http://localhost:8000/api/ai/status
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "show pathogenic variants"}'
```

---

## Example Queries

| Query | Intent | Behaviour |
|-------|--------|-----------|
| `Show pathogenic variants` | data_query | ClinVar Pathogenic pattern SQL |
| `Show likely pathogenic variants` | data_query | ClinVar Likely_pathogenic pattern SQL |
| `What are my disease causing variants?` | data_query | `_PATIENT_REPORT` → ClinVar + InterVar filter |
| `List pathogenic genes` | data_query | Distinct genes with Pathogenic variants |
| `Classification tier breakdown with gene count` | data_query | CASE WHEN tier → COUNT(DISTINCT gene), COUNT(*) |
| `Average CADD score for stopgain vs synonymous` | data_query | GROUP BY exonic_func, AVG(CADD_phred) |
| `Show in-frame deletions not in repeat regions` | data_query | nonframeshift + repeat_masker filter |
| `I have muscle weakness and seizures` | hpo_query | HPO → gene pool → DB pathogenic filter |
| `Look up rs80357906` | data_query | rsID lookup + ClinVar external API |
| `What is PVS1?` | general | Static KB answer |
| `Explain CADD score` | general | LLM general answer |

---

## License

For educational and research use only.  
All genomic interpretation results must be reviewed by a certified genetic counselor or physician before any clinical decision.
