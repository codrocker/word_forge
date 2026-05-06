"""Assemble concrete Stage instances from WordforgeConfig.

Enforces spec §5 ordering regardless of how config lists them. P5a
implements `fetch_dict` + `phonetic`; P5b adds the 4 LLM stages
(paraphrase/derivatives/examples/mnemonic). quality_gate + export are
P5c — silently skipped until then.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from wordforge.config import WordforgeConfig
    from wordforge.llm.client import LLMClient
    from wordforge.pipeline.artifacts import StageArtifactStore
    from wordforge.pipeline.protocols import Stage

# Spec §5 stage order — canonical sequence the runner follows.
_SPEC_STAGE_ORDER = (
    "fetch_dict",
    "paraphrase",
    "phonetic",
    "derivatives",
    "examples",
    "mnemonic",
    "quality_gate",
    "export",
)


def build_stages(
    config: WordforgeConfig,
    *,
    engine: Engine,
    artifacts: StageArtifactStore,
    llm: LLMClient | None = None,
) -> list[Stage]:
    from wordforge.stages.derivatives import DerivativesStage
    from wordforge.stages.examples import ExamplesStage
    from wordforge.stages.export import ExportStage
    from wordforge.stages.fetch_dict import FetchDictStage
    from wordforge.stages.mnemonic import MnemonicStage
    from wordforge.stages.paraphrase import ParaphraseStage
    from wordforge.stages.phonetic import PhoneticStage
    from wordforge.stages.quality_gate import QualityGateStage

    non_llm: dict[str, type] = {
        "fetch_dict": FetchDictStage,
        "phonetic": PhoneticStage,
    }
    llm_impls: dict[str, type] = {
        "paraphrase": ParaphraseStage,
        "derivatives": DerivativesStage,
        "examples": ExamplesStage,
        "mnemonic": MnemonicStage,
    }
    non_llm_late: dict[str, type] = {
        "quality_gate": QualityGateStage,
        "export": ExportStage,
    }

    # paraphrase_rerank is an optional sub-stage config (not in the main
    # order list). Pass it into ParaphraseStage; None if absent.
    rerank_cfg = config.stages.get("paraphrase_rerank")

    result: list[Stage] = []
    for name in _SPEC_STAGE_ORDER:
        if name not in config.stages:
            continue
        stage_cfg = config.stages[name]
        if name in non_llm:
            result.append(non_llm[name](engine=engine, artifacts=artifacts, config=stage_cfg))
        elif name in llm_impls:
            if llm is None:
                # No LLMClient wired (no env key); skip silently.
                continue
            kwargs: dict = dict(
                engine=engine, artifacts=artifacts, config=stage_cfg, llm=llm
            )
            if name == "paraphrase":
                kwargs["rerank_config"] = rerank_cfg
            result.append(llm_impls[name](**kwargs))
        elif name in non_llm_late:
            result.append(non_llm_late[name](engine=engine, artifacts=artifacts, config=stage_cfg))
    return result
