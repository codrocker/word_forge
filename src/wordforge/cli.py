"""Typer CLI entry point.

P2 delivers only `cache prune`. Other subcommands (ingest/run/plan/dlq) are
intentionally NOT registered here — P3+ will add them when they really land.
Writing placeholder stubs now would be code that's guaranteed to be rewritten
(see Round 1 battle D-section).

Note: `from wordforge.cache import CacheStore` is imported INSIDE cache_prune
rather than at module top. Top-level import would crash `--help` and `cache
--help` for any user before they've ever needed a DB connection — the delayed
import keeps CLI discovery zero-DB.
"""

from __future__ import annotations

from datetime import timedelta

import typer

app = typer.Typer(
    help="wordforge — vocabulary-app data production pipeline.",
    no_args_is_help=True,
)
cache_app = typer.Typer(help="external_call_cache maintenance.")
app.add_typer(cache_app, name="cache")


@cache_app.command("prune")
def cache_prune(
    older_than: str = typer.Option(
        "30d", "--older-than", help="Delete cache rows older than this (e.g. 30d, 7d)."
    ),
) -> None:
    """Delete external_call_cache rows whose created_at is older than --older-than."""
    from wordforge.cache import CacheStore
    from wordforge.db.engine import make_engine

    delta = _parse_delta(older_than)
    engine = make_engine()
    try:
        n = CacheStore(engine).prune(older_than=delta)
    finally:
        engine.dispose()
    typer.echo(f"pruned {n} cache rows older than {older_than}")


def _parse_delta(spec: str) -> timedelta:
    """'30d' -> 30 days. Only 'd' is supported — wordforge cache TTL is always day-scale."""
    if not spec.endswith("d"):
        raise typer.BadParameter(f"expected <N>d (days), got {spec!r}")
    try:
        n = int(spec[:-1])
    except ValueError as e:
        raise typer.BadParameter(f"expected <N>d (days), got {spec!r}") from e
    if n < 0:
        raise typer.BadParameter("duration must be non-negative")
    return timedelta(days=n)


@app.command("ingest")
def ingest(
    path: str = typer.Argument(..., help="File with one raw word/phrase per line."),
    batch: str | None = typer.Option(
        None,
        "--batch",
        help="Attach words to batch id, creating the batch row if missing.",
    ),
) -> None:
    """Normalize words from PATH and INSERT them into pipeline.words.

    Deduplicates by (normalized_form, type). Insertion order is preserved,
    so callers that want domain.words.word_id roughly aligned with some
    upstream id should pre-sort the input file by that id.
    """
    from pathlib import Path

    from wordforge.db.engine import make_engine
    from wordforge.ingest import ingest_words

    p = Path(path)
    if not p.is_file():
        raise typer.BadParameter(f"not a regular file: {path}")
    raw_forms = p.read_text(encoding="utf-8").splitlines()

    engine = make_engine()
    try:
        res = ingest_words(engine, raw_forms=raw_forms, batch_id=batch)
    finally:
        engine.dispose()
    typer.echo(
        f"ingested: inserted={res.inserted} deduped={res.deduped} skipped_empty={res.skipped_empty}"
    )


@app.command("run")
def run_cmd(
    batch: str = typer.Option(
        ..., "--batch", help="Batch id. Must already exist (created by `wordforge ingest --batch`)."
    ),
    stage: str | None = typer.Option(
        None,
        "--stage",
        help="Limit run to a single stage (must be present in configs/default.toml).",
    ),
    word: str | None = typer.Option(
        None, "--word", help="Limit run to a single word (normalized form). Must belong to --batch."
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass stage_artifacts fingerprint skip (still uses cache)."
    ),
    concurrency: int = typer.Option(
        5,
        "--concurrency",
        help="Max concurrent words per stage (Semaphore). Default 5 matches spec; "
        "raise to 20-50 for faster large-batch runs if Bedrock quota allows.",
    ),
) -> None:
    """Run the pipeline for all words in a batch."""
    import asyncio

    import sqlalchemy as sa

    from wordforge.config import load_stage_config
    from wordforge.db.engine import make_engine
    from wordforge.pipeline.artifacts import StageArtifactStore
    from wordforge.pipeline.budget import BudgetGate
    from wordforge.pipeline.runner import StageRunner
    from wordforge.pipeline.runs import StageRunStore

    cfg = load_stage_config()
    if stage is not None and stage not in cfg.stages:
        raise typer.BadParameter(
            f"unknown stage: {stage!r}; configured: {sorted(cfg.stages.keys())}"
        )

    engine = make_engine()
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pipeline.batches WHERE id = :id"),
                {"id": batch},
            ).scalar()
            if exists is None:
                raise typer.BadParameter(
                    f"unknown batch: {batch!r}. Run `wordforge ingest <file> "
                    f"--batch {batch}` first to create the batch."
                )
            if word is not None:
                # Round 1 D3: use str.casefold() (matches wordforge.ingest.normalize)
                # — str.lower() would miss rows like "straße" → "strasse".
                word_ids = [
                    r[0]
                    for r in conn.execute(
                        sa.text(
                            "SELECT id FROM pipeline.words "
                            "WHERE batch_id = :b AND normalized_form = :n "
                            "ORDER BY id"
                        ),
                        {"b": batch, "n": word.casefold()},
                    ).all()
                ]
                if not word_ids:
                    raise typer.BadParameter(f"word {word!r} not found in batch {batch!r}")
            else:
                word_ids = [
                    r[0]
                    for r in conn.execute(
                        sa.text("SELECT id FROM pipeline.words WHERE batch_id = :b ORDER BY id"),
                        {"b": batch},
                    ).all()
                ]

        from wordforge.cache import CacheStore
        from wordforge.llm.anthropic_completer import register_if_env_key as _register_anthropic
        from wordforge.llm.azure_openai_completer import (
            register_if_env_key as _register_azure,
        )
        from wordforge.llm.bedrock_completer import register_if_env_key as _register_bedrock
        from wordforge.llm.client import LLMClient
        from wordforge.llm.gemini_completer import register_if_env_key as _register_gemini
        from wordforge.llm.openai_completer import register_if_env_key as _register_openai
        from wordforge.llm.qwen_completer import register_if_env_key as _register_qwen
        from wordforge.stages.registry import build_stages

        artifacts_store = StageArtifactStore(engine)
        # Merge every provider whose credentials are present. Stages pick the
        # one named in configs/default.toml `[stages.<name>].provider`; this
        # way swapping a stage's provider is a TOML-only change.
        completers: dict = {}
        for register in (
            _register_bedrock,
            _register_anthropic,
            _register_gemini,
            _register_openai,
            _register_qwen,
            _register_azure,
        ):
            completers.update(register())
        llm = LLMClient(store=CacheStore(engine), completers=completers) if completers else None
        stages = build_stages(cfg, engine=engine, artifacts=artifacts_store, llm=llm)
        if stage is not None:
            stages = [s for s in stages if s.name == stage]
        from wordforge.dlq import DeadLetterStore

        runner = StageRunner(
            artifacts=artifacts_store,
            runs=StageRunStore(engine),
            budget=BudgetGate(engine),
            dlq=DeadLetterStore(engine),
            concurrency=concurrency,
        )
        result = asyncio.run(
            runner.run(stages=stages, word_ids=word_ids, batch_id=batch, force=force)
        )
    finally:
        engine.dispose()

    if not word_ids:
        typer.echo(
            f"run complete: batch={batch} words=0 stages={len(stages)} "
            f"(no words attached to this batch — was `wordforge ingest "
            f"--batch {batch}` run?)",
            err=True,
        )
    else:
        total_events = len(word_ids) * len(stages)
        actual_events = result.ok_events + result.failed_events + result.skipped_events
        pruned_events = total_events - actual_events
        typer.echo(
            f"run complete: batch={batch} | {len(word_ids)} words × "
            f"{len(stages)} stages = {total_events} events | "
            f"ok_events={result.ok_events} "
            f"failed_events={result.failed_events} "
            f"skipped_events={result.skipped_events} "
            f"pruned_events={pruned_events}"
        )


@app.command("plan")
def plan_cmd(
    stage: str = typer.Option(..., "--stage", help="Stage to plan (e.g. paraphrase)."),
    batch: str | None = typer.Option(
        None, "--batch", help="Limit plan to one batch; omit to scan all batches."
    ),
) -> None:
    """Dry-run: show how many words would re-run + an estimate of $ cost.

    Read-only; never touches stage_runs / external_call_cache.
    """
    from wordforge.config import load_stage_config
    from wordforge.db.engine import make_engine
    from wordforge.pipeline.plan import build_plan

    cfg = load_stage_config()
    if stage not in cfg.stages:
        raise typer.BadParameter(
            f"unknown stage: {stage!r}; configured: {sorted(cfg.stages.keys())}"
        )

    engine = make_engine()
    try:
        try:
            report = build_plan(engine, config=cfg, stage_name=stage, batch_id=batch)
        except LookupError as e:
            raise typer.BadParameter(str(e)) from e
    finally:
        engine.dispose()

    batch_repr = report.batch_id if report.batch_id is not None else "<all>"
    sample_repr = ", ".join(report.sample_forms) if report.sample_forms else "—"
    # Round 1 D2: name the coarse-counter has_artifact + add explicit caveat
    # so operators understand this doesn't verify fingerprint drift.
    # Round 1 D4: print up to 10 concrete forms (spec §7 L501 "列出将重跑的词").
    typer.echo(
        f"plan: stage={report.stage_name} batch={batch_repr} | "
        f"total_candidates={report.total_candidates} "
        f"has_artifact={report.has_artifact} (fingerprint unchecked — P5 will verify) "
        f"needs_rerun={report.needs_rerun} | "
        f"estimated_cost_usd={report.estimated_cost_usd:.4f} "
        f"(source={report.cost_source}) | "
        f"sample: {sample_repr}"
    )


dlq_app = typer.Typer(help="dead_letter administration (P7).")
app.add_typer(dlq_app, name="dlq")


@dlq_app.command("list")
def dlq_list(
    limit: int = typer.Option(50, "--limit", help="Max rows to show."),
) -> None:
    """Show unresolved dead_letter rows (newest first)."""
    from wordforge.db.engine import make_engine
    from wordforge.dlq import DeadLetterStore

    engine = make_engine()
    try:
        rows = DeadLetterStore(engine).list_open(limit=limit)
    finally:
        engine.dispose()
    if not rows:
        typer.echo("no unresolved dead_letter rows")
        return
    for r in rows:
        err_line = r.error.splitlines()[0][:80] if r.error else ""
        typer.echo(
            f"[{r.id}] word_id={r.word_id} stage={r.stage_name} "
            f"attempt={r.attempt} created_at={r.created_at} | {err_line}"
        )


@dlq_app.command("replay")
def dlq_replay(
    word_id: int = typer.Option(..., "--word-id", help="pipeline.words.id to replay."),
) -> None:
    """Mark all open dead_letter rows for WORD_ID as resolved + reset pipeline.words.status='new'.

    Next `wordforge run --batch ...` will pick this word up again.
    """
    from wordforge.db.engine import make_engine
    from wordforge.dlq import DeadLetterStore

    engine = make_engine()
    try:
        try:
            n = DeadLetterStore(engine).replay(word_id=word_id)
        except LookupError as e:
            raise typer.BadParameter(str(e)) from e
    finally:
        engine.dispose()
    typer.echo(f"replayed: {n} dead_letter rows resolved; word_id={word_id} → status='new'")


@app.command("review")
def review_cmd(
    batch: str | None = typer.Option(None, "--batch", help="batch_id to review"),
    word: str | None = typer.Option(None, "--word", help="single normalized form to review"),
    all_: bool = typer.Option(False, "--all", help="scan all domain.words"),
    limit: int = typer.Option(100, "--limit", help="target-count cap for --batch / --all"),
    concurrency: int = typer.Option(
        10,
        "--concurrency",
        help="Max concurrent words. Each word issues ≤ 6 LLM calls in parallel, "
        "bounded by --call-concurrency.",
    ),
    call_concurrency: int | None = typer.Option(
        None,
        "--call-concurrency",
        help="Max concurrent in-flight LLM calls. Defaults to concurrency*3.",
    ),
    call_timeout: float = typer.Option(
        120.0, "--call-timeout",
        help="Hard per-call ceiling (seconds). Backstop for proxy-hang pathology.",
    ),
    apply_: bool = typer.Option(False, "--apply", help="actually write patches to app.*"),
    output: str = typer.Option(
        "/tmp/wordforge_smoke/review_fixes.jsonl", "--output",
        help="Append jsonl records here. Pair with --skip-done-from for resume.",
    ),
    skip_done_from: list[str] = typer.Option(  # noqa: B008
        [], "--skip-done-from",
        help="Read this jsonl and skip any word_id already present. Repeatable.",
    ),
) -> None:
    """Multi-checker quality review of app.* with JSON-patch fix apply.

    Five narrow-focus haiku 'checkers' run in parallel per word; their
    issues are aggregated and handed to an opus 'fixer' which emits
    JSON patches; apply_patches_for_word runs each word's patches in a
    single DB txn with old_value drift detection.
    """
    import asyncio
    import json
    import os
    import sys as _sys

    import sqlalchemy as _sa

    from wordforge.cache import CacheStore
    from wordforge.db.engine import make_engine
    from wordforge.llm.bedrock_completer import register_if_env_key as _register_bedrock
    from wordforge.llm.client import LLMClient
    from wordforge.reviewer.config import CFG
    from wordforge.reviewer.runner import run_review

    done_ids: set[int] = set()
    for src in skip_done_from:
        if not os.path.exists(src):
            typer.echo(f"warn: --skip-done-from {src} does not exist, skipping", err=True)
            continue
        before = len(done_ids)
        with open(src, encoding="utf-8") as f:
            for line in f:
                try:
                    wid = json.loads(line).get("word_id")
                except json.JSONDecodeError:
                    continue
                if isinstance(wid, int):
                    done_ids.add(wid)
        typer.echo(
            f"resume: +{len(done_ids) - before} word_ids from {src} "
            f"(total skip set={len(done_ids)})"
        )

    engine = make_engine()
    completers = _register_bedrock()
    if not completers:
        typer.echo(
            "error: no Bedrock creds (set AWS_BEARER_TOKEN_BEDROCK or AWS_ACCESS_KEY_ID)",
            err=True,
        )
        raise typer.Exit(2)
    llm = LLMClient(store=CacheStore(engine), completers=completers)

    targets: list[tuple[str | None, int | None]] = []
    if word:
        targets = [(word, None)]
    elif batch:
        with engine.connect() as conn:
            rows = conn.execute(
                _sa.text(
                    "SELECT aw.form, aw.word_id FROM pipeline.words w "
                    "JOIN domain.words aw ON aw.word_id = w.app_word_id "
                    "WHERE w.batch_id=:b AND w.status='done' "
                    "ORDER BY w.id LIMIT :lim"
                ),
                {"b": batch, "lim": limit},
            ).all()
        targets = [(r[0], r[1]) for r in rows]
    elif all_:
        with engine.connect() as conn:
            rows = conn.execute(
                _sa.text("SELECT form, word_id FROM domain.words ORDER BY word_id LIMIT :lim"),
                {"lim": limit},
            ).all()
        targets = [(r[0], r[1]) for r in rows if r[1] not in done_ids]
    else:
        raise typer.BadParameter("pass --word, --batch, or --all")

    cc = call_concurrency or concurrency * CFG.CALL_CONCURRENCY_MULTIPLIER
    typer.echo(
        f"review (multi-checker) on {len(targets)} words, "
        f"word_concurrency={concurrency}, call_concurrency={cc}, apply={apply_}"
    )
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    rc = asyncio.run(
        run_review(
            targets=targets,
            engine=engine,
            llm=llm,
            output_path=output,
            apply_=apply_,
            word_concurrency=concurrency,
            call_concurrency=cc,
            call_timeout=call_timeout,
        )
    )
    if rc:
        _sys.exit(rc)


if __name__ == "__main__":
    app()
