#!/usr/bin/env bash
# backup_all.sh — full wordforge backup.
#
# Default strategy: pg_dump -Fc (logical, safe on a live DB).
# Optional strategy: docker volume tarball, faster but requires PG to be
#   stopped first otherwise tar races with PG's on-disk writes and the
#   resulting archive is corrupt on restore. Enable with `--offline-tar`
#   and the script will `docker compose stop` before tar.
#
# Per-strategy outputs land under BACKUP_ROOT/YYYY-MM-DD/. Retention
# deletes any day-directory older than RETAIN_DAYS.
#
# Env knobs:
#   BACKUP_ROOT         default /tmp/wordforge_smoke/backup
#   RETAIN_DAYS         default 7
#   PG_CONTAINER        default wordforge-pg
#   PG_DB               default wordforge
#   PG_USER             default wordforge
#   VOLUME_NAME         default wordforge_wordforge_pg_data
#
# Exit code: 0 if both backups succeed; 1 otherwise (partial backup still
# kept for triage).
#
# Typical usage: add to a cron / launchd job. A sample plist lives at
# scripts/com.wordforge.backup.plist — copy to ~/Library/LaunchAgents/
# then `launchctl load` it.

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/tmp/wordforge_smoke/backup}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
PG_CONTAINER="${PG_CONTAINER:-wordforge-pg}"
PG_DB="${PG_DB:-wordforge}"
PG_USER="${PG_USER:-wordforge}"
VOLUME_NAME="${VOLUME_NAME:-wordforge_wordforge_pg_data}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

OFFLINE_TAR=0
for arg in "$@"; do
    case "$arg" in
        --offline-tar) OFFLINE_TAR=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 64 ;;
    esac
done

TS="$(date +%Y-%m-%d_%H%M%S)"
DAY_DIR="$BACKUP_ROOT/$(date +%Y-%m-%d)"
mkdir -p "$DAY_DIR"

LOG="$DAY_DIR/backup_${TS}.log"
exec > >(tee -a "$LOG") 2>&1

echo "=== wordforge backup ${TS} ==="

# --- Strategy 1: pg_dump -Fc ---------------------------------------------
dump_out="$DAY_DIR/pgdump_${TS}.dump"
dump_ok=0
echo ">> pg_dump → $dump_out"
if docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    if docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -Fc -d "$PG_DB" > "$dump_out"; then
        size=$(wc -c < "$dump_out" | awk '{print int($1/1024/1024)}')
        echo "   ok: ${size} MB"
        dump_ok=1
    else
        echo "   FAIL: pg_dump returned non-zero"
    fi
else
    echo "   SKIP: container $PG_CONTAINER not running"
fi

# --- Strategy 2: docker volume tarball (ONLY if --offline-tar) -----------
tarball_out="$DAY_DIR/volume_${TS}.tgz"
tarball_ok=0
if [[ $OFFLINE_TAR -eq 0 ]]; then
    echo ">> docker volume tarball → skipped (pass --offline-tar to enable; "
    echo "   PG must be stopped during tar to avoid 'file changed as we read it')"
else
    echo ">> STOPPING PG container for offline tarball..."
    docker compose -f "$COMPOSE_FILE" stop postgres
    trap 'echo ">> bringing PG back up" && docker compose -f "$COMPOSE_FILE" start postgres' EXIT
    # postgres:15 image is already pulled (we run PG in it). Avoids a
    # DockerHub pull for alpine in environments with flaky registry
    # access (mainland China). Override via BACKUP_TAR_IMAGE if needed.
    TAR_IMAGE="${BACKUP_TAR_IMAGE:-postgres:15}"
    echo ">> docker volume tarball → $tarball_out"
    if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
        if docker run --rm \
            -v "${VOLUME_NAME}:/data:ro" \
            -v "${DAY_DIR}:/bak" \
            "$TAR_IMAGE" \
            tar czf "/bak/volume_${TS}.tgz" -C / data; then
            size=$(wc -c < "$tarball_out" | awk '{print int($1/1024/1024)}')
            echo "   ok: ${size} MB"
            tarball_ok=1
        else
            echo "   FAIL: tar returned non-zero"
        fi
    else
        echo "   SKIP: volume $VOLUME_NAME not found"
    fi
    # trap restores PG regardless of outcome
fi

# --- Retention: delete day-dirs older than RETAIN_DAYS -------------------
echo ">> pruning backups older than ${RETAIN_DAYS} days under $BACKUP_ROOT"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETAIN_DAYS}" -print -exec rm -rf {} +

echo "=== done: pg_dump=${dump_ok} volume_tar=${tarball_ok} offline_tar=${OFFLINE_TAR} ==="

# Exit success iff the primary (pg_dump) succeeded AND, if offline tarball
# was requested, that also succeeded. Primary is mandatory; tarball only
# when explicitly asked for.
if [[ $dump_ok -eq 1 ]] && { [[ $OFFLINE_TAR -eq 0 ]] || [[ $tarball_ok -eq 1 ]]; }; then
    exit 0
fi
exit 1
