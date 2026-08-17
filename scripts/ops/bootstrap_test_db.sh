#!/usr/bin/env bash
# bootstrap_test_db.sh — bring up the test Postgres container and run alembic
# against it. Idempotent: safe to run before every test session.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

export DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test}"

echo ">> Starting test Postgres (docker-compose.test.yml, port 5434)..."
docker compose -f docker-compose.test.yml up -d

echo ">> Waiting for Postgres to be healthy..."
for i in {1..30}; do
  if docker exec wordforge-pg-test pg_isready -U wordforge -d wordforge_test >/dev/null 2>&1; then
    echo "   up after ${i}s"
    break
  fi
  sleep 1
  if [[ $i -eq 30 ]]; then
    echo "ERROR: test Postgres never became healthy" >&2
    exit 1
  fi
done

echo ">> Running alembic upgrade head against $DATABASE_URL"
uv run alembic upgrade head

echo ">> Done. To run tests:"
echo "   DATABASE_URL='$DATABASE_URL' uv run pytest"
