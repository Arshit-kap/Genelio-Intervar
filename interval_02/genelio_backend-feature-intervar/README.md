# Genelio Backend

Django + DRF + JWT backend that mirrors the public API surface of
[genelio.com](https://genelio.com). Signup / login / profile, chat sessions,
and report uploads for the six report types shown in the live upload picker:

| Type | Implemented |
|---|---|
| WGS | stub |
| WES | stub |
| Oral Microbiome | stub |
| **Gut Microbiome** | **full** (PDF parser + RAG chat) |
| Skin Microbiome | stub |
| Vaginal Microbiome | stub |

Stub analyzers return `{"status": "stub"}` so the full upload → list →
detail → chat flow works end-to-end for every type while the real pipelines
are built out.

## Stack

- **Python** 3.11
- **Django** 5.1 + **Django REST Framework** 3.15
- **JWT** via `djangorestframework-simplejwt`
- **Postgres** 16 (via Docker)
- **pgAdmin 4** (via Docker)
- **drf-spectacular** for OpenAPI / Swagger / ReDoc

> Docker is used **only for infrastructure** (Postgres + pgAdmin). The Django
> app itself runs on the host. A commented-out `web` service in
> [`docker-compose.yml`](docker-compose.yml) is ready if you ever want to
> containerize the API too.

## Layout

```
genelio/              Django project config (settings, urls, wsgi, asgi)
accounts/             Custom User + djoser-compatible /auth/* endpoints
reports/              Report model, upload, list, detail
  analyzers/          Per-type parsers (gut = real, others = stub)
chatbot/              Chat sessions + messages + RAG pipeline
postman/              Postman collection + environment + sample PDF fixture
docker/pgadmin/       Pre-registered server for pgAdmin
legacy/               Original Gradio prototype — kept for reference
docker-compose.yml    Postgres + pgAdmin (only)
requirements.txt
.env.example
```

## Getting started

### 1. Start Postgres + pgAdmin

```bash
cp .env.example .env
docker compose up -d
```

This starts two containers:

| Service | Host port | Credentials |
|---|---|---|
| `genelio-db` (Postgres 16) | `localhost:5432` | `genelio` / `genelio` / db `genelio` |
| `genelio-pgadmin` (pgAdmin 4) | http://localhost:5050 | `admin@genelio.local` / `admin` |

pgAdmin comes pre-registered with the `db` server
([`docker/pgadmin/servers.json`](docker/pgadmin/servers.json)) so it shows up
in the sidebar on first login — just enter the Postgres password (`genelio`)
when prompted.

### 2. Install the Python app locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Migrate and run

```bash
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver 127.0.0.1:8000
```

The API is now at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/schema/swagger/`.

### Shutting down

```bash
docker compose stop             # keep data
docker compose down             # remove containers
docker compose down -v          # remove containers AND wipe pgdata/pgadmin
```

## Database

Default connection string (in [.env.example](.env.example)):

```
DATABASE_URL=postgres://genelio:genelio@localhost:5432/genelio
```

Override `DATABASE_URL` for any other database supported by
[dj-database-url](https://github.com/jazzband/dj-database-url) — e.g. a
managed Postgres URL in staging/production.

### Migrations

Migrations live under each app's `migrations/` folder and are committed:

- [accounts/migrations/0001_initial.py](accounts/migrations/0001_initial.py) — custom User
- [reports/migrations/0001_initial.py](reports/migrations/0001_initial.py) — Report + indexes
- [chatbot/migrations/0001_initial.py](chatbot/migrations/0001_initial.py) — ChatSession + ChatMessage

Regenerate after model changes:

```bash
python manage.py makemigrations
python manage.py migrate
```

## API

### Auth  (`/auth/...`)
| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/users/` | Signup (email + password + re_password + agreed_to_terms + first/last name) |
| `GET` | `/auth/users/me/` | Current user |
| `PATCH` | `/auth/users/me/` | Update profile |
| `POST` | `/auth/users/set_password/` | Change password |
| `POST` | `/auth/users/set_avatar/` | multipart avatar upload |
| `POST` | `/auth/jwt/create/` | Login → `{access, refresh}` |
| `POST` | `/auth/jwt/refresh/` | Rotate access token |
| `POST` | `/auth/jwt/verify/` | Verify token |

### Reports  (`/chatbot/report/...`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/chatbot/report/types/` | Catalogue of report types |
| `GET` | `/chatbot/report/data/` | Paginated list of the caller's reports (filter: `report_type`, `status`) |
| `POST` | `/chatbot/report/data/` | multipart upload — `file` + `report_type` |
| `GET` | `/chatbot/report/data/<id>/` | Detail, includes `parsed_data` |
| `DELETE` | `/chatbot/report/data/<id>/` | Delete |

### Chat  (`/chatbot/chat/...`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/chatbot/chat/session/list/` | Paginated session list |
| `POST` | `/chatbot/chat/session/list/` | Create session (optional `report_id`) |
| `GET` | `/chatbot/chat/session/<id>/` | Detail, includes messages |
| `PATCH` | `/chatbot/chat/session/<id>/` | Rename / relink report |
| `DELETE` | `/chatbot/chat/session/<id>/` | Delete |
| `POST` | `/chatbot/chat/session/<id>/send/` | Send a user message, returns user + assistant messages |

## Postman

A ready-to-run collection covering the full user flow lives under
[`postman/`](postman).

- [`postman/Genelio.postman_collection.json`](postman/Genelio.postman_collection.json)
- [`postman/Genelio.postman_environment.json`](postman/Genelio.postman_environment.json)
- [`postman/fixtures/sample.pdf`](postman/fixtures/sample.pdf) — tiny PDF used by the upload request

### Import into Postman

1. **Import** both JSON files.
2. Select the **Genelio (local docker)** environment (top-right).
3. Collection → **Run**. That's it.

The prerequest script auto-generates a unique `email` per run, and each
response's Tests stash `access_token`, `refresh_token`, `report_id`, and
`session_id` into the environment so later requests pick them up
automatically.

### Run from the CLI (Newman)

```bash
cd postman
npx --yes newman@6 run Genelio.postman_collection.json \
  -e Genelio.postman_environment.json \
  --env-var email="demo+$(date +%s)@example.com"
```

Last run against this backend:

```
iterations        1   0 failed
requests         17   0 failed
test-scripts     17   0 failed
assertions       26   0 failed
total run duration: ~570ms
```

### Flow covered

1. **Auth** — Signup → Login (JWT) → `/auth/users/me/` → Update profile → Refresh token
2. **Reports** — List types → Upload (WES stub, multipart with sample.pdf) → List (paginated) → Filter by type → Detail
3. **Chat** — Create session (linked to the uploaded report) → List → Send message → Fetch with messages → Rename
4. **Cleanup** — Delete session → Delete report

### Gut-microbiome chat path

Only the Gut Microbiome type has real parsing + RAG. To exercise it:

1. Change `report_type` to `gut` in the **Upload report** request.
2. Attach a real gut-microbiome PDF (replace `fixtures/sample.pdf`).
3. Make sure a vLLM-compatible endpoint is reachable at `VLLM_BASE_URL` (see
   `.env`). Without it the `send` endpoint returns a graceful error instead
   of 500-ing.

## Tests

```bash
python manage.py test
```

Covers: auth flow, password mismatch rejection, report types catalogue,
upload + list happy path, tenant isolation, chat create / list / send →
**6/6 passing**.
