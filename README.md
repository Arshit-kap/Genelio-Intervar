# Genelio InterVar — MedGemma-27B Backend

> **Genomic Variant Q&A Engine powered by Google MedGemma-27B (multimodal medical LLM)**

This branch (`interval_gemma`) runs the backend with **MedGemma-27B** as the language model, accessed via a vLLM-served API endpoint. The engine interprets WGS/WES genomic variant reports in natural language, applying ACMG/AMP 2015 criteria and querying a 76,330-variant InterVar-annotated SQLite database.

---

## Architecture

```
User → FastAPI (port 8000) → Intent Router → SQL Executor → MedGemma-27B → Formatted Answer
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
- **Backend server**: Ubuntu 20.04+, 16 GB RAM minimum, Python 3.10+
- **MedGemma server**: Separate GPU server running MedGemma-27B via vLLM
- **SSH access**: PEM keys for both servers (`ubuntu_.pem` and `ubuntu_01.pem`)
- **autossh**: For persistent tunnel to MedGemma server

### Software
```bash
# Conda environment (recommended)
conda create -n intervar python=3.10 -y
conda activate intervar

# Install autossh (Ubuntu)
sudo apt-get install -y autossh
```

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/Arshit-kap/Genelio-Intervar.git
cd Genelio-Intervar
git checkout interval_gemma
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
cp .env.gemma.example .env
# Edit .env — set VLLM_API_URL to your MedGemma endpoint
```

The `.env` file for MedGemma:
```env
LLM_BACKEND=vllm_api
VLLM_API_URL=http://localhost:8030
VLLM_MODEL=medgemma-27b
SQLALCHEMY_DATABASE_URL=sqlite:///patient_variants.db
HF_HOME=/path/to/hf_cache
```

---

## MedGemma Tunnel Setup

MedGemma-27B is served on a separate GPU server (`213.181.122.239`) via vLLM.  
Access it by creating a persistent SSH tunnel from the backend server:

### One-time setup
```bash
# Copy your MedGemma PEM key to the backend server
scp -i ubuntu_.pem ubuntu_01.pem ubuntu@<backend-server>:/home/ubuntu/.ssh/ubuntu_01.pem
chmod 600 /home/ubuntu/.ssh/ubuntu_01.pem
```

### Start the tunnel (autossh — auto-reconnects on failure)
```bash
./tunnel_medgemma.sh &
```

This opens three port-forwards on `localhost`:
| Local port | Remote port | Service |
|-----------|-------------|---------|
| 8010 | 8010 | vLLM API (alt) |
| 8020 | 8020 | vLLM API (alt) |
| 8030 | 8030 | MedGemma-27B API (primary) |

Verify the tunnel is active:
```bash
ss -tlnp | grep 8030
# Should show: 127.0.0.1:8030
```

---

## Starting the Server

### Manual start
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Production start (with logging)
```bash
./start_port8000.sh
```

### Start everything (tunnel + server)
```bash
./start_all.sh
```

The server will be available at:
- API: `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## API Reference

### Primary endpoints

#### `POST /api/ai/chat`
Main conversational interface. Send a natural language question and receive an interpreted answer.

```bash
curl -X POST http://localhost:8000/api/ai/chat \
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
curl -X POST http://localhost:8000/api/ai/query \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "P8000",
    "question": "Show me stopgain variants with CADD score above 25"
  }'
```

#### `GET /api/ai/status`
Check LLM backend connectivity.

```bash
curl http://localhost:8000/api/ai/status
```

#### `POST /api/variants/search`
Direct variant filter (no LLM needed):

```bash
curl -X POST http://localhost:8000/api/variants/search \
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

## Testing

Run the full category test suite (24 questions across 6 categories):

```bash
# MedGemma backend on port 8000
python test_categories.py --port 8000

# Spot-check specific questions
python test_spot.py --port 8000

# 12-question quick test
python test_12q.py --port 8000
```

Expected: **24/24 PASS** on both ports.

---

## Project Structure

```
├── main.py                          # FastAPI entry point
├── requirements.txt                 # Python dependencies
├── .env.gemma.example               # MedGemma environment template
├── start_all.sh                     # Start tunnel + both servers
├── start_port8000.sh                # Start MedGemma server (port 8000)
├── tunnel_medgemma.sh               # autossh tunnel script
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

**MedGemma-27B** — Google's medical large language model
- **Size**: 27 billion parameters
- **Specialization**: Biomedical and clinical text
- **Serving**: vLLM on dedicated GPU server
- **Access**: SSH tunnel forwarding port 8030

MedGemma is particularly suited for this application due to:
- Pre-training on medical literature and clinical records
- Understanding of ACMG criteria, variant pathogenicity, and genomic nomenclature
- Ability to produce appropriately cautious clinical language
- Strong performance on genomic Q&A benchmarks

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

- `interval_qwen` branch — Same backend with **Qwen3-32B** (Ollama) as the LLM
- InterVar: [https://github.com/WGLab/InterVar](https://github.com/WGLab/InterVar)
- ACMG 2015 Standards: [https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4544753/](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4544753/)
- MedGemma: [https://ai.google.dev/gemma/docs/medgemma](https://ai.google.dev/gemma/docs/medgemma)
