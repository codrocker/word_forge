"""Quality-review pipeline (previously scripts/review_and_fix.py).

Packages in this module:
- config   — ReviewConfig dataclass (tunable knobs)
- prompts  — 5 checker + 1 opus fixer prompt templates
- patch    — JSON-patch application + PatchDriftError + _check_drift
- blob     — build_word_blob (read app.* into a single dict for prompting)
- worker   — run_one_word, _run_checker, llm_call_*
- runner   — asyncio Queue orchestrator (reusable)

scripts/review_and_fix.py is a thin shim preserved for backward compat;
new invocations should go through `wordforge review` (CLI subcommand)
once that lands.
"""
