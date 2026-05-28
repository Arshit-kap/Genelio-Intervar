# Genelio Backend — Project Context

This file is consumed by Claude Code (and similar AI coding assistants) when
working in this repo. Keep it short, high-signal, and fact-checked against the
actual code — not aspirational.

## What this repo is

Django 5.1 + DRF 3.15 backend for Genelio, an AI health-coaching app that lets
users upload genomic / microbiome PDF reports and chat with a Retrieval-
Augmented LLM grounded in their report. Production lives at
`https://api.genelio.com`; the Next.js frontend at `https://genelio.com` is in
a separate repo (`KWP-inc/genelio_frontend`).

## Stack

- **Web:** Django 5.1.3 + djangorestframework 3.15.2 + simplejwt 5.3.1 +
  django-cors-headers + django-filter + drf-spectacular (OpenAPI/Swagger).
- **Auth:** JWT (`/auth/jwt/create/`, `/auth/jwt/refresh/`), djoser-shaped
  user surface at `/auth/users/...`. Custom user model: `accounts.User`
  (email-as-username).
- **DB:** Postgres 16+ (prod runs `pgvector/pgvector:pg17` in Docker on the
  same box as Django; connection via `localhost:5432`). SQLite is fine for
  tests (`DATABASE_URL=sqlite:///./test.sqlite3`).
- **LLM:** vLLM-served chat model (default `qwen3-30b` at
  `http://localhost:8011/v1`). Speaks the OpenAI-compatible API via the
  `openai` Python SDK.
- **RAG:** Chroma persistent client + `nomic-ai/nomic-embed-text-v1.5`
  embedder via `sentence-transformers`. Chroma store lives at
  `media/chroma/` per `CHROMA_PERSIST_DIR`. Nomic's modeling code requires
  `einops` (in `requirements.txt`; trips first-chat-after-deploy if missing).
- **PDF parsing:** `pdfplumber` (no `unstructured` / `PyMuPDF`).

## Apps

| App        | What it does                                                     |
|------------|------------------------------------------------------------------|
| `accounts` | Custom `User` model, email login, JWT views, profile views.      |
| `reports`  | Per-user report uploads. One Report row per uploaded PDF.        |
| `chatbot`  | Chat sessions + messages; the RAG/LLM pipeline; review capture.  |

### `reports/analyzers/` — registry-dispatched parsers

Per-report-type modules each expose `analyze(pdf_path) -> dict`. Output shape
is uniform across all types so `chatbot.pipeline` can treat them the same:

```python
{
    "status": "ok" | "stub",
    "site":   "wes" | "wgs" | "gut" | "oral" | "skin" | "vaginal" | None,
    "report": { ... structured payload ... },
    "analysis_context": "<plain-text grounding for the LLM>",
    "structured_summary": "<optional, microbiome only>",
}
```

Registry lives in `reports/analyzers/__init__.py`:

- `_REGISTRY: dict[report_type, callable]` — actual dispatch table.
- `IMPLEMENTED_TYPES: frozenset[str]` — drives the `implemented: true` flag
  on `GET /chatbot/report/types/`. Add a type here when its analyzer is real.

Real analyzers today: `gut`, `oral`, `skin`, `vaginal`, `wes`, `wgs`. The
genomic ones (`wes.py`, `wgs.py`) are thin wrappers over
`reports/analyzers/_genomic.py::analyze_genomic(pdf, site=...)`, which is the
shared BioAro WES/WGS template parser (pdfplumber-based; structured analysis
+ variant table + ACMG classifications + per-gene narrative).

### `chatbot/pipeline.py` — the chat brain

Single entry point: `generate_reply(session, user_message) -> str`.

Routing logic, in order:
1. **No report attached** → graceful stub ("No report is attached…").
2. **Report attached, type ∈ `_RAG_ENABLED_TYPES`** → embed the PDF text
   into Chroma (one collection per report, lazy-built on first query),
   retrieve top-k chunks, format them into the system prompt, call vLLM.
   `_RAG_ENABLED_TYPES` includes all 6 report types as of `stable`.
3. **Report attached, type ∉ `_RAG_ENABLED_TYPES`** → "Support coming soon".

Two prompt templates: `_MICROBIOME_SYSTEM_PROMPT_TEMPLATE` (abundance ranges,
diversity scores, dysbiosis) vs `_GENOMIC_SYSTEM_PROMPT_TEMPLATE` (variants,
ACMG classifications, zygosity, mode of inheritance). `_GENOMIC_TYPES =
{wes, wgs}` controls which template a chat gets.

Heavy ML deps (`sentence_transformers`, `chromadb`) are **lazy-imported
inside `_embedder()` / `_chroma_client()`** — Django boot/test runners don't
need them, only the first chat query does.

### `chatbot/models.py::MessageReview`

Captures human verdicts on assistant replies (correct / partial / incorrect
+ free-text notes + optional corrected response). Used as the source of
fine-tuning training data. Migration: `chatbot/migrations/0002_messagereview.py`.
Currently has no DRF view/serializer — the Gradio reviewer prototype writes
to it directly via the Django ORM.

## URL surface

```
/admin/                           Django admin
/auth/users/                      User registration + profile
/auth/users/me/                   Current user
/auth/users/forgot_password/      Password reset request
/auth/users/reset_password/       Password reset confirm
/auth/jwt/create/                 Login → JWT pair
/auth/jwt/refresh/                Refresh access token
/chatbot/report/types/            Catalogue of 6 report types + implemented flag (public)
/chatbot/report/data/             Upload (POST) + list (GET) reports
/chatbot/chat/session/list/       Create + list chat sessions
/chatbot/chat/session/<id>/       Session detail (with message history)
/chatbot/chat/session/<id>/send/  Send a user message → assistant reply
/schema/                          OpenAPI schema (drf-spectacular)
/schema/swagger/                  Swagger UI
/schema/redoc/                    ReDoc UI
```

## Branches

- `stable` — **production deploy branch.** Whatever's here is what's running
  on `api.genelio.com`. Squash-merge into here from feature branches.
- `develop`, `qa`, `uat`, `main` — declared in the original GitFlow plan
  in the README, but the team has not actually been using them. Don't trust
  them as in-sync with anything.
- `gradio-ui` — separate prototype branch. Hosts the standalone reviewer UI
  (a Gradio app embedding Django via `bootstrap.py`, SQLite-backed). Not
  deployed to prod; runs on the same box at port 7860, exposed via a
  `gradio.live` share tunnel.

## Local dev

```bash
# 1. Bring up Postgres + pgAdmin in Docker (optional — SQLite works for tests)
docker compose up -d db pgadmin

# 2. Python env
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Env file
cp .env.example .env
# edit .env: DJANGO_SECRET_KEY, DATABASE_URL, VLLM_BASE_URL

# 4. Migrate + run
python manage.py migrate
python manage.py runserver 0.0.0.0:8000

# 5. Tests (no Postgres needed — uses in-memory SQLite)
DATABASE_URL=sqlite:///./test.sqlite3 \
DJANGO_SECRET_KEY=test-only-key \
DJANGO_DEBUG=True \
DJANGO_ALLOWED_HOSTS='*' \
python manage.py test reports chatbot
```

## Conventions

- **Lazy ML imports.** Anything that pulls `torch`, `sentence-transformers`,
  or `chromadb` lives inside a function, not at module top. Tests + Django
  boot must work without them installed.
- **Uniform analyzer output.** Every analyzer returns the same dict shape
  (`status` / `site` / `report` / `analysis_context` / optional
  `structured_summary`). Don't break this — `chatbot/pipeline.py` is
  type-blind to which report it's looking at.
- **No `manage.py runserver` in serious load tests.** It's the dev server.
  Production runs it because of a screen-session deploy that predates this
  doc; please don't perpetuate the pattern in new infra.
- **Sanitised PDF fixtures only.** `postman/fixtures/*.pdf` are scrubbed.
  Never commit a real patient PDF.

## See also

- `DEPLOYMENT.md` — how this repo is deployed to `213.181.122.239`,
  step-by-step runbook + rollback.
- `README.md` — high-level project intro.
- `gradio_ui/README.md` (on the `gradio-ui` branch) — reviewer-UI prototype.
