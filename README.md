# Genelio — Genomic Variant Q&A Engine

An AI-powered genomic variant interpretation platform built on InterVar. Ask natural language questions about patient variant reports and get plain-English answers, powered by Qwen3-32B and a 76,000-variant SQLite database.

---

## What it does

- **Natural language Q&A** — "What are the disease-causing variants in my report?" → runs SQL, explains results in plain English
- **Text-to-SQL** — hybrid pattern matching + Qwen3-32B LLM fallback, no hallucination on known queries
- **Patient-facing explanations** — translates genomic jargon (PVS1, gnomAD AF, CADD) into readable summaries
- **External evidence** — fetches ClinVar and PubMed records for specific variants/genes
- **ACMG/AMP 2015** — automated variant interpretation engine with all 28 evidence criteria
- **Gradio chat UI** — dark-mode chat interface backed by the FastAPI REST API
- **FastAPI REST** — full Swagger docs at `/docs`, structured JSON endpoints for every query type

---

## Architecture

```
gradio_app.py            ← Gradio chat frontend  (port 7860)
main.py                  ← FastAPI app           (port 8000)
├── app/api/
│   ├── ai_endpoints.py  ← /api/ai/chat, /api/ai/query, /api/ai/status
│   ├── core_endpoints.py← /api/variants/search, /api/health
│   ├── acmg_endpoints.py← /api/acmg/interpret
│   └── evidence_endpoints.py ← /api/evidence (ClinVar, PubMed)
├── app/ai/
│   ├── text_to_sql.py   ← hybrid pattern SQL + LLM pipeline
│   ├── llm_config.py    ← Ollama/vLLM/HuggingFace client + explain()
│   └── schema_injector.py ← DB schema + few-shot examples for LLM
├── app/acmg/
│   ├── classifier.py    ← ACMG/AMP 2015 rule engine
│   └── evaluator.py     ← Evidence evaluator
├── app/external/
│   ├── clinvar_client.py
│   ├── pubmed_client.py
│   └── clingen_client.py
├── app/ingestion/       ← InterVar flat-file → SQLite pipeline
├── app/models.py        ← SQLAlchemy ORM models
├── app/database.py      ← DB session setup
└── app/config.py        ← Environment variable config

ingest_new_db.py         ← One-time DB ingestion from InterVar .txt file
add_indexes.py           ← Create SQL indexes for fast queries
db/schema.sql            ← Raw SQL schema reference
```

---

## Database

- **Engine:** SQLite (file: `patient_variants.db`)
- **Rows:** 76,330 variants in the current patient report
- **Format:** InterVar flat-file (34 annotated columns per variant)
- **Key columns:** `"Ref.Gene"`, `"ExonicFunc.refGene"`, `"InterVar: InterVar and Evidence"`, `"clinvar: Clinvar"`, `Freq_gnomAD_genome_ALL`, `CADD_phred`, `Otherinfo`

The database is **not** stored in the repo (binary, patient data). Use `ingest_new_db.py` to build it from an InterVar output file.

---

## Quick Start

### Prerequisites

- Python 3.9+
- [Ollama](https://ollama.ai) with `qwen3:32b` pulled (for AI features), **or** a HuggingFace token
- SQLite (built into Python — no install needed)

### 1. Clone and install

```bash
git clone https://github.com/Arshit-kap/Genelio-Intervar.git
cd Genelio-Intervar
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required — path to the SQLite patient database
SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db

# LLM backend — choose one:

# Option A: Ollama (recommended for local GPU, 32B model)
LLM_BACKEND=vllm_api
VLLM_API_URL=http://localhost:11434
VLLM_MODEL=qwen3:32b

# Option B: HuggingFace Inference API (free tier, no GPU needed)
# LLM_BACKEND=hf_api
# HF_TOKEN=hf_your_token_here

# Option C: No LLM (pattern SQL only — no explanations)
# LLM_BACKEND=mock
```

### 3. Build the database

Place your InterVar output file (e.g. `patient_variants.txt`) in the project root, then:

```bash
python ingest_new_db.py --input patient_variants.txt --db patient_variants.db
python add_indexes.py
```

Expected output: `Ingested N variants. Indexes created.`

### 4. Start the backend

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Check it's working: `http://localhost:8000/api/health`  
API docs (Swagger): `http://localhost:8000/docs`

### 5. Start the Gradio UI

In a second terminal:

```bash
python gradio_app.py
```

Open `http://localhost:7860`

---

## Running on a Remote GPU Server

The production deployment uses an Ubuntu server with Ollama serving Qwen3-32B.

### Server-side setup

```bash
# Pull the model (one-time, ~20GB)
ollama pull qwen3:32b

# Start Ollama (if not already running as a service)
ollama serve &

# Start the backend inside your conda environment
conda activate intervar
export LLM_BACKEND=vllm_api
export VLLM_API_URL=http://localhost:11434
export VLLM_MODEL=qwen3:32b
export SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db
uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info
```

### SSH tunnel for local access

```bash
# From your local machine — forwards remote port 8000 to localhost:8000
ssh -N -L 8000:localhost:8000 ubuntu@YOUR_SERVER_IP
```

Then use `http://localhost:8000` as normal.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Server + DB health check |
| `POST` | `/api/ai/chat` | Conversational interface (NL + history) |
| `POST` | `/api/ai/query` | Text-to-SQL (structured JSON output) |
| `GET` | `/api/ai/status` | LLM backend status |
| `POST` | `/api/ai/reconnect` | Reconnect to Ollama if LLM dropped |
| `GET` | `/api/ai/examples` | Example questions |
| `POST` | `/api/variants/search` | Direct variant filter (gene, chr, pos) |
| `GET` | `/api/evidence/{id}` | ClinVar + PubMed for a variant |
| `GET` | `/api/acmg/interpret/{id}` | ACMG/AMP 2015 classification |

### Example — chat query

```bash
curl -X POST http://localhost:8000/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the disease-causing variants in my report?"}'
```

### Example — structured query

```bash
curl -X POST http://localhost:8000/api/ai/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Show missense variants with CADD score above 30"}'
```

---

## Example Questions

**Data queries (hit the SQLite DB):**
- `"How many variants are in the report?"`
- `"How many types of variants are there?"`
- `"What are the disease-causing variants in the report?"`
- `"Find variants in DMD, TP53 and BRCA1"`
- `"Which variants are Pathogenic in ClinVar but have gnomAD frequency > 1%?"`
- `"Show missense variants with CADD score above 30"`
- `"What is the average CADD score for stopgain vs synonymous variants?"`
- `"Is BRCA1 in my report?"`
- `"Look up rs80357906"`

**General genomics questions (LLM knowledge):**
- `"What is PVS1?"`
- `"Explain CADD scores"`
- `"What does VUS mean?"`
- `"What is gnomAD allele frequency?"`
- `"What is a missense variant?"`

---

## Project File Guide

| File | Purpose |
|------|---------|
| `main.py` | FastAPI entry point — mounts all routers |
| `gradio_app.py` | Gradio chat frontend |
| `ingest_new_db.py` | **Run once** — loads InterVar .txt file into SQLite |
| `add_indexes.py` | Creates SQL indexes after ingestion |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |
| `app/ai/text_to_sql.py` | Core NL→SQL engine (pattern + LLM hybrid) |
| `app/ai/llm_config.py` | LLM client (Ollama/HF/mock), `explain()` function |
| `app/ai/schema_injector.py` | DB schema + few-shot examples injected into LLM prompts |
| `app/api/ai_endpoints.py` | `/api/ai/*` endpoints + intent classifier |
| `app/api/core_endpoints.py` | `/api/variants/*` direct search endpoints |
| `app/api/acmg_endpoints.py` | ACMG/AMP 2015 interpretation endpoints |
| `app/api/evidence_endpoints.py` | ClinVar, PubMed, ClinGen external API endpoints |
| `app/acmg/classifier.py` | ACMG rule engine (PVS1, PS, PM, PP, BA1, BS, BP) |
| `app/external/clinvar_client.py` | ClinVar NCBI eUtils client |
| `app/external/pubmed_client.py` | PubMed eUtils client |
| `app/models.py` | SQLAlchemy ORM model for the `variants` table |
| `app/database.py` | DB session factory |
| `app/config.py` | Env var config (DATABASE_URL, LLM_BACKEND, etc.) |
| `db/schema.sql` | Raw SQL DDL for reference |
| `scripts/` | Utility scripts for ingestion verification |

---

## LLM Backend Options

| Backend | Config | Notes |
|---------|--------|-------|
| **Ollama + Qwen3-32B** | `LLM_BACKEND=vllm_api`, `VLLM_API_URL=http://localhost:11434` | Best quality, needs GPU (~40GB VRAM) |
| **Ollama + smaller** | `VLLM_MODEL=qwen3:8b` | Needs ~12GB VRAM, still good |
| **HuggingFace API** | `LLM_BACKEND=hf_api`, `HF_TOKEN=hf_...` | Free tier, no GPU, ~1000 req/day |
| **No LLM** | `LLM_BACKEND=mock` | Pattern SQL only, no explanations |

For Ollama installation: https://ollama.ai/download

---

## InterVar Column Reference

The database uses the original InterVar column names (with dots and spaces) — they must be double-quoted in SQL:

| Column | Meaning |
|--------|---------|
| `"Ref.Gene"` | Gene symbol (e.g. BRCA1) |
| `"ExonicFunc.refGene"` | Variant type (missense SNV, stopgain, frameshift…) |
| `"InterVar: InterVar and Evidence"` | ACMG classification + evidence codes |
| `"clinvar: Clinvar"` | ClinVar significance |
| `Freq_gnomAD_genome_ALL` | gnomAD population frequency (0–1) |
| `CADD_phred` | CADD damage score (>20 damaging, >30 highly damaging) |
| `Otherinfo` | Zygosity: `het` / `hom` / `hemi` |
| `Chr`, `Start`, `Ref`, `Alt` | Genomic coordinates |
| `avsnp147` | dbSNP rsID |
| `Orpha`, `OMIM` | Disease associations |

---

## Troubleshooting

**LLM not connecting:**
```bash
# Check Ollama is running
curl http://localhost:11434/v1/models
# Reconnect without restart
curl -X POST http://localhost:8000/api/ai/reconnect
```

**Database not found:**
```bash
# Run ingestion first
python ingest_new_db.py --input your_file.txt --db patient_variants.db
```

**Server not responding after restart:**
```bash
# Check uvicorn is running
pgrep -f 'uvicorn main:app'
# Check backend log
tail -f backend.log
```

**Reasoning text appearing in responses:**  
This is fixed by `extra_body={"think": False}` in `llm_config.py`. If it reappears, ensure the Ollama version supports Qwen3 think mode (`ollama --version`).

---

## References

- [InterVar](http://intervar.org/) — ACMG/AMP 2015 variant interpretation tool
- [ACMG/AMP 2015 Guidelines](https://pubmed.ncbi.nlm.nih.gov/25741868/)
- [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/)
- [gnomAD](https://gnomad.broadinstitute.org/)
- [Qwen3 / Ollama](https://ollama.ai/)

---

## License

For genomic research and educational purposes only. Variant interpretations are not medical advice.
