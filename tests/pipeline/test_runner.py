"""StageRunner: serial stages, Semaphore(5) intra-stage, skip on matching
fingerprint, UPSERT on miss, failure-isolated."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
import sqlalchemy as sa

from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.pipeline.budget import BudgetGate
from wordforge.pipeline.protocols import StagePayload
from wordforge.pipeline.runner import StageRunner
from wordforge.pipeline.runs import StageRunStore


@dataclass
class FakeStage:
    name: str
    fingerprint_of: dict[int, str] = field(default_factory=dict)
    raises_for: set[int] = field(default_factory=set)
    raises_in_fp: set[int] = field(default_factory=set)
    calls: list[int] = field(default_factory=list)
    concurrent_peak: int = 0
    _in_flight: int = 0
    # Py 3.10+ `asyncio.Lock()` lazy-binds to the running loop on first
    # acquire(); it is safe to construct here via dataclass field_factory
    # outside any event loop (Round 5 gemini battle concede).
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def expected_fingerprint(self, *, word_id: int) -> str:
        if word_id in self.raises_in_fp:
            raise KeyError(f"missing config for {word_id}")
        return self.fingerprint_of.get(word_id, f"fp-{self.name}-{word_id}")

    async def run_one(self, *, word_id: int) -> StagePayload:
        async with self._lock:
            self._in_flight += 1
            self.concurrent_peak = max(self.concurrent_peak, self._in_flight)
        try:
            await asyncio.sleep(0.01)
            if word_id in self.raises_for:
                raise RuntimeError(f"boom for {word_id}")
            self.calls.append(word_id)
            return StagePayload(
                payload={"stage": self.name, "w": word_id},
                source=f"pipeline:fake:{self.name}_v1",
                model="fake-1",
                prompt_version="v1",
                cost_usd=0.001,
                tokens_in=10,
                tokens_out=10,
                duration_ms=10,
            )
        finally:
            async with self._lock:
                self._in_flight -= 1


def _seed_batch_and_words(
    engine, *, n_words: int, batch_id: str = "B1", cap: float | None = None
) -> list[int]:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches (id, label, budget_cap_usd) VALUES (:id, :id, :cap)"
            ),
            {"id": batch_id, "cap": cap},
        )
        ids = []
        for i in range(n_words):
            r = conn.execute(
                sa.text(
                    "INSERT INTO pipeline.words "
                    "(raw_form, normalized_form, type, batch_id) "
                    "VALUES (:f, :f, 1, :b) RETURNING id"
                ),
                {"f": f"word{i}", "b": batch_id},
            )
            ids.append(r.scalar())
    return ids


def _runner(engine) -> StageRunner:
    return StageRunner(
        artifacts=StageArtifactStore(engine),
        runs=StageRunStore(engine),
        budget=BudgetGate(engine),
    )


def test_single_stage_runs_all_words(at_head):
    ids = _seed_batch_and_words(at_head, n_words=3)
    stage = FakeStage(name="paraphrase")
    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1"))
    assert sorted(stage.calls) == ids

    with at_head.connect() as conn:
        n_art = conn.execute(sa.text("SELECT count(*) FROM pipeline.stage_artifacts")).scalar()
        n_runs_ok = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE status='ok'")
        ).scalar()
        total = conn.execute(
            sa.text("SELECT total_cost_usd FROM pipeline.batches WHERE id='B1'")
        ).scalar()
    assert n_art == 3
    assert n_runs_ok == 3
    assert float(total) == pytest.approx(0.003)


def test_semaphore_limits_concurrency(at_head):
    """Round 2 R2-D3: runner固定 DEFAULT_CONCURRENCY=5, no per-stage override."""
    ids = _seed_batch_and_words(at_head, n_words=20)
    stage = FakeStage(name="paraphrase")
    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1"))
    assert stage.concurrent_peak <= 5
    assert stage.concurrent_peak >= 2  # gather actually parallelized some


def test_stages_run_serially(at_head):
    ids = _seed_batch_and_words(at_head, n_words=3)
    order: list[str] = []

    @dataclass
    class OrderedStage:
        name: str

        def expected_fingerprint(self, *, word_id):
            return f"fp-{self.name}-{word_id}"

        async def run_one(self, *, word_id):
            order.append(f"{self.name}:start:{word_id}")
            await asyncio.sleep(0.01)
            order.append(f"{self.name}:end:{word_id}")
            return StagePayload(
                payload={},
                source="pipeline:x:y",
                model=None,
                prompt_version=None,
                cost_usd=0.0,
                tokens_in=None,
                tokens_out=None,
                duration_ms=1,
            )

    s1 = OrderedStage(name="fetch_dict")
    s2 = OrderedStage(name="paraphrase")
    asyncio.run(_runner(at_head).run(stages=[s1, s2], word_ids=ids, batch_id="B1"))

    # All fetch_dict events must finish before any paraphrase event starts.
    last_fetch = max(i for i, e in enumerate(order) if e.startswith("fetch_dict"))
    first_para = min(i for i, e in enumerate(order) if e.startswith("paraphrase"))
    assert last_fetch < first_para


def test_skip_on_fingerprint_match(at_head):
    """Round 1 D2 battle: fingerprint hit ⇒ runner returns silently, NO
    stage_runs row is written. `skipped` status is reserved for P5 Export
    Case C (human/import takeover).
    """
    ids = _seed_batch_and_words(at_head, n_words=2)
    stage = FakeStage(name="paraphrase")

    StageArtifactStore(at_head).upsert(
        word_id=ids[0],
        stage_name="paraphrase",
        fingerprint=stage.expected_fingerprint(word_id=ids[0]),
        payload={"old": True},
        source="pipeline:x:y",
    )

    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1"))
    assert stage.calls == [ids[1]]  # only the unskipped word was run

    with at_head.connect() as conn:
        any_skipped = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE status='skipped'")
        ).scalar()
        run_for_skipped_word = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert any_skipped == 0, "runner must not write skipped rows on fingerprint hit"
    assert run_for_skipped_word == 0, "skipped word should have zero stage_runs"


def test_force_bypasses_fingerprint_skip(at_head):
    ids = _seed_batch_and_words(at_head, n_words=1)
    stage = FakeStage(name="paraphrase")
    StageArtifactStore(at_head).upsert(
        word_id=ids[0],
        stage_name="paraphrase",
        fingerprint=stage.expected_fingerprint(word_id=ids[0]),
        payload={"old": True},
        source="pipeline:x:y",
    )

    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1", force=True))
    assert stage.calls == [ids[0]]  # force made us rerun


def test_fingerprint_exception_isolated(at_head):
    """Round 1 U-gem-3 P1: if expected_fingerprint() raises, the per-word
    failure must NOT cancel sibling coroutines in asyncio.gather."""
    ids = _seed_batch_and_words(at_head, n_words=3)
    stage = FakeStage(name="paraphrase", raises_in_fp={ids[1]})

    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1"))

    # Siblings still ran — the KeyError inside expected_fingerprint did not
    # cancel anyone.
    assert sorted(stage.calls) == [ids[0], ids[2]]

    with at_head.connect() as conn:
        row = (
            conn.execute(
                sa.text("SELECT status, error FROM pipeline.stage_runs WHERE word_id = :w"),
                {"w": ids[1]},
            )
            .mappings()
            .first()
        )
    assert row["status"] == "failed"
    assert "KeyError" in row["error"]


def test_failure_isolated_per_word(at_head):
    ids = _seed_batch_and_words(at_head, n_words=3)
    stage = FakeStage(name="paraphrase", raises_for={ids[1]})
    asyncio.run(_runner(at_head).run(stages=[stage], word_ids=ids, batch_id="B1"))
    assert sorted(stage.calls) == [ids[0], ids[2]]

    with at_head.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT word_id, status, error FROM pipeline.stage_runs ORDER BY word_id")
        ).all()
    by_word = {r[0]: (r[1], r[2]) for r in rows}
    assert by_word[ids[0]] == ("ok", None)
    assert by_word[ids[1]][0] == "failed"
    assert "boom" in by_word[ids[1]][1]
    assert by_word[ids[2]] == ("ok", None)


def test_budget_exceeded_halts_before_next_stage(at_head):
    ids = _seed_batch_and_words(at_head, n_words=2, cap=0.0015)
    s1 = FakeStage(name="fetch_dict")
    s2 = FakeStage(name="paraphrase")
    from wordforge.pipeline.budget import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        asyncio.run(_runner(at_head).run(stages=[s1, s2], word_ids=ids, batch_id="B1"))
    # s1 ran (cost=$0.002 > cap but check is before-stage, s1 passed because
    # before s1 runs total=0 < 0.0015). After s1 total=0.002 > cap, so s2 blocked.
    assert sorted(s1.calls) == ids
    assert s2.calls == []


def test_artifact_without_run_is_skipped_next_time_but_force_recovers(at_head):
    """Round 3 R3-codex-2 battle verdict (方案 B): spec §6 L437-441 accepts
    "崩了最多丢一两条没记账". This test documents and locks that semantics.

    Scenario: artifact.upsert commits successfully, but the subsequent
    record_ok fails (simulated by pointing the runs store at a doomed batch).
    The artifact row persists; next run hits fingerprint and silently skips;
    `--force` recovers by bypassing the fingerprint skip.
    """
    ids = _seed_batch_and_words(at_head, n_words=1)
    stage = FakeStage(name="paraphrase")

    # 1. Pre-seed an artifact with the fingerprint the stage will compute —
    #    simulates "upsert succeeded" from a prior crashed run.
    fp = stage.expected_fingerprint(word_id=ids[0])
    StageArtifactStore(at_head).upsert(
        word_id=ids[0],
        stage_name="paraphrase",
        fingerprint=fp,
        payload={"stale": True},
        source="pipeline:x:y",
    )

    # 2. Default re-run: fingerprint hit ⇒ silent skip, stage.run_one never
    #    invoked. Artifact-without-run state is preserved.
    asyncio.run(
        _runner(at_head).run(
            stages=[stage],
            word_ids=ids,
            batch_id="B1",
        )
    )
    assert stage.calls == []
    with at_head.connect() as conn:
        n_runs = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert n_runs == 0  # never ran

    # 3. --force: bypasses fingerprint skip; stage actually runs; artifact
    #    overwritten; stage_runs now has the ok row; budget accumulation
    #    catches up. State fully recovered.
    asyncio.run(
        _runner(at_head).run(
            stages=[stage],
            word_ids=ids,
            batch_id="B1",
            force=True,
        )
    )
    assert stage.calls == [ids[0]]
    with at_head.connect() as conn:
        ok_count = conn.execute(
            sa.text(
                "SELECT count(*) FROM pipeline.stage_runs WHERE word_id = :w AND status = 'ok'"
            ),
            {"w": ids[0]},
        ).scalar()
    assert ok_count == 1


def test_cascading_failures_filter_surviving_across_stages(at_head):
    """Round 4 R4-gem-1: a word that fails Stage N must not be passed to
    Stage N+1 (which would read a missing upstream fingerprint and produce
    a spurious downstream failure). Runner.run must filter survivors."""
    ids = _seed_batch_and_words(at_head, n_words=3)
    s1 = FakeStage(name="fetch_dict", raises_for={ids[1]})
    s2 = FakeStage(name="paraphrase")

    asyncio.run(
        _runner(at_head).run(
            stages=[s1, s2],
            word_ids=ids,
            batch_id="B1",
        )
    )

    # Stage 1: word 1 failed; words 0 and 2 ran ok.
    assert sorted(s1.calls) == [ids[0], ids[2]]
    # Stage 2: should NOT receive the failed word 1; only 0 and 2.
    assert sorted(s2.calls) == [ids[0], ids[2]]

    with at_head.connect() as conn:
        stage_runs = conn.execute(
            sa.text(
                "SELECT word_id, stage_name, status FROM pipeline.stage_runs "
                "ORDER BY stage_name, word_id"
            )
        ).all()
    # Expected: (0,fetch_dict,ok) (2,fetch_dict,ok) (1,fetch_dict,failed)
    #           (0,paraphrase,ok) (2,paraphrase,ok)
    # word 1 has NO row for paraphrase (was filtered out before reaching it).
    word1_para = [r for r in stage_runs if r[0] == ids[1] and r[1] == "paraphrase"]
    assert word1_para == [], "failed word should not have any paraphrase row"


# --- P7 Task 2: auto-DLQ on 3rd failure ---


def _runner_with_dlq(engine) -> StageRunner:
    from wordforge.dlq import DeadLetterStore

    return StageRunner(
        artifacts=StageArtifactStore(engine),
        runs=StageRunStore(engine),
        budget=BudgetGate(engine),
        dlq=DeadLetterStore(engine),
    )


def test_runner_records_dlq_on_third_failure(at_head):
    """After 3 failed stage_runs for (word_id, stage_name), DLQ row is written."""
    ids = _seed_batch_and_words(at_head, n_words=1, batch_id="B_DLQ3")
    stage = FakeStage(name="paraphrase", raises_for={ids[0]})
    runner = _runner_with_dlq(at_head)

    # Run 3 times (each run produces 1 failed stage_run)
    for _ in range(3):
        asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQ3", force=True))

    with at_head.connect() as conn:
        dlq_rows = conn.execute(
            sa.text(
                "SELECT word_id, stage_name, attempt FROM pipeline.dead_letter WHERE word_id = :w"
            ),
            {"w": ids[0]},
        ).all()
    assert len(dlq_rows) == 1
    assert dlq_rows[0][0] == ids[0]
    assert dlq_rows[0][1] == "paraphrase"
    assert dlq_rows[0][2] == 3


def test_runner_does_not_dlq_on_first_failure(at_head):
    """1 failure should NOT trigger DLQ."""
    ids = _seed_batch_and_words(at_head, n_words=1, batch_id="B_DLQ1")
    stage = FakeStage(name="paraphrase", raises_for={ids[0]})
    runner = _runner_with_dlq(at_head)

    asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQ1", force=True))

    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.dead_letter WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert n == 0


def test_runner_no_dlq_when_store_none(at_head):
    """dlq=None (default) preserves old behavior — no DLQ write even on 3 failures."""
    ids = _seed_batch_and_words(at_head, n_words=1, batch_id="B_DLQN")
    stage = FakeStage(name="paraphrase", raises_for={ids[0]})
    runner = _runner(at_head)  # uses default dlq=None

    for _ in range(3):
        asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQN", force=True))

    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.dead_letter WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert n == 0


def test_runner_does_not_duplicate_dlq_on_fourth_fifth_attempt(at_head):
    """Ensure only exactly attempt==3 creates a dead_letter row; attempts 4, 5 skip."""
    ids = _seed_batch_and_words(at_head, n_words=1, batch_id="B_DLQ5")
    stage = FakeStage(name="paraphrase", raises_for={ids[0]})
    runner = _runner_with_dlq(at_head)

    # Run 5 times (each run produces 1 failed stage_run)
    for _ in range(5):
        asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQ5", force=True))

    with at_head.connect() as conn:
        dlq_count = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.dead_letter WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert dlq_count == 1, f"expected exactly 1 DLQ row, got {dlq_count}"


def test_failed_attempt_count_resets_after_dlq_replay(at_head):
    """After a dlq replay, historical failures no longer count toward next DLQ."""
    from wordforge.dlq import DeadLetterStore
    from wordforge.pipeline.runs import StageRunStore

    ids = _seed_batch_and_words(at_head, n_words=1, batch_id="B_DLQ_RESET")
    stage = FakeStage(name="paraphrase", raises_for={ids[0]})
    runner = _runner_with_dlq(at_head)

    # 1. Seed 3 failures (triggers DLQ)
    for _ in range(3):
        asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQ_RESET", force=True))

    # Verify DLQ was recorded
    with at_head.connect() as conn:
        dlq_count = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.dead_letter WHERE word_id = :w"),
            {"w": ids[0]},
        ).scalar()
    assert dlq_count == 1

    # 2. Replay (marks resolved)
    DeadLetterStore(at_head).replay(word_id=ids[0])

    # 3. Seed 1 more failure (fresh post-replay)
    asyncio.run(runner.run(stages=[stage], word_ids=ids, batch_id="B_DLQ_RESET", force=True))

    # 4. Assert failed_attempt_count returns 1 (not 4)
    runs = StageRunStore(at_head)
    count = runs.failed_attempt_count(
        word_id=ids[0], stage_name="paraphrase", batch_id="B_DLQ_RESET"
    )
    assert count == 1, f"expected 1 post-replay failure, got {count}"
