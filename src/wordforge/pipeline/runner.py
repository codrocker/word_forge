"""StageRunner — serial stages, asyncio.gather + Semaphore per stage.

Spec §5 "执行模式": "stage 之间严格串行；stage 内部对多个独立词用
asyncio.gather + Semaphore(5) 限流". Fixed at DEFAULT_CONCURRENCY = 5 in P3
(Round 2 R2-D3: no per-stage Protocol override; future yaml-driven per-stage
tuning will flow through runner constructor args, not Stage Protocol).

No retry, no DLQ writes (P5). Failed words just log `failed` to stage_runs
and skip UPSERT of stage_artifacts (so next run will retry, fingerprint-miss
style).

Round 1 battle decisions:
- D1: no StageContext. batch_id / force are runner-local state.
- D2: fingerprint-hit ⇒ return silently, NO record_skipped. `skipped` status
      is P5 Export-Case-C only (spec §3 L138 / §5 L325 / L374).
- U-gem-3: per-word try/except wraps expected_fingerprint() + should_skip()
      + run_one() so a single-word crash cannot cancel sibling coroutines
      via asyncio.gather.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.pipeline.budget import BudgetGate
from wordforge.pipeline.protocols import Stage, StagePayload
from wordforge.pipeline.runs import StageRunStore

if TYPE_CHECKING:
    from wordforge.dlq import DeadLetterStore

DEFAULT_CONCURRENCY = 5
# Every N completed events in a stage triggers a progress line. Also always
# logged at stage start / stage end. Override via WORDFORGE_PROGRESS_EVERY.
PROGRESS_EVERY = int(os.environ.get("WORDFORGE_PROGRESS_EVERY", "100"))


def _fmt_dur(secs: float) -> str:
    s = max(0, int(secs))
    h, r = divmod(s, 3600)
    m, ss = divmod(r, 60)
    return f"{h}h{m:02d}m{ss:02d}s" if h else f"{m}m{ss:02d}s"


def _emit(line: str) -> None:
    """Single-line progress print to stdout, unbuffered. tail -f friendly."""
    print(line, flush=True)
    # stderr duplicate so `2>logfile` tails still see it when stdout is
    # captured by caller pipelines
    sys.stdout.flush()


@dataclass
class _StageProgress:
    """Lightweight counters the `one` coroutine increments. The run loop
    reads these under the asyncio single-thread model (no lock needed) to
    emit `[N/total] ...` lines every PROGRESS_EVERY completions.
    """
    stage_name: str
    total: int
    started_at: float
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    last_logged: int = 0

    @property
    def done(self) -> int:
        return self.ok + self.skipped + self.failed

    def maybe_log(self) -> None:
        n = self.done
        if n == 0:
            return
        if n - self.last_logged < PROGRESS_EVERY and n != self.total:
            return
        self.last_logged = n
        elapsed = time.monotonic() - self.started_at
        rate = n / elapsed if elapsed > 0 else 0.0
        remaining = self.total - n
        eta_secs = remaining / rate if rate > 0 else 0.0
        eta_dt = _dt.datetime.now() + _dt.timedelta(seconds=eta_secs)
        _emit(
            f"  [{self.stage_name} {n}/{self.total}] "
            f"ok={self.ok} skipped={self.skipped} failed={self.failed} | "
            f"elapsed={_fmt_dur(elapsed)} rate={rate:.2f}w/s "
            f"eta={_fmt_dur(eta_secs)}@{eta_dt.strftime('%m-%d %H:%M')}"
        )


@dataclass
class RunResult:
    """Round 3 R3-C2 + Round 4 R4-gem-3: per-run summary. Counters are per
    (word × stage) EVENTS — a 2-stage run over 3 words tallies 6 events total.
    The `_events` suffix is explicit so CLI output like `skipped_events=800000`
    isn't misread as "800k distinct words". CLI caller divides by len(stages)
    to recover per-word numbers when that's what the operator wants.

    `skipped_events` counts fingerprint-hit silent skips (no DB write in
    `stage_runs`; Round 2 R2-D2 decision)."""

    ok_events: int = 0
    failed_events: int = 0
    skipped_events: int = 0


@dataclass
class StageRunner:
    artifacts: StageArtifactStore
    runs: StageRunStore
    budget: BudgetGate
    dlq: DeadLetterStore | None = None
    concurrency: int = DEFAULT_CONCURRENCY

    async def run(
        self,
        *,
        stages: Sequence[Stage],
        word_ids: Sequence[int],
        batch_id: str | None,
        force: bool = False,
    ) -> RunResult:
        """Run stages serially, filtering failed words out between stages.

        Round 4 R4-gem-1: spec §5 L296-305 stage dependency DAG — a word that
        failed Stage N is not a valid candidate for Stage N+1 because its
        `expected_fingerprint` would depend on a never-written upstream
        artifact. We maintain a `surviving` list: ok + fingerprint-hit words
        continue; failed words drop out until next `--force` rerun.
        """
        result = RunResult()
        surviving: list[int] = list(word_ids)
        for stage in stages:
            # Spec §6 "Budget 熔断": check once per stage, before any task runs.
            # If the PREVIOUS stage blew the cap, this raises → overall run halts.
            self.budget.check(batch_id)
            surviving = await self._run_stage(
                stage,
                surviving,
                batch_id=batch_id,
                force=force,
                result=result,
            )
        return result

    async def _run_stage(
        self,
        stage: Stage,
        word_ids: Sequence[int],
        *,
        batch_id: str | None,
        force: bool,
        result: RunResult,
    ) -> list[int]:
        """Return the list of words that are eligible for the next stage
        (ok + fingerprint-hit). Failed words are filtered out so downstream
        stages don't hit missing-upstream errors (Round 4 R4-gem-1)."""
        # Per-stage concurrency override removed (Round 2 R2-D3); spec §5 L309
        # default is 5 but StageRunner.concurrency is now operator-tunable via
        # CLI `--concurrency` to ride out the LLM-RTT-bound stages (examples,
        # mnemonic) when Bedrock has headroom. Keeps spec's original shape
        # (one semaphore, per-stage wait) but lets large batches finish in
        # minutes instead of hours.
        sem = asyncio.Semaphore(self.concurrency)
        # Survivors are ok + skipped; failures drop out. Append order is
        # coroutine-completion order under the semaphore, NOT original input
        # order (Round 5 R5-codex-P3 clarification). Downstream stages don't
        # depend on order and tests in this plan assert `sorted(...)`, so
        # this is fine. Safe without a lock: asyncio is single-thread event
        # loop; no concurrent append at the Python bytecode level.
        survivors: list[int] = []

        progress = _StageProgress(
            stage_name=stage.name,
            total=len(word_ids),
            started_at=time.monotonic(),
        )
        _emit(f"  [{stage.name} start] words={progress.total} concurrency={self.concurrency}")

        async def one(word_id: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                # Round 2 R2-C1 + U-gem-3: whole-coroutine try/except —
                # wraps fingerprint, skip check, stage.run_one AND the post-
                # run DB writes (artifacts.upsert + runs.record_ok). Any
                # exception is localized to this word's failed row; nothing
                # escapes to asyncio.gather to cancel siblings.
                try:
                    expected_fp = stage.expected_fingerprint(word_id=word_id)
                    if not force and self.artifacts.should_skip(
                        word_id=word_id,
                        stage_name=stage.name,
                        expected_fingerprint=expected_fp,
                    ):
                        result.skipped_events += 1  # R3-C2 counter (no DB write)
                        survivors.append(word_id)  # still valid for next stage
                        progress.skipped += 1
                        progress.maybe_log()
                        return  # D2: fingerprint hit ⇒ silent skip, no stage_runs write
                    payload = await stage.run_one(word_id=word_id)
                    self.artifacts.upsert(
                        word_id=word_id,
                        stage_name=stage.name,
                        fingerprint=expected_fp,
                        payload=payload.payload,
                        source=payload.source,
                        model=payload.model,
                        prompt_version=payload.prompt_version,
                    )
                    self.runs.record_ok(
                        batch_id=batch_id,
                        word_id=word_id,
                        stage_name=stage.name,
                        model=payload.model,
                        tokens_in=payload.tokens_in,
                        tokens_out=payload.tokens_out,
                        cost_usd=payload.cost_usd,
                        duration_ms=payload.duration_ms,
                    )
                    result.ok_events += 1
                    survivors.append(word_id)
                    progress.ok += 1
                    progress.maybe_log()
                except Exception as exc:  # noqa: BLE001
                    duration = int((time.perf_counter() - t0) * 1000)
                    # Round 3 R3-gem-3: `format_exc(limit=10)` limits stack
                    # depth safely (preserves exception structure, no UTF-8
                    # mid-char slicing, keeps root cause of chained exceptions).
                    # Round 2 R2-U-gem-2: diagnostic info so P5 parser bugs
                    # are recoverable from stage_runs.error alone.
                    tb = traceback.format_exc(limit=10)
                    error = f"{type(exc).__name__}: {exc}\n{tb}"
                    # Round 3 R3-C1: double-fault guard. record_failed itself
                    # opens a new txn; if the DB is still down, we MUST NOT
                    # let its exception escape to asyncio.gather (cancels
                    # sibling coroutines). Swallow + local log; this word's
                    # failure is lost from stage_runs but other words survive.
                    try:
                        self.runs.record_failed(
                            batch_id=batch_id,
                            word_id=word_id,
                            stage_name=stage.name,
                            error=error,
                            duration_ms=duration,
                        )
                    except Exception:  # noqa: BLE001
                        logging.exception(
                            "double-fault recording stage failure (word_id=%s stage=%s)",
                            word_id,
                            stage.name,
                        )
                    # P7: auto-record dead_letter on 3rd failure
                    if self.dlq is not None:
                        try:
                            attempt = self.runs.failed_attempt_count(
                                word_id=word_id,
                                stage_name=stage.name,
                                batch_id=batch_id,
                            )
                            if attempt == 3:
                                self.dlq.record(
                                    word_id=word_id,
                                    stage_name=stage.name,
                                    error=error,
                                    attempt=attempt,
                                )
                        except Exception:  # noqa: BLE001
                            logging.exception(
                                "failed to record dead_letter (word_id=%s stage=%s)",
                                word_id,
                                stage.name,
                            )
                    result.failed_events += 1
                    progress.failed += 1
                    progress.maybe_log()
                    # survivors: do not append; failed word drops from next stage

        # Round 3 R3-codex-7: for 10万词 we allocate 10万 coroutine objects
        # up-front here. `Semaphore` caps concurrent execution to 5, but the
        # coroutine objects themselves live in memory until scheduled.
        # Coroutine memory (~few hundred bytes each) ≈ 30-60 MB on the high
        # end — acceptable for single-machine 100k scale. If P5 profiling
        # shows pressure, introduce a chunked gather loop here.
        await asyncio.gather(*(one(w) for w in word_ids))
        # Final progress line for this stage (always logged, regardless of
        # PROGRESS_EVERY boundary).
        elapsed = time.monotonic() - progress.started_at
        _emit(
            f"  [{stage.name} complete] ok={progress.ok} skipped={progress.skipped} "
            f"failed={progress.failed} elapsed={_fmt_dur(elapsed)}"
        )
        # survivors may be in completion (not input) order; tests assert
        # `sorted(...)` to stay robust.
        return survivors
