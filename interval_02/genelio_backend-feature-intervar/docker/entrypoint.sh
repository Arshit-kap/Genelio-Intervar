#!/bin/sh
# Container entrypoint — wait for Postgres, run migrations, collect static,
# then exec the CMD passed by the Dockerfile (or compose override).
set -e

# ----------------------------------------------------------------------
# Wait for the db service to accept connections.
# We retry for ~60s before giving up to make `docker compose up` resilient
# against the Postgres container needing a moment to initialise.
# ----------------------------------------------------------------------
host="${POSTGRES_HOST:-db}"
port="${POSTGRES_PORT:-5432}"

echo "[entrypoint] waiting for Postgres at ${host}:${port}…"
i=0
until python -c "import socket,sys; s=socket.socket(); s.settimeout(2);
sys.exit(0 if (s.connect_ex(('${host}', ${port})) == 0) else 1)" 2>/dev/null
do
    i=$((i+1))
    if [ "$i" -ge 30 ]; then
        echo "[entrypoint] Postgres did not become reachable in time" >&2
        exit 1
    fi
    sleep 2
done
echo "[entrypoint] Postgres is up."

# ----------------------------------------------------------------------
# Apply migrations + collect static. Both are idempotent.
# ----------------------------------------------------------------------
python manage.py migrate --noinput
python manage.py collectstatic --noinput || true

echo "[entrypoint] launching: $*"
exec "$@"
