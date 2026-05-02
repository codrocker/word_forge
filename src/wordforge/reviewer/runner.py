"""Review orchestrator — asyncio Queue + N worker coros + heartbeat + watchdog.

Public API: `run_review(targets, engine, llm, output_path, apply_,
word_concurrency, call_concurrency, call_timeout) -> int`.

Returns 0 on clean completion, 2 on watchdog-detected stall. Writes
per-word results as jsonl into `output_path` (append mode — pair with
a --skip-done-from pointing at the same file for resume).

Design choices, all driven by prod incidents:
- Queue + N workers, not asyncio.gather over 100k coroutines: bounds
  memory and gives the heartbeat task room on the event loop.
- Per-call asyncio.wait_for + independent watchdog task: a half-dead
  SOCKS5 proxy (mainland-China reality) doesn't get to hang us forever.
- Per-item try/except in worker: one bad word must not kill the worker
  or halt queue draining. Every failure lands in jsonl so a later
  --skip-done-from pass picks up the rest.
- Explicit ThreadPoolExecutor with sized cap: default asyncio executor
  caps at min(32, cpu*5) and doesn't expose sizing; under heavy
  to_thread traffic we want control.
- Output stream + async Lock: not using a sync lock because workers run
  on the event loop, not threads.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import datetime as _dt
import json
import sys
from typing import Any

from wordforge.llm.client import LLMClient
from wordforge.reviewer.config import CFG
from wordforge.reviewer.worker import run_one_word


def fmt_dur(secs: float) -> str:
    """Format seconds as 'HHhMMmSSs' (or 'MMmSSs' under an hour)."""
    secs = max(0, int(secs))
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def progress_stats(
    started_at: float, now: float, done: int, total: int
) -> dict[str, str]:
    """Compute elapsed / rate / ETA strings.

    Single source of truth — both the interactive progress line (every
    PROGRESS_EVERY words) and heartbeat (every HEARTBEAT_SECS) format time
    the same way.
    """
    elapsed = now - started_at
    remaining = max(0, total - done)
    if done > 0 and elapsed > 0:
        rate = done / elapsed
        eta_secs = remaining / rate if rate > 0 else 0.0
        eta_dt = _dt.datetime.now() + _dt.timedelta(seconds=eta_secs)
        return {
            "elapsed": fmt_dur(elapsed),
            "rate": f"{rate:.2f}w/s",
            "eta": f"{fmt_dur(eta_secs)}@{eta_dt.strftime('%m-%d %H:%M')}",
        }
    return {"elapsed": fmt_dur(elapsed), "rate": "—", "eta": "unknown"}


async def run_review(
    *,
    targets: list[tuple[str | None, int | None]],
    engine,
    llm: LLMClient,
    output_path: str,
    apply_: bool,
    word_concurrency: int,
    call_concurrency: int,
    call_timeout: float,
) -> int:
    """Main orchestrator. Returns exit code (0 ok, 2 stall)."""
    loop = asyncio.get_running_loop()
    tp = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(word_concurrency * 4 + 8, 32),
        thread_name_prefix="rv-io",
    )
    loop.set_default_executor(tp)

    totals: dict[str, Any] = {
        "haiku_cost": 0.0, "opus_cost": 0.0,
        "done": 0, "with_issues": 0, "with_patches": 0,
        "applied_rows": 0, "deletes": 0,
        "drift_skipped": 0, "errors": 0,
    }
    started_at = loop.time()
    last_progress = started_at
    stall_event = asyncio.Event()

    q: asyncio.Queue[tuple[str | None, int | None] | None] = asyncio.Queue()
    for t in targets:
        q.put_nowait(t)
    # One sentinel per worker so they exit cleanly when the queue drains.
    for _ in range(word_concurrency):
        q.put_nowait(None)

    sem = asyncio.Semaphore(call_concurrency)

    # stdout/stderr already line-buffered under `python -u`, but we also
    # explicitly flush after each write so tail -f is truly real-time.
    out = open(output_path, "a", encoding="utf-8")  # noqa: SIM115  (closed in finally)
    out_lock = asyncio.Lock()

    async def worker(wid: int) -> None:
        nonlocal last_progress
        while True:
            item = await q.get()
            try:
                if item is None:
                    return
                form, word_id = item
                try:
                    rec = await run_one_word(
                        engine, llm, form, word_id, apply_, sem, call_timeout
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    # Per-item fail-soft: one bad word must NOT kill the
                    # worker or halt queue draining. Every failure lands in
                    # jsonl so a later --skip-done-from pass picks it up.
                    rec = {
                        "form": form,
                        "word_id": word_id,
                        "error": f"{type(e).__name__}: {e}",
                    }
                async with out_lock:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.flush()
                    totals["done"] += 1
                    totals["haiku_cost"] += rec.get("haiku_total", 0) or 0
                    totals["opus_cost"] += rec.get("opus_cost", 0) or 0
                    if rec.get("error"):
                        totals["errors"] += 1
                    if rec.get("issues"):
                        totals["with_issues"] += 1
                    if rec.get("patches"):
                        totals["with_patches"] += 1
                    totals["applied_rows"] += rec.get("applied_count", 0) or 0
                    totals["drift_skipped"] += len(rec.get("drift_skipped") or [])
                    for p in rec.get("patches") or []:
                        if p.get("op") == "delete":
                            totals["deletes"] += 1
                    last_progress = loop.time()
                    if (
                        totals["done"] % CFG.PROGRESS_EVERY == 0
                        or totals["done"] == len(targets)
                    ):
                        ps = progress_stats(
                            started_at, loop.time(), totals["done"], len(targets)
                        )
                        print(
                            f"  [{totals['done']}/{len(targets)}] "
                            f"issues={totals['with_issues']} "
                            f"patches={totals['with_patches']} "
                            f"applied={totals['applied_rows']} "
                            f"drift_skipped={totals['drift_skipped']} "
                            f"deletes={totals['deletes']} "
                            f"errors={totals['errors']} "
                            f"haiku=${totals['haiku_cost']:.2f} "
                            f"opus=${totals['opus_cost']:.2f} "
                            f"| elapsed={ps['elapsed']} "
                            f"rate={ps['rate']} "
                            f"eta={ps['eta']}",
                            flush=True,
                        )
            finally:
                q.task_done()

    async def heartbeat() -> None:
        while not stall_event.is_set():
            await asyncio.sleep(CFG.HEARTBEAT_SECS)
            ps = progress_stats(
                started_at, loop.time(), totals["done"], len(targets)
            )
            idle = int(loop.time() - last_progress)
            # sem._value is a CPython detail but stable across 3.x; exposed
            # as "in_flight" so operators can tell if a stall is IO-bound
            # (in_flight > 0) vs queue-empty.
            in_flight = call_concurrency - sem._value
            print(
                f"  [heartbeat] done={totals['done']}/{len(targets)} "
                f"elapsed={ps['elapsed']} rate={ps['rate']} eta={ps['eta']} "
                f"idle={idle}s in_flight={in_flight}",
                flush=True,
            )

    async def watchdog() -> None:
        while not stall_event.is_set():
            await asyncio.sleep(10)
            if (
                loop.time() - last_progress >= CFG.STALL_SECS
                and totals["done"] < len(targets)
            ):
                msg = (
                    f"PIPELINE STALLED: no word completed for "
                    f"{int(loop.time() - last_progress)}s. "
                    f"done={totals['done']}/{len(targets)}. "
                    f"Restart the proxy (port 1082) and re-run with "
                    f"--skip-done-from {output_path} to resume."
                )
                print(msg, file=sys.stderr, flush=True)
                stall_event.set()
                return

    workers = [asyncio.create_task(worker(i)) for i in range(word_concurrency)]
    hb_task = asyncio.create_task(heartbeat())
    wd_task = asyncio.create_task(watchdog())

    try:
        # Wait for either all workers done OR watchdog stall.
        workers_done = asyncio.gather(*workers)
        stall_wait = asyncio.create_task(stall_event.wait())
        await asyncio.wait(
            [workers_done, stall_wait], return_when=asyncio.FIRST_COMPLETED
        )
        if stall_event.is_set():
            # Cancel workers; partial jsonl is already on disk.
            for w in workers:
                w.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await workers_done
            return 2
        # Happy path: make sure stall_wait is cleaned up.
        stall_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stall_wait
    finally:
        hb_task.cancel()
        wd_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task
        with contextlib.suppress(asyncio.CancelledError):
            await wd_task
        out.flush()
        out.close()
        tp.shutdown(wait=False, cancel_futures=True)

    print(
        f"\nDone. haiku=${totals['haiku_cost']:.2f} + "
        f"opus=${totals['opus_cost']:.2f} = "
        f"${totals['haiku_cost'] + totals['opus_cost']:.2f}"
    )
    print(
        f"words with issues: {totals['with_issues']}/{len(targets)} "
        f"({100 * totals['with_issues'] / max(1, len(targets)):.1f}%)"
    )
    print(
        f"applied_rows: {totals['applied_rows']}  deletes: {totals['deletes']}"
    )
    return 0
