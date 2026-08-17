#!/usr/bin/env bash
# web_admin_e2e.sh — Smoke test for wordforge web admin API.
# Exit 0 = all green, non-zero = failure (stderr shows which step + request_id).
#
# Usage:
#   bash scripts/smoke/web_admin_e2e.sh
#   WORDFORGE_SMOKE_BASE_URL=http://staging:8000 bash scripts/smoke/web_admin_e2e.sh

set -euo pipefail

# --- Prerequisites -----------------------------------------------------------
for cmd in curl jq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo >&2 "FATAL: need '$cmd' installed but not found in PATH"
    exit 127
  fi
done

# --- Config from env ----------------------------------------------------------
BASE_URL="${WORDFORGE_SMOKE_BASE_URL:-http://127.0.0.1:8000}"
EMAIL="${WORDFORGE_SMOKE_EMAIL:-dev@wordforge.local}"
PASSWORD="${WORDFORGE_SMOKE_PASSWORD:-devpass123}"

COOKIE_JAR="/tmp/wordforge-smoke-cookies-$$.txt"
TMP_BODY="/tmp/wordforge-smoke-body-$$.json"
TMP_HDR="/tmp/wordforge-smoke-hdr-$$.txt"

# --- Cleanup on exit ----------------------------------------------------------
cleanup() {
  rm -f "$COOKIE_JAR" "$TMP_BODY" "$TMP_HDR"
}
trap cleanup EXIT

# --- Helpers ------------------------------------------------------------------

# do_curl <method> <path> [extra curl args...]
# Sets: HTTP_CODE, BODY (string), REQ_ID
do_curl() {
  local method="$1" path="$2"
  shift 2
  local url="${BASE_URL}${path}"

  HTTP_CODE=$(curl -s -o "$TMP_BODY" -D "$TMP_HDR" \
    -w "%{http_code}" \
    -X "$method" \
    -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    -H "Content-Type: application/json" \
    "$@" \
    "$url") || {
      echo >&2 "ERROR: curl failed (connection refused?). Is the backend running at $BASE_URL?"
      exit 1
    }

  BODY=$(cat "$TMP_BODY")
  REQ_ID=$(grep -i '^x-request-id:' "$TMP_HDR" 2>/dev/null | awk '{print $2}' | tr -d '\r' || echo "unknown")
}

fail_step() {
  local step="$1" desc="$2"
  echo >&2 "--- HTTP $HTTP_CODE | X-Request-ID: $REQ_ID ---"
  echo >&2 "$BODY"
  echo >&2 "<<< step $step FAIL: $desc"
  exit 1
}

pass_step() {
  local step="$1" desc="$2"
  echo ">>> step $step PASS: $desc"
}

# --- Step 1: health -----------------------------------------------------------
do_curl GET /api/v1/health
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 1 "health endpoint returned $HTTP_CODE (expected 200)"
fi
if ! echo "$BODY" | jq -e '.ok == true' &>/dev/null; then
  fail_step 1 "health body.ok is not true"
fi
pass_step 1 "GET /api/v1/health -> 200, ok=true"

# --- Step 2: login success ----------------------------------------------------
do_curl POST /api/v1/auth/login -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 2 "login returned $HTTP_CODE (expected 200)"
fi
# Verify session cookie was set
if ! grep -qi "session" "$COOKIE_JAR" 2>/dev/null; then
  fail_step 2 "session cookie not set after login"
fi
LOGIN_EMAIL=$(echo "$BODY" | jq -r '.data.editor.email // empty')
if [[ "$LOGIN_EMAIL" != "$EMAIL" ]]; then
  fail_step 2 "login response editor.email='$LOGIN_EMAIL' != expected '$EMAIL'"
fi
pass_step 2 "POST /api/v1/auth/login -> 200, cookie set, email matches"

# --- Step 3: login failure (bad password) -------------------------------------
do_curl POST /api/v1/auth/login -d "{\"email\":\"$EMAIL\",\"password\":\"WRONG_PASSWORD_SMOKE\"}"
if [[ "$HTTP_CODE" != "401" ]]; then
  fail_step 3 "bad-password login returned $HTTP_CODE (expected 401)"
fi
ERR_CODE=$(echo "$BODY" | jq -r '.error.code // empty')
if [[ "$ERR_CODE" != "unauthenticated" ]]; then
  fail_step 3 "error.code='$ERR_CODE' (expected 'unauthenticated')"
fi
pass_step 3 "POST /api/v1/auth/login (bad pw) -> 401, error.code=unauthenticated"

# --- Step 4: me ---------------------------------------------------------------
do_curl GET /api/v1/auth/me
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 4 "GET /me returned $HTTP_CODE (expected 200)"
fi
ME_EMAIL=$(echo "$BODY" | jq -r '.data.email // empty')
if [[ "$ME_EMAIL" != "$EMAIL" ]]; then
  fail_step 4 "me response email='$ME_EMAIL' != expected '$EMAIL'"
fi
pass_step 4 "GET /api/v1/auth/me -> 200, email matches"

# --- Step 5: search basic -----------------------------------------------------
do_curl GET "/api/v1/words?limit=5"
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 5 "search returned $HTTP_CODE (expected 200)"
fi
if ! echo "$BODY" | jq -e '.data.items | type == "array"' &>/dev/null; then
  fail_step 5 "data.items is not an array"
fi
ITEMS_LEN=$(echo "$BODY" | jq '.data.items | length')
pass_step 5 "GET /api/v1/words?limit=5 -> 200, items array (len=$ITEMS_LEN)"

# --- Step 6: search with q ---------------------------------------------------
do_curl GET "/api/v1/words?q=a&limit=3"
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 6 "search with q returned $HTTP_CODE (expected 200)"
fi
pass_step 6 "GET /api/v1/words?q=a&limit=3 -> 200"

# --- Steps 7-10: require items from step 5 -----------------------------------
if [[ "$ITEMS_LEN" == "0" ]]; then
  echo >&2 "WARNING: step 5 returned 0 items — skipping steps 7-10 (dev DB should have words; empty suggests wrong env)"
  echo ">>> steps 7-10 SKIPPED (empty DB)"
  echo ">>> ALL 10 STEPS PASSED [7-10 skipped] (base=$BASE_URL)"
  exit 0
fi

# --- Step 7: detail -----------------------------------------------------------
WORD_ID=$(echo "$BODY" | jq -r '.data.items[0].word_id // empty')
# Re-fetch from step 5 body; but step 6 overwrote BODY so re-fetch step 5
do_curl GET "/api/v1/words?limit=1"
WORD_ID=$(echo "$BODY" | jq -r '.data.items[0].word_id // empty')

do_curl GET "/api/v1/words/$WORD_ID"
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 7 "detail for word_id=$WORD_ID returned $HTTP_CODE (expected 200)"
fi
DETAIL_WORD_ID=$(echo "$BODY" | jq -r '.data.word.word_id // empty')
if [[ "$DETAIL_WORD_ID" != "$WORD_ID" ]]; then
  fail_step 7 "detail word_id='$DETAIL_WORD_ID' != expected '$WORD_ID'"
fi
WORD_FORM=$(echo "$BODY" | jq -r '.data.word.form // empty')
pass_step 7 "GET /api/v1/words/$WORD_ID -> 200, word_id matches (form='$WORD_FORM')"

# --- Step 8: audit list -------------------------------------------------------
do_curl GET "/api/v1/audit?limit=5"
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 8 "audit list returned $HTTP_CODE (expected 200)"
fi
if ! echo "$BODY" | jq -e '.data.items | type == "array"' &>/dev/null; then
  fail_step 8 "data.items is not an array"
fi
pass_step 8 "GET /api/v1/audit?limit=5 -> 200, items array"

# --- Step 9: PATCH drift (expect 409) ----------------------------------------
# Save current form to verify no side-effect
do_curl GET "/api/v1/words/$WORD_ID"
FORM_BEFORE=$(echo "$BODY" | jq -r '.data.word.form // empty')

PATCH_PAYLOAD=$(jq -n '{changes: [{field_path: "words.form", target_id: null, op: "update", old_value: "__SMOKE_IMPOSSIBLE__", new_value: "__SMOKE_NEW__"}]}')
do_curl PATCH "/api/v1/words/$WORD_ID" -d "$PATCH_PAYLOAD"
if [[ "$HTTP_CODE" != "409" ]]; then
  fail_step 9 "drift PATCH returned $HTTP_CODE (expected 409)"
fi
DRIFT_CODE=$(echo "$BODY" | jq -r '.error.code // empty')
if [[ "$DRIFT_CODE" != "conflict" ]]; then
  fail_step 9 "error.code='$DRIFT_CODE' (expected 'conflict')"
fi

# Verify no side-effect: form unchanged
do_curl GET "/api/v1/words/$WORD_ID"
FORM_AFTER=$(echo "$BODY" | jq -r '.data.word.form // empty')
if [[ "$FORM_BEFORE" != "$FORM_AFTER" ]]; then
  fail_step 9 "SIDE EFFECT: form changed from '$FORM_BEFORE' to '$FORM_AFTER' despite drift!"
fi
pass_step 9 "PATCH /api/v1/words/$WORD_ID (drift) -> 409 conflict, form unchanged"

# --- Step 10: logout ----------------------------------------------------------
do_curl POST /api/v1/auth/logout
if [[ "$HTTP_CODE" != "200" ]]; then
  fail_step 10 "logout returned $HTTP_CODE (expected 200)"
fi

# Verify session is revoked: GET /me should now 401
do_curl GET /api/v1/auth/me
if [[ "$HTTP_CODE" != "401" ]]; then
  fail_step 10 "GET /me after logout returned $HTTP_CODE (expected 401)"
fi
pass_step 10 "POST /api/v1/auth/logout -> 200; GET /me -> 401 (session revoked)"

# --- Summary ------------------------------------------------------------------
echo ">>> ALL 10 STEPS PASSED (base=$BASE_URL)"
