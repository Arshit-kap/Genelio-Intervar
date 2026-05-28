# syntax=docker/dockerfile:1.7
#
# Genelio backend — Django + DRF + JWT + Postgres
#
# This image is intentionally small (slim base, no build artefacts left
# around) but still carries everything the runtime needs:
#   * psycopg[binary] talking to the Postgres container
#   * Pillow for avatar uploads
#   * pdfplumber for PDF parsing
#   * sentence-transformers + chromadb for the gut RAG pipeline
#
# It is used by docker-compose.yml's `web` service. For local dev the
# container runs Django's runserver (auto-reload on volume-mounted code).
# Swap CMD for gunicorn when promoting to staging/prod.

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages:
#   * libpq5         — runtime shared library that psycopg[binary] dlopens
#   * libjpeg / zlib — runtime libs Pillow links against
#   * curl           — used by the HEALTHCHECK below
#   * gcc + libpq-dev are NOT needed because we use the binary wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        zlib1g \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so this layer caches across code edits.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && pip install gunicorn==23.0.0

# Copy application source. .dockerignore keeps .venv / media / __pycache__
# / scripts/_out_*.json out of the image.
COPY . .

# Persist uploaded PDFs and the Chroma vector store outside the image.
RUN mkdir -p /app/media /app/staticfiles
VOLUME ["/app/media"]

EXPOSE 8000

# Lightweight health probe — hitting an auth-required endpoint returns 401
# when the app is healthy (200/401/403 all mean "responding").
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS -o /dev/null -w "%{http_code}" http://localhost:8000/auth/users/ \
        | grep -E "^(200|401|403)$" || exit 1

# Entrypoint applies migrations and collects static before handing off to
# the chosen server command.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]

# Default: dev server with auto-reload. Override in compose for prod.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
