# Genelio Backend — Deployment

This is the runbook for getting code on `stable` running at
`https://api.genelio.com`. If you're reading this for the first time, start
with **Hosting model** so the rest makes sense.

> All production paths are **on `213.181.122.239`** (Ubuntu 24.04 KVM VM).
> SSH: `ssh -i ~/Downloads/ubuntu_.pem ubuntu@213.181.122.239`.

---

## Hosting model (today)

Not gunicorn, not systemd, not Docker — just `screen` sessions on a shared
VM. Be aware of the limitations before changing anything.

| Thing | Where |
|---|---|
| Code checkout | `/home/ubuntu/Genelio/genelio_backend` (this repo) |
| Branch deployed | `stable` |
| Python venv | `/home/ubuntu/llm-stack/venvs/genelio-backend-env` (Python 3.12.3) |
| Process | `python manage.py runserver 0.0.0.0:8112` running inside `screen -S genelio-backend` |
| Reverse proxy | nginx → `api.genelio.com` → `127.0.0.1:8112` (Let's Encrypt TLS) |
| Postgres | Docker container `postgres` (`pgvector/pgvector:pg17`) on `127.0.0.1:5432` |
| LLM | Docker container `vllm-qwen3-30b` on `0.0.0.0:8011` (host port) |
| Embedder | Loaded in-process inside the runserver, downloads HF model on first chat |
| Chroma store | `media/chroma/` on the deploy clone (persists across restarts) |
| `.env` | `/home/ubuntu/Genelio/genelio_backend/.env` |
| Backups | Manual `pg_dump` to `~/backup/genelio-pre-<sha>-<ts>.dump` |

⚠️ **Known caveats:**
- `manage.py runserver` is Django's dev server. Single-process, no graceful
  reload. Restarts cause a 2–5s outage window where nginx returns 502.
- `DJANGO_DEBUG=True` in production `.env` (info-disclosure risk; not blocking).
- `develop`, `main`, `qa`, `uat` branches exist on origin but are stale —
  `stable` is the only live one. Don't deploy from any other branch.
- A separate `ai-api.genelio.com` DNS record points at an unrelated AWS ELB
  (`ca-central-1`, returns 404). It is **not** this server. Production lives
  on `api.genelio.com` only.

---

## Standard deploy (code change, no infra change)

This is the path used for the WES/WGS analyzer ship in `d19e12e`.

### 0. Local prep

```bash
cd ~/dev/genelio_backend
git fetch origin
git checkout stable
git pull --ff-only origin stable

# Run the test suite locally (uses SQLite, no Postgres required)
DATABASE_URL=sqlite:///./test.sqlite3 \
DJANGO_SECRET_KEY=test-only-key \
DJANGO_DEBUG=True \
DJANGO_ALLOWED_HOSTS='*' \
python manage.py test reports chatbot
# Expect: OK (skipped=N) — N is the integration tests for fixtures not in
# postman/fixtures/. New code should add OK tests, not skipped ones.

# Push your commits
git push origin stable
```

### 1. (Optional but recommended) Pre-deploy DB snapshot

```bash
ssh -i ~/Downloads/ubuntu_.pem ubuntu@213.181.122.239 \
  "mkdir -p ~/backup && \
   docker exec postgres pg_dump -U genelio -Fc genelio \
     > ~/backup/genelio-pre-$(git -C ~/dev/genelio_backend rev-parse --short HEAD)-$(date +%Y%m%dT%H%M%SZ).dump"
```

Skip only when the migration is purely additive (new tables / additive FKs)
**and** you're prepared to accept the rollback strategy of "unmigrate, the
table starts empty so no data is lost."

### 2. Pull on the server

```bash
ssh -i ~/Downloads/ubuntu_.pem ubuntu@213.181.122.239
cd /home/ubuntu/Genelio/genelio_backend
git fetch origin
git pull --ff-only origin stable
git log -1 --oneline    # confirm new HEAD
```

### 3. Pick up new dependencies

```bash
PIP=/home/ubuntu/llm-stack/venvs/genelio-backend-env/bin/pip
$PIP install -r requirements.txt    # safety net; fast no-op if nothing new
```

### 4. Apply migrations

```bash
PY=/home/ubuntu/llm-stack/venvs/genelio-backend-env/bin/python
$PY manage.py showmigrations chatbot reports accounts
$PY manage.py migrate
$PY manage.py showmigrations chatbot reports accounts    # confirm all [X]
```

### 5. Restart the backend

The screen session keeps the runserver alive. Restart by sending Ctrl-C
to the foreground process inside the screen, then re-issuing the command.

```bash
# Send Ctrl-C
screen -S genelio-backend -p 0 -X stuff $'\003'
sleep 3
ss -tlnp | grep ':8112' || echo 'port freed'

# Start runserver again
screen -S genelio-backend -p 0 -X stuff \
  $'cd /home/ubuntu/Genelio/genelio_backend && /home/ubuntu/llm-stack/venvs/genelio-backend-env/bin/python manage.py runserver 0.0.0.0:8112\n'
sleep 5
ss -tlnp | grep ':8112'   # confirm bound
```

Outage window: ~2–5s. nginx returns 502 in the gap.

### 6. Smoke test

```bash
# On the box (skips nginx + TLS)
curl -s http://127.0.0.1:8112/chatbot/report/types/ | jq .

# Through the public URL (nginx + TLS + ALLOWED_HOSTS check)
curl -s https://api.genelio.com/chatbot/report/types/ | jq .

# Expected (post-d19e12e): WES + WGS report implemented:true.
```

If the smoke fails:
- Check the screen buffer: `screen -S genelio-backend -X hardcopy /tmp/be.txt; cat /tmp/be.txt`.
- Common: missing dep (the runserver crashes on import), missing env var
  (DATABASE_URL, DJANGO_SECRET_KEY), Postgres not up, vLLM not up.

---

## First-time deploy (new server / disaster recovery)

```bash
# Clone (use SSH; never embed PATs in remotes)
git clone git@github.com:KWP-inc/genelio_backend.git \
  /home/ubuntu/Genelio/genelio_backend
cd /home/ubuntu/Genelio/genelio_backend
git checkout stable

# Postgres (Docker)
docker compose up -d db pgadmin

# vLLM is brought up separately (`llm-stack` repo). Ensure
# http://localhost:8011/v1/models responds before continuing.

# Python env (or reuse /home/ubuntu/llm-stack/venvs/genelio-backend-env)
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt

# .env (do NOT commit; copy from .env.example, fill values)
cp .env.example .env
# Required: DJANGO_SECRET_KEY (generate fresh), DJANGO_ALLOWED_HOSTS
# (must include api.genelio.com), DATABASE_URL, VLLM_BASE_URL.
# DJANGO_DEBUG should be False in any new deploy.

./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput

# nginx site (drop into /etc/nginx/sites-available/api.genelio.com,
# symlink into sites-enabled, run `certbot --nginx -d api.genelio.com`):
#   server {
#       server_name api.genelio.com;
#       location / {
#           proxy_pass http://127.0.0.1:8112;
#           proxy_read_timeout 300s;
#           client_max_body_size 50M;
#       }
#   }

# Start in a screen
screen -dmS genelio-backend
screen -S genelio-backend -p 0 -X stuff \
  $'cd /home/ubuntu/Genelio/genelio_backend && ./venv/bin/python manage.py runserver 0.0.0.0:8112\n'
```

---

## Rollback

For the recent WES/WGS deploy specifically, the `0002_messagereview` migration
is additive (creates a new table, no data destroyed). Rollback is:

```bash
ssh -i ~/Downloads/ubuntu_.pem ubuntu@213.181.122.239
cd /home/ubuntu/Genelio/genelio_backend

# 1. Code rollback
git fetch origin
git checkout <previous-stable-sha>      # the one you replaced

# 2. Schema rollback (drops chatbot_messagereview — empty table, no data lost)
/home/ubuntu/llm-stack/venvs/genelio-backend-env/bin/python manage.py migrate chatbot 0001_initial

# 3. Restart screen (same as deploy step 5)

# 4. If you took a pg_dump, restore is:
docker exec -i postgres pg_restore --clean --if-exists -U genelio -d genelio \
  < ~/backup/genelio-pre-<sha>-<ts>.dump
```

---

## Things to fix when there's a moment

These came out of an audit; they don't block deploys but they're worth
ticketing:

1. **Move off `manage.py runserver`** to gunicorn (or uvicorn-worker under
   gunicorn) under systemd. Gives you `ExecReload=` + graceful workers +
   automatic restart on boot. Eliminates the 502-during-restart window.
2. **`DJANGO_DEBUG=True`** in `.env` on prod — flip it to `False` and verify
   nothing depends on the resulting error pages or static-serve behavior
   (whitenoise should already cover statics).
3. **`ai-api.genelio.com`** DNS → AWS ELB returns 404. Either retire the
   record or stand up a real backend behind it.
4. **No CI deploy hook.** A `git pull && migrate && restart` script invoked
   by a webhook (or a GitHub Actions runner with SSH) would remove the
   manual deploy steps above.
5. **Branch hygiene.** `develop`, `qa`, `uat`, `main` are stale; either
   delete them or make the README's GitFlow story actually true.

---

## See also

- `CLAUDE.md` — repo orientation for AI assistants.
- `README.md` — project description.
- Frontend repo: `git@github.com:KWP-inc/genelio_frontend.git` (deployed in
  parallel on the same box; see its own `DEPLOYMENT.md`).
