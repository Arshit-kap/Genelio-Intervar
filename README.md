# Genelio InterVar — Qwen3-32B Backend

> **Genomic Variant Q&A Engine powered by Qwen3-32B via Ollama**

This branch (`interval_qwen`) runs the backend with **Qwen3-32B** as the language model, served locally via [Ollama](https://ollama.com/). The engine interprets WGS/WES genomic variant reports in natural language, applying ACMG/AMP 2015 criteria and querying a 76,330-variant InterVar-annotated SQLite database.

---

## Architecture

```
User → FastAPI (port 8001) → Intent Router → SQL Executor → Qwen3-32B (Ollama) → Formatted Answer
                                    ↓
                              HPO Resolver ← OMIM / HPO ontology
                                    ↓
                          InterVar SQLite DB (76,330 variants)
                                    ↓
                     External APIs: ClinVar · PubMed · ClinGen
```

### Key Components

| Module | Path | Purpose |
|--------|------|---------|
| Intent Router | `app/ai/intervar_router.py` | 8-intent classifier + SQL executor + KB |
| HPO Resolver | `app/hpo/resolver.py` | Symptom → HP term mapping |
| Gene Enricher | `app/ai/gene_enricher.py` | Gene name → disease/pathway annotation |
| LLM Config | `app/ai/llm_config.py` | Backend abstraction (vLLM / Ollama / HF) |
| Schema Injector | `app/ai/schema_injector.py` | DB schema + examples injected into LLM prompt |
| Answer Validator | `app/ai/validator.py` | HGVS + safety tag post-processing |
| Compat Layer | `app/api/compat_endpoints.py` | Genelio Next.js frontend compatibility |

### 8-Intent Classification

| Intent | Trigger | SQL Path |
|--------|---------|----------|
| `coord_lookup` | chr/position query | Exact coordinate lookup |
| `biofilter` | gene name / variant type | Filtered variant search |
| `acmg_clinvar` | pathogenic / dangerous / harmful | ClinVar + InterVar filter |
| `disease_link` | disease / condition name | OMIM / Orpha / gene link search |
| `aggregate` | count / average / top N | GROUP BY aggregate SQL |
| `hpo_symptom` | symptom / clinical feature | HPO → gene overlap query |
| `schema_lookup` | what is X / explain | Knowledge base lookup (no SQL) |
| `summary` | carrier / overview / VUS count | Full-table summary query |

---

## Prerequisites

### Server Requirements
- **Backend server**: Ubuntu 20.04+, 32 GB RAM recommended (Qwen3-32B needs GPU memory), Python 3.10+
- **GPU**: Minimum 24 GB VRAM for Qwen3-32B in float16; use 16 GB VRAM with quantization (q4)
- **Ollama**: Installed locally — serves Qwen3-32B on port 11434

### Software
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull Qwen3-32B model (~20 GB download)
ollama pull qwen3:32b

# Verify
ollama list
# should show: qwen3:32b

# Conda environment (recommended)
conda create -n intervar python=3.10 -y
conda activate intervar
```

> **Low-VRAM option**: Use `qwen3:14b` or `qwen3:8b` for faster inference on smaller GPUs.  
> Update `VLLM_MODEL=qwen3:14b` in your `.env` accordingly.

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/Arshit-kap/Genelio-Intervar.git
cd Genelio-Intervar
git checkout interval_qwen
```

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Set up the database
Place `patient_variants.db` (SQLite, ~500 MB) in the project root.  
The database must contain a `variants` table with 76,330 InterVar-annotated WGS variants.

> **Note**: The database file is not included in the repository (`.gitignore`).  
> Contact the project maintainer to obtain the pre-populated database.

### 4. Configure environment
```bash
cp .env.qwen.example .env
```

The `.env` file for Qwen3:
```env
LLM_BACKEND=vllm_api
VLLM_API_URL=http://localhost:11434
VLLM_MODEL=qwen3:32b
SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db
```

---

## Starting Ollama

Ollama must be running before starting the FastAPI server:

```bash
# Start Ollama service (runs in background on port 11434)
ollama serve &

# Verify it's running
curl http://localhost:11434/api/tags
# Should list qwen3:32b in the models

# Test a quick generation (optional)
ollama run qwen3:32b "What is CADD score?" --verbose
```

---

## Starting the Server

### Manual start
```bash
# Export Qwen3 env vars (overrides .env)
export LLM_BACKEND=vllm_api
export VLLM_API_URL=http://localhost:11434
export VLLM_MODEL=qwen3:32b

uvicorn main:app --host 0.0.0.0 --port 8001
```

### Production start (with logging)
```bash
./start_port8001.sh
```

The server will be available at:
- API: `http://localhost:8001`
- Swagger docs: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`

---

## API Reference

### Primary endpoints

#### `POST /api/ai/chat`
Main conversational interface. Send a natural language question and receive an interpreted answer.

```bash
curl -X POST http://localhost:8001/api/ai/chat \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P8000",
    "message": "Do I have any pathogenic variants?",
    "session_id": "session_001"
  }'
```

**Response:**
```json
{
  "reply": "Your report contains 4 variants classified as Pathogenic by ClinVar...",
  "intent": "acmg_clinvar",
  "rows_found": 4,
  "session_id": "session_001"
}
```

#### `POST /api/ai/query`
Structured natural language → SQL query with full metadata.

```bash
curl -X POST http://localhost:8001/api/ai/query \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P8000",
    "question": "Show me stopgain variants with CADD score above 25"
  }'
```

#### `GET /api/ai/status`
Check LLM backend connectivity.

```bash
curl http://localhost:8001/api/ai/status
```

#### `POST /api/variants/search`
Direct variant filter (no LLM needed):

```bash
curl -X POST http://localhost:8001/api/variants/search \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P8000",
    "gene": "BRCA1",
    "limit": 20
  }'
```

### ACMG Interpretation

#### `GET /api/acmg/interpret/{variant_id}`
Full ACMG/AMP 2015 classification for a variant.

#### `POST /api/acmg/batch-interpret`
Batch classify up to 50 variants.

### External Evidence

#### `GET /api/evidence/{variant_id}`
Combined ClinVar + PubMed + ClinGen evidence.

#### `GET /api/evidence/search/pubmed?query=BRCA1+pathogenic`
Free PubMed literature search.

---

## Example Queries

The following questions are all handled by the natural language chat endpoint:

### Variant classification
```
"Are any of my variants harmful?"
"Do I have pathogenic variants?"
"Show me my dangerous mutations"
"What is actionable in my report?"
```

### ACMG criteria
```
"What is a CADD score?"
"What does VUS mean?"
"Explain BA1"
"What is PVS1?"
"What does heterozygous mean?"
```

### Disease links
```
"Do I have any variants related to lung disease?"
"Anything related to my heart?"
"What conditions are linked to BRCA1?"
```

### Symptom-based (HPO)
```
"I have weak muscles and trouble seeing at night — what could it be?"
"Are there kidney-related issues in my genes?"
```

### Comparisons & conflicts
```
"ClinVar says VUS but InterVar says pathogenic — why?"
"What variants have conflicting classifications?"
```

### Carrier status
```
"Am I a carrier for any recessive conditions?"
"Are there incidental findings in my report?"
"Will my children inherit this?"
```

---

## Output Format

Each variant record in the response includes:

```
🧬 **GENE_NAME** · HGVS_notation · ExonicFunc
   Evidence tier: [Pathogenic/Likely pathogenic/VUS/Benign]
   chrN:position REF>ALT
   ClinVar: [classification]
   InterVar: [full InterVar string — first 70 chars]
   Zygosity: [het/hom]
   rsID: [if available]
   CADD: [Phred score]
   SIFT: [score]
   gnomAD AF: [allele frequency]
   ACMG evidence fired: [PVS1, PM, PP, ...]
   Disease links: OMIM:123456 · Orpha:789
```

Header for each response:
```
MATCHED RECORDS — N match(es) (showing top K ranked by InterVar verdict then ClinVar then CADD, universe: 76,330 variants).
Filter applied: [description of the SQL filter used]
```

---

## Performance Notes

Qwen3-32B via Ollama has **longer response times** than API-based models:

| Operation | Typical latency |
|-----------|----------------|
| Schema lookup (no SQL) | 5–15 sec |
| Pattern SQL (indexed query) | 8–20 sec |
| LLM-generated SQL | 20–90 sec |
| Summary query (76K rows) | 10–30 sec |

To mitigate timeouts for the frontend:
- Increase client-side timeout to **120 seconds**
- Use `/api/ai/chat` with streaming if available
- Most intents are handled by pattern SQL (fast path) — LLM SQL is only used for complex/novel queries

---

## Testing

Run the full category test suite (24 questions across 6 categories):

```bash
# Qwen3 backend on port 8001
python test_categories.py --port 8001

# Spot-check specific questions
python test_spot.py --port 8001

# 12-question quick test
python test_12q.py --port 8001
```

Expected: **24/24 PASS** on both ports.

---

## Project Structure

```
├── main.py                          # FastAPI entry point
├── requirements.txt                 # Python dependencies
├── .env.qwen.example                # Qwen3 environment template
├── start_port8001.sh                # Start Qwen3 server (port 8001)
├── app/
│   ├── ai/
│   │   ├── intervar_router.py       # Core: intent router + SQL executor + KB
│   │   ├── llm_config.py            # LLM backend abstraction
│   │   ├── schema_injector.py       # DB schema + example injection
│   │   ├── text_to_sql.py           # Pattern SQL + LLM fallback
│   │   ├── gene_enricher.py         # Gene → disease annotation
│   │   ├── validator.py             # HGVS + safety post-processing
│   │   └── pathogenicity.py         # Pathogenicity scoring
│   ├── api/
│   │   ├── ai_endpoints.py          # /api/ai/* routes
│   │   ├── core_endpoints.py        # /api/variants/*, /api/metadata/*
│   │   ├── acmg_endpoints.py        # /api/acmg/*
│   │   ├── evidence_endpoints.py    # /api/evidence/*
│   │   └── compat_endpoints.py      # Genelio Next.js compatibility
│   ├── hpo/
│   │   ├── resolver.py              # HPO term resolution
│   │   └── api_client.py            # HPO API client
│   ├── acmg/
│   │   ├── evaluator.py             # ACMG 2015 evidence evaluator
│   │   └── classifier.py            # ACMG classification engine
│   ├── external/
│   │   ├── clinvar_client.py        # ClinVar REST API client
│   │   ├── pubmed_client.py         # PubMed E-utilities client
│   │   └── clingen_client.py        # ClinGen Evidence Repo client
│   ├── database.py                  # SQLAlchemy session factory
│   ├── models.py                    # ORM models
│   └── config.py                    # Env-var configuration
├── test_categories.py               # Full 24-question category tests
├── test_spot.py                     # Spot-check tests
└── test_12q.py                      # 12-question quick test
```

---

## Model Information

**Qwen3-32B** — Alibaba's open-source large language model
- **Size**: 32 billion parameters
- **Context window**: 32,768 tokens
- **Specialization**: Code, reasoning, instruction following
- **Serving**: Ollama (local inference, no cloud dependency)
- **License**: Open source (Apache 2.0)

Qwen3-32B advantages for this application:
- Strong SQL generation capability — accurately translates genomic questions to SQL
- `/no_think` directive supported — reduces verbosity and improves response speed
- Runs entirely on-premises — no data leaves your server
- Excellent reasoning for complex multi-criteria variant queries

---

## Safety & Disclaimer

> **This system is for educational and research use only.**
>
> The variant interpretations provided by this system are **not medical advice** and should not be used for clinical decision-making without review by a certified genetic counselor or clinical geneticist.
>
> - Classifications may differ between ClinVar submissions and InterVar automated scoring
> - VUS classifications are uncertain and subject to reclassification
> - Always consult a healthcare professional for medical decisions

---

## Related

- `interval_gemma` branch — Same backend with **MedGemma-27B** (vLLM) as the LLM
- InterVar: [https://github.com/WGLab/InterVar](https://github.com/WGLab/InterVar)
- ACMG 2015 Standards: [https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4544753/](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4544753/)
- Qwen3: [https://huggingface.co/Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B)
- Ollama: [https://ollama.com/](https://ollama.com/)
