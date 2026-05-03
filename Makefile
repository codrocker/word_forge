# wordforge — developer shortcuts.
# All targets should be idempotent.

.PHONY: test test-db-up test-db-down lint clean

TEST_DATABASE_URL ?= postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test

# Bring up the isolated test Postgres, run migrations against it, run pytest.
# Coexists with the dev container (5433) so you can run `wordforge run` and
# tests at the same time.
test: test-db-up
	DATABASE_URL='$(TEST_DATABASE_URL)' uv run pytest

# Start the test container + migrate. Stops short of running tests so CI
# can parallelize.
test-db-up:
	./scripts/ops/bootstrap_test_db.sh

# Stop the test container. Does NOT remove the volume — use
# `docker compose down -v` if you really want to wipe test data.
test-db-down:
	docker compose down

lint:
	uv run ruff check .

clean:
	find . -name '__pycache__' -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
