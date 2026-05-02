"""CLI smoke: --help works and lists the cache prune subcommand.

The full `wordforge cache prune` end-to-end test lives in Task 4 Step 5
(after CacheStore exists). This Task 0 only checks the CLI scaffold."""

from __future__ import annotations

import os
import subprocess

WORDFORGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Env keys that, if set, cause wordforge.cli to register an LLM completer and
# therefore run the LLM stages. Tests that want to exercise the "no LLM"
# path need to pop ALL of these. Update here when a new provider is added
# to src/wordforge/llm/*.
_LLM_PROVIDER_ENV_KEYS = (
    # Bedrock (AWS)
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    # Anthropic direct
    "ANTHROPIC_API_KEY",
    # Gemini (AI Studio + Vertex AI SA path)
    "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "WORDFORGE_GCP_SA_AWS_ACCESS_KEY_ID",
    "WORDFORGE_GCP_SA_AWS_SECRET_ACCESS_KEY",
    # OpenAI direct
    "OPENAI_API_KEY",
    # Azure OpenAI
    "AZURE_OPENAI_EP1_KEY",
    "AZURE_OPENAI_EP2_KEY",
    # Alibaba Qwen (DashScope)
    "DASHSCOPE_API_KEY",
)


def _wordforge(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "wordforge", *args],
        cwd=WORDFORGE_DIR,
        check=False,
        capture_output=True,
        text=True,
    )


def test_wordforge_help_lists_cache_subcommand():
    r = _wordforge("--help")
    assert r.returncode == 0
    assert "cache" in r.stdout


def test_cache_help_lists_prune():
    r = _wordforge("cache", "--help")
    assert r.returncode == 0
    assert "prune" in r.stdout


def test_cache_prune_end_to_end(at_head):
    """Actually invoke `wordforge cache prune --older-than 0d`.

    Depends on `at_head` fixture (from tests/conftest.py) to guarantee
    schema is at head — otherwise this test is order-dependent on other
    DB tests that happened to leave the schema migrated.
    """
    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge",
    )
    r = subprocess.run(
        ["uv", "run", "wordforge", "cache", "prune", "--older-than", "0d"],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "pruned" in r.stdout


def test_ingest_help_lists_it():
    r = _wordforge("--help")
    assert r.returncode == 0
    assert "ingest" in r.stdout


def test_ingest_end_to_end(at_head, tmp_path):
    """Write a tiny wordlist, run `wordforge ingest`, verify rows exist."""
    import os

    wordlist = tmp_path / "words.txt"
    wordlist.write_text("apple\nbanana\n pick up \n\n", encoding="utf-8")

    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge",
    )
    r = subprocess.run(
        ["uv", "run", "wordforge", "ingest", str(wordlist)],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "inserted=3" in r.stdout
    assert "skipped_empty=1" in r.stdout


def test_run_help_lists_it():
    r = _wordforge("--help")
    assert r.returncode == 0
    assert "run" in r.stdout


def test_run_end_to_end_with_stages(at_head, tmp_path):
    """P5a: `wordforge ingest --batch` auto-creates the batch
    and attaches words; `wordforge run --batch` then runs fetch_dict + phonetic
    stages via WORDFORGE_STUB_YOUDAO_JSON stub."""
    import os

    wordlist = tmp_path / "words.txt"
    wordlist.write_text("apple\nbanana\n", encoding="utf-8")

    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge",
    )
    # P5a test stub: force FetchDictStage to use fake HTML.
    env["WORDFORGE_STUB_YOUDAO_JSON"] = (
        '{"simple":{"word":[{"usphone":"ˈæpl","ukphone":"ˈæpl",'
        '"usspeech":"apple&type=2","ukspeech":"apple&type=1"}]}}'
    )
    # P5c: unset LLM provider env so registry skips LLM stages, exercising
    # the "no-API-key" path: fetch_dict + phonetic + quality_gate + export
    # (4 stages total; quality_gate fails for missing LLM upstream).
    for k in _LLM_PROVIDER_ENV_KEYS:
        env.pop(k, None)

    # Step 1: ingest — creates batch + inserts words in one command.
    r = subprocess.run(
        ["uv", "run", "wordforge", "ingest", str(wordlist), "--batch", "B1"],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr

    # Step 2: run — 4 stages (fetch_dict + phonetic + quality_gate + export).
    # LLM stages skipped (no API key). quality_gate fails (missing upstream
    # paraphrase/derivatives/examples/mnemonic) → export pruned from surviving set.
    r = subprocess.run(
        ["uv", "run", "wordforge", "run", "--batch", "B1"],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "2 words" in r.stdout
    assert "4 stages" in r.stdout
    assert "run complete" in r.stdout
    assert "ok_events=4" in r.stdout
    assert "failed_events=2" in r.stdout


def test_run_unknown_batch_fails_fast(at_head):
    """Round 1 U-codex-3 / battle: `wordforge run --batch DOESNOTEXIST`
    must fail loud instead of silently creating an empty batch."""
    import os

    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge",
    )
    r = subprocess.run(
        ["uv", "run", "wordforge", "run", "--batch", "NOPE"],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "unknown batch" in (r.stderr + r.stdout).lower()


# --- P4 Task 3 CLI tests ---
# Round 1 D6: seed via ingest_words (production path).
# Round 1 D7: _wordforge is defined at module scope — no self-import needed.

import sqlalchemy as sa  # noqa: E402 — test module

from wordforge.db.engine import make_engine  # noqa: E402
from wordforge.ingest import ingest_words  # noqa: E402


def _ensure_batch(engine, batch_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches (id, label) VALUES (:b, :b) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"b": batch_id},
        )


def test_cli_plan_without_batch(at_head):
    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["a", "b"], batch_id="B_PLAN_1")
    finally:
        engine.dispose()

    r = _wordforge("plan", "--stage", "paraphrase")
    assert r.returncode == 0, r.stderr
    assert "paraphrase" in r.stdout
    assert "needs_rerun=2" in r.stdout
    assert "estimated_cost_usd=" in r.stdout
    assert "fingerprint unchecked" in r.stdout
    assert "sample:" in r.stdout


def test_cli_plan_with_batch(at_head):
    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["a"], batch_id="B_PLAN_2")
    finally:
        engine.dispose()

    r = _wordforge("plan", "--stage", "paraphrase", "--batch", "B_PLAN_2")
    assert r.returncode == 0, r.stderr
    assert "batch=B_PLAN_2" in r.stdout
    assert "needs_rerun=1" in r.stdout


def test_cli_plan_unknown_stage_fails_fast(at_head):
    r = _wordforge("plan", "--stage", "no_such_stage")
    assert r.returncode != 0
    assert "unknown stage" in (r.stderr + r.stdout).lower()


def test_cli_plan_unknown_batch_fails_fast(at_head):
    r = _wordforge("plan", "--stage", "paraphrase", "--batch", "DOES_NOT_EXIST")
    assert r.returncode != 0
    assert "unknown batch" in (r.stderr + r.stdout).lower()


def test_cli_run_stage_filter_accepts_valid(at_head):
    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["a"], batch_id="B_RUN_S")
    finally:
        engine.dispose()
    r = _wordforge("run", "--batch", "B_RUN_S", "--stage", "paraphrase")
    assert r.returncode == 0, r.stderr
    assert "run complete" in r.stdout


def test_cli_run_stage_filter_rejects_unknown(at_head):
    engine = make_engine()
    try:
        _ensure_batch(engine, "B_RUN_BAD_S")
    finally:
        engine.dispose()
    r = _wordforge("run", "--batch", "B_RUN_BAD_S", "--stage", "no_such_stage")
    assert r.returncode != 0
    assert "unknown stage" in (r.stderr + r.stdout).lower()


def test_cli_run_word_filter(at_head):
    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["apple", "banana"], batch_id="B_RUN_W")
    finally:
        engine.dispose()
    r = _wordforge("run", "--batch", "B_RUN_W", "--word", "apple")
    assert r.returncode == 0, r.stderr
    assert "1 words" in r.stdout


def test_cli_run_word_casefold_matches_ingest(at_head):
    """Round 1 D3: CLI filter must use casefold, matching ingest.normalize().

    Python's str.casefold() converts ß → 'ss' (unlike str.lower() which
    leaves ß alone). So ingesting 'Straße' stores normalized_form='strasse',
    and a user typing '--word STRASSE' casefolds to 'strasse' — both sides
    converge."""
    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["Straße"], batch_id="B_RUN_CF")
    finally:
        engine.dispose()
    r = _wordforge("run", "--batch", "B_RUN_CF", "--word", "STRASSE")
    assert r.returncode == 0, r.stderr
    assert "1 words" in r.stdout


def test_cli_run_word_not_found_fails_fast(at_head):
    engine = make_engine()
    try:
        _ensure_batch(engine, "B_RUN_NFW")
    finally:
        engine.dispose()
    r = _wordforge("run", "--batch", "B_RUN_NFW", "--word", "ghost")
    assert r.returncode != 0
    assert "word" in (r.stderr + r.stdout).lower()


def test_cli_run_fetch_dict_phonetic_end_to_end(at_head):
    """P5a Task 3: `wordforge run --batch B` actually produces stage_artifacts
    for fetch_dict + phonetic.

    Uses a VCR-style env flag to stub YoudaoClient — CI never hits real network.
    """
    import os

    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["apple"], batch_id="B_E2E")
    finally:
        engine.dispose()

    env = os.environ.copy()
    env.setdefault(
        "DATABASE_URL", "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge"
    )
    # P5a test stub: force FetchDictStage to use fake.
    env["WORDFORGE_STUB_YOUDAO_JSON"] = (
        '{"simple":{"word":[{"usphone":"ˈæpl","ukphone":"ˈæpl",'
        '"usspeech":"apple&type=2","ukspeech":"apple&type=1"}]}}'
    )
    # P5c: unset LLM provider env vars so registry skips LLM + export stages,
    # keeping this test scoped to fetch_dict + phonetic (the P5a promise).
    for k in _LLM_PROVIDER_ENV_KEYS:
        env.pop(k, None)

    r = subprocess.run(
        ["uv", "run", "wordforge", "run", "--batch", "B_E2E"],
        cwd=WORDFORGE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "run complete" in r.stdout
    # Expect 1 word × 2 non-LLM stages = 2 events, all ok. (LLM stages
    # skipped because no provider env is set.)
    assert "ok_events=2" in r.stdout

    # Verify rows landed.
    engine = make_engine()
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT stage_name FROM pipeline.stage_artifacts a "
                    "JOIN pipeline.words w ON a.word_id = w.id "
                    "WHERE w.batch_id = 'B_E2E' ORDER BY stage_name"
                )
            ).all()
        assert [r[0] for r in rows] == ["fetch_dict", "phonetic"]
    finally:
        engine.dispose()


# --- P7 Task 1 DLQ CLI tests ---


def test_cli_dlq_list_empty(at_head):
    """dlq list with no dead_letter rows prints a friendly message."""
    r = _wordforge("dlq", "list")
    assert r.returncode == 0, r.stderr
    assert "no unresolved dead_letter rows" in r.stdout


def test_cli_dlq_list_shows_open(at_head):
    """dlq list shows open rows."""
    from wordforge.dlq import DeadLetterStore

    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["dlqword"], batch_id="B_DLQ_L")
        with engine.connect() as conn:
            wid = conn.execute(
                sa.text("SELECT id FROM pipeline.words WHERE batch_id = 'B_DLQ_L' LIMIT 1")
            ).scalar_one()
        DeadLetterStore(engine).record(
            word_id=wid, stage_name="paraphrase", error="test error msg", attempt=3
        )
    finally:
        engine.dispose()

    r = _wordforge("dlq", "list")
    assert r.returncode == 0, r.stderr
    assert "paraphrase" in r.stdout
    assert "test error msg" in r.stdout
    assert f"word_id={wid}" in r.stdout


def test_cli_dlq_replay_resolves(at_head):
    """dlq replay resolves open rows and reports count."""
    from wordforge.dlq import DeadLetterStore

    engine = make_engine()
    try:
        ingest_words(engine, raw_forms=["dlqreplay"], batch_id="B_DLQ_R")
        with engine.connect() as conn:
            wid = conn.execute(
                sa.text("SELECT id FROM pipeline.words WHERE batch_id = 'B_DLQ_R' LIMIT 1")
            ).scalar_one()
        store = DeadLetterStore(engine)
        store.record(word_id=wid, stage_name="s1", error="e", attempt=3)
        store.record(word_id=wid, stage_name="s2", error="e", attempt=4)
    finally:
        engine.dispose()

    r = _wordforge("dlq", "replay", "--word-id", str(wid))
    assert r.returncode == 0, r.stderr
    assert "2 dead_letter rows resolved" in r.stdout
    assert f"word_id={wid}" in r.stdout


def test_cli_dlq_replay_unknown_word_fails(at_head):
    """dlq replay on unknown word_id exits non-zero with error message."""
    r = _wordforge("dlq", "replay", "--word-id", "99999")
    assert r.returncode != 0
    assert "unknown word_id" in (r.stderr + r.stdout).lower()
