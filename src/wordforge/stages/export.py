"""ExportStage — the only writer to app.* schema.

Reads all 7 upstream stages; dispatches Case A/B/C per spec §5; one txn per
word. Preflight asserts same-source invariant before mutating children.

Spec §5 export-txn logic:
  0a: SELECT domain.words WHERE form+type → existing_row?
  0b: (Case B/C) preflight same-source assert on children
  1: UPSERT domain.words (Case A: INSERT, Case B: ON CONFLICT UPDATE WHERE pipeline:)
  2: (Case B) DELETE pipeline: children
  3: INSERT meanings + mnemonics
  4: UPDATE pipeline.words SET status='done', app_word_id=...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlalchemy as sa

from wordforge.pipeline.fingerprint import fingerprint
from wordforge.pipeline.runner import StagePayload

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from wordforge.config import StageConfig
    from wordforge.pipeline.artifacts import StageArtifactStore


@dataclass
class ExportStage:
    engine: Engine
    artifacts: StageArtifactStore
    config: StageConfig
    name: str = field(default="export", init=False)

    _UPSTREAMS = (
        "fetch_dict",
        "paraphrase",
        "phonetic",
        "derivatives",
        "examples",
        "mnemonic",
        "quality_gate",
    )

    def expected_fingerprint(self, *, word_id: int) -> str:
        fps: list[str] = []
        for up in self._UPSTREAMS:
            row = self.artifacts.get(word_id=word_id, stage_name=up)
            if row is not None and row["fingerprint"]:
                fps.append(row["fingerprint"])
        return fingerprint(
            upstream_fingerprints=fps,
            stage_config={"parser_version": self.config.parser_version},
            prompt_version=None,
            prompt_content_hash=None,
            parser_version=self.config.parser_version,
        )

    async def run_one(self, *, word_id: int) -> StagePayload:
        ups: dict = {}
        for up in self._UPSTREAMS:
            row = self.artifacts.get(word_id=word_id, stage_name=up)
            if row is None:
                raise LookupError(f"missing upstream artifact '{up}' for word_id={word_id}")
            ups[up] = row

        qg = ups["quality_gate"]["payload"]
        if not qg.get("passed"):
            raise ValueError(
                f"quality_gate did not pass for word_id={word_id}: {qg.get('failed_rules')}"
            )

        with self.engine.connect() as conn:
            pw = conn.execute(
                sa.text(
                    "SELECT normalized_form, type FROM pipeline.words WHERE id = :id"
                ),
                {"id": word_id},
            ).one()
        form, type_ = pw[0], pw[1]

        row_w = _build_app_words_row(ups, form, type_)
        row_meanings = _build_app_meanings(ups)
        row_mnemonics = _build_app_mnemonics(ups)
        row_sentences = _build_app_sentences(ups, len(row_meanings))

        import asyncio

        loop = asyncio.get_running_loop()
        app_word_id, case = await loop.run_in_executor(
            None,
            self._run_export_txn,
            word_id,
            row_w,
            row_meanings,
            row_mnemonics,
            row_sentences,
        )

        return StagePayload(
            payload={"app_word_id": app_word_id, "case": case},
            source=f"pipeline:local:export_v{self.config.parser_version}",
            cost_usd=0.0,
        )

    def _run_export_txn(
        self,
        pipeline_word_id: int,
        row_w: dict,
        row_meanings: list[dict],
        row_mnemonics: list[dict],
        row_sentences: list[list[dict]],
    ) -> tuple[int, str]:
        """Single txn. Dispatch case A/B/C. Raise for preflight fail."""
        with self.engine.begin() as conn:
            # 0a: probe existing domain.words
            existing = conn.execute(
                sa.text(
                    "SELECT word_id, source FROM domain.words WHERE form = :form AND type = :type"
                ),
                {"form": row_w["form"], "type": row_w["type"]},
            ).first()

            if existing is None:
                case = "A"
            elif existing[1].startswith("pipeline:"):
                case = "B"
            else:
                case = "C"

            if case in ("B", "C"):
                # 0b preflight: all child rows must match parent source prefix
                self._preflight_same_source(conn, existing[0], existing[1], case)

            if case in ("A", "B"):
                # 1) UPSERT domain.words
                new_id = self._upsert_app_words(conn, row_w)
                # 2) Case B: DELETE pipeline children before re-insert
                if case == "B":
                    self._delete_pipeline_children(conn, new_id)
                # 3) INSERT children
                self._insert_children(
                    conn, new_id, row_meanings, row_mnemonics, row_sentences
                )
                app_word_id = new_id
            else:
                # Case C: skip app.* writes, use existing word_id
                app_word_id = existing[0]

            # 4) update pipeline.words
            conn.execute(
                sa.text(
                    "UPDATE pipeline.words SET app_word_id = :w, status = 'done' WHERE id = :pw"
                ),
                {"w": app_word_id, "pw": pipeline_word_id},
            )

            # 5) serving read model — read domain.* within the same txn so the
            # aggregated JSONB always reflects canonical relational state.
            self._upsert_serving_word_payload(conn, app_word_id)

            return app_word_id, case

    def _upsert_serving_word_payload(self, conn: Connection, word_id: int) -> None:
        """Build aggregated JSONB payload from domain.* + domain.package_word
        and upsert into serving.word_payload. Runs inside the export txn so
        domain.* and serving.* cannot drift.

        Implementation notes:
        - Read straight from domain.* rather than piecing upstream artifacts
          together — any preceding preflight/Case-C branch may have stored
          human-authored content, and serving must reflect whatever is
          actually in the canonical tables.
        - domain.package_word has no FK to domain.words, so the LEFT JOIN
          handles the "package references a word we don't have yet" case
          (see mirror script's post-flight warning).
        """
        row = conn.execute(
            sa.text(
                "SELECT form, type, phonetic_us, phonetic_uk, audio_us, audio_uk "
                "FROM domain.words WHERE word_id = :w"
            ),
            {"w": word_id},
        ).first()
        if row is None:
            return  # word not in domain yet (Case C variant); skip
        form, type_, ph_us, ph_uk, a_us, a_uk = row

        meanings = conn.execute(
            sa.text(
                "SELECT meaning_id, pos, pos_sub, cn_paraphrase, en_paraphrase, "
                "equivalents, synonyms, antonyms "
                "FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"
            ),
            {"w": word_id},
        ).all()
        meaning_blocks = []
        for m in meanings:
            sentences = conn.execute(
                sa.text(
                    "SELECT sentence_id, form AS en, translation AS cn "
                    "FROM domain.sentences WHERE meaning_id = :mid ORDER BY sentence_id"
                ),
                {"mid": m[0]},
            ).all()
            meaning_blocks.append({
                "meaning_id": m[0], "pos": m[1], "pos_sub": m[2],
                "cn": m[3], "en": m[4],
                "equivalents": m[5], "synonyms": m[6], "antonyms": m[7],
                "sentences": [
                    {"sentence_id": s[0], "en": s[1], "cn": s[2]} for s in sentences
                ],
            })

        mnemonic_rows = conn.execute(
            sa.text(
                "SELECT content, type FROM domain.mnemonics "
                "WHERE word_id = :w ORDER BY mnemonic_id LIMIT 1"
            ),
            {"w": word_id},
        ).first()
        mnemonic = (
            {"content": mnemonic_rows[0], "type": mnemonic_rows[1]}
            if mnemonic_rows else None
        )

        phrase_rows = conn.execute(
            sa.text(
                "SELECT phrase_id, form, meaning "
                "FROM domain.phrases WHERE owner_word_id = :w ORDER BY phrase_id"
            ),
            {"w": word_id},
        ).all()
        phrases = [
            {"phrase_id": p[0], "en": p[1], "meaning": p[2]} for p in phrase_rows
        ]

        package_rows = conn.execute(
            sa.text(
                "SELECT package_id, unit_id, sort_order, importance "
                "FROM domain.package_word WHERE word_id = :w "
                "ORDER BY package_id, sort_order"
            ),
            {"w": word_id},
        ).all()
        packages = [
            {
                "package_id": p[0], "unit_id": p[1],
                "sort_order": float(p[2]),
                "importance": p[3],
            } for p in package_rows
        ]

        payload = {
            "form": form,
            "type": type_,
            "phonetic": {
                "us": ph_us, "uk": ph_uk,
                "audio_us": a_us, "audio_uk": a_uk,
            },
            "meanings": meaning_blocks,
            "mnemonic": mnemonic,
            "phrases": phrases,
            "packages": packages,
        }

        conn.execute(
            sa.text(
                "INSERT INTO serving.word_payload "
                "(word_id, form, type, payload, payload_schema_version, updated_at) "
                "VALUES (:wid, :form, :type, CAST(:payload AS jsonb), 1, now()) "
                "ON CONFLICT (word_id) DO UPDATE SET "
                "  form = EXCLUDED.form, "
                "  type = EXCLUDED.type, "
                "  payload = EXCLUDED.payload, "
                "  payload_schema_version = EXCLUDED.payload_schema_version, "
                "  updated_at = now()"
            ),
            {
                "wid": word_id, "form": form, "type": type_,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )

    def _preflight_same_source(
        self, conn: Connection, word_id: int, parent_source: str, case: str
    ) -> None:
        """Preflight same-source assert (spec §3 L148-163).

        For Case B (pipeline parent) expected prefix is 'pipeline:'.
        For Case C (human/import parent) expected prefix from parent_source.

        MUST include sentences JOIN — spec §3 L153-156 requires catching
        human: sentences hanging under pipeline: meanings before DELETE
        CASCADE silently destroys them.
        """
        expected_prefix = parent_source.split(":", 1)[0] + ":"
        rows = conn.execute(
            sa.text(
                """
                SELECT source FROM domain.meanings WHERE word_id = :w
                UNION ALL
                SELECT s.source FROM domain.sentences s
                  JOIN domain.meanings m ON s.meaning_id = m.meaning_id
                 WHERE m.word_id = :w
                UNION ALL
                SELECT source FROM domain.mnemonics WHERE word_id = :w
                UNION ALL
                SELECT source FROM domain.phrases WHERE owner_word_id = :w
                """
            ),
            {"w": word_id},
        ).all()
        for (src,) in rows:
            if not src.startswith(expected_prefix):
                raise AssertionError(
                    f"preflight(case={case}): child row source={src!r} does not match "
                    f"parent prefix {expected_prefix!r} for word_id={word_id}"
                )

    def _upsert_app_words(self, conn: Connection, row_w: dict) -> int:
        """Spec §5 L334-351 full UPSERT — 16 data columns; word_id is serial.

        Ingest order (recovery dumps ORDER BY momo word_id) + single-stream
        export keeps assigned word_ids monotone w.r.t. momo ids; concurrency
        may shuffle adjacent pairs but the two stores stay roughly aligned.
        """
        sql = """
            INSERT INTO domain.words (
              type, form, phonetic_us, phonetic_uk, audio_us, audio_uk,
              structure, plural, past_tense, past_participle, third_person,
              present_participle, comparative, superlative, derivatives, source
            ) VALUES (
              :type, :form, :phonetic_us, :phonetic_uk, :audio_us, :audio_uk,
              CAST(:structure AS jsonb), :plural, :past_tense, :past_participle,
              :third_person, :present_participle, :comparative, :superlative,
              CAST(:derivatives AS jsonb), :source
            )
            ON CONFLICT (form, type) DO UPDATE SET
              phonetic_us = EXCLUDED.phonetic_us,
              phonetic_uk = EXCLUDED.phonetic_uk,
              audio_us = EXCLUDED.audio_us,
              audio_uk = EXCLUDED.audio_uk,
              structure = EXCLUDED.structure,
              plural = EXCLUDED.plural,
              past_tense = EXCLUDED.past_tense,
              past_participle = EXCLUDED.past_participle,
              third_person = EXCLUDED.third_person,
              present_participle = EXCLUDED.present_participle,
              comparative = EXCLUDED.comparative,
              superlative = EXCLUDED.superlative,
              derivatives = EXCLUDED.derivatives,
              source = EXCLUDED.source,
              updated_at = now()
            WHERE domain.words.source LIKE 'pipeline:%'
            RETURNING word_id
        """
        result = conn.execute(sa.text(sql), row_w).first()
        if result is None:
            raise ConcurrentModificationError(
                "UPSERT returned 0 rows — concurrent modification or human-source block"
            )
        return result[0]

    def _delete_pipeline_children(self, conn: Connection, word_id: int) -> None:
        # sentences CASCADE via meanings
        conn.execute(
            sa.text("DELETE FROM domain.meanings WHERE word_id = :w AND source LIKE 'pipeline:%'"),
            {"w": word_id},
        )
        conn.execute(
            sa.text("DELETE FROM domain.mnemonics WHERE word_id = :w AND source LIKE 'pipeline:%'"),
            {"w": word_id},
        )
        conn.execute(
            sa.text(
                "DELETE FROM domain.phrases WHERE owner_word_id = :w AND source LIKE 'pipeline:%'"
            ),
            {"w": word_id},
        )

    def _insert_children(
        self,
        conn: Connection,
        word_id: int,
        meanings: list[dict],
        mnemonics: list[dict],
        sentences_per_meaning: list[list[dict]],
    ) -> None:
        """Insert meanings (RETURNING meaning_id) then sentences keyed on the
        returned ids. `sentences_per_meaning` is parallel to `meanings`.
        """
        returned_meaning_ids: list[int] = []
        for m in meanings:
            m["word_id"] = word_id
            row = conn.execute(
                sa.text(
                    "INSERT INTO domain.meanings "
                    "(word_id, pos, pos_sub, cn_paraphrase, en_paraphrase, "
                    " equivalents, synonyms, antonyms, source) VALUES "
                    "(:word_id, :pos, :pos_sub, :cn_paraphrase, :en_paraphrase, "
                    " CAST(:equivalents AS jsonb), CAST(:synonyms AS jsonb), "
                    " CAST(:antonyms AS jsonb), :source) RETURNING meaning_id"
                ),
                m,
            ).first()
            returned_meaning_ids.append(row[0] if row else None)

        for mn in mnemonics:
            mn["word_id"] = word_id
            conn.execute(
                sa.text(
                    "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                    "VALUES (:word_id, :type, CAST(:content AS jsonb), :source)"
                ),
                mn,
            )

        sentence_source = mnemonics[0]["source"] if mnemonics else "pipeline:local:examples_v1"
        for idx, mid in enumerate(returned_meaning_ids):
            if mid is None or idx >= len(sentences_per_meaning):
                continue
            for s in sentences_per_meaning[idx]:
                en = s.get("en")
                cn = s.get("cn")
                # domain.sentences.translation is NOT NULL — drop examples that
                # the LLM emitted without a CN translation (rare but happens
                # on truncated responses for super-polysemous words).
                if not en or not cn:
                    continue
                conn.execute(
                    sa.text(
                        "INSERT INTO domain.sentences "
                        "(meaning_id, form, translation, source) "
                        "VALUES (:mid, :form, :translation, :source)"
                    ),
                    {
                        "mid": mid,
                        "form": en,
                        "translation": cn,
                        "source": sentence_source,
                    },
                )


# --- Pure helper functions ---


_POS_MAP = {
    "n": 1, "v": 2, "adj": 3, "adv": 4,
    "prep": 5, "conj": 6, "pron": 7, "interj": 8,
    "num": 9, "art": 10,
    "phrasal_verb": 201,
}


def _build_app_words_row(ups: dict, form: str, type_: int) -> dict:
    """Compose domain.words row dict from upstream artifacts."""
    phonetic = ups["phonetic"]["payload"] if isinstance(ups["phonetic"]["payload"], dict) else {}
    derivs = (
        ups["derivatives"]["payload"] if isinstance(ups["derivatives"]["payload"], dict) else {}
    )
    parap = ups["paraphrase"]["payload"] if isinstance(ups["paraphrase"]["payload"], dict) else {}
    word_forms = derivs.get("word_forms", {}) if isinstance(derivs, dict) else {}

    return {
        "type": type_,
        "form": form,
        "phonetic_us": phonetic.get("phonetic_us"),
        "phonetic_uk": phonetic.get("phonetic_uk"),
        "audio_us": phonetic.get("audio_us"),
        "audio_uk": phonetic.get("audio_uk"),
        "structure": json.dumps(parap.get("structure")) if parap.get("structure") else None,
        "plural": word_forms.get("plural"),
        "past_tense": word_forms.get("past_tense"),
        "past_participle": word_forms.get("past_participle"),
        "third_person": word_forms.get("third_person"),
        "present_participle": word_forms.get("present_participle"),
        "comparative": word_forms.get("comparative"),
        "superlative": word_forms.get("superlative"),
        "derivatives": json.dumps(derivs) if derivs else None,
        "source": ups["paraphrase"]["source"],
    }


def _build_app_meanings(ups: dict) -> list[dict]:
    """One row per meaning from paraphrase payload."""
    parap = ups["paraphrase"]["payload"]
    derivs_payload = ups["derivatives"]["payload"]
    derivs_per = derivs_payload.get("per_meaning", []) if isinstance(derivs_payload, dict) else []

    rows: list[dict] = []
    for i, m in enumerate(parap.get("meanings", [])):
        per = next((d for d in derivs_per if d.get("meaning_index") == i), {})
        rows.append(
            {
                "pos": _POS_MAP.get(m.get("pos")),
                "pos_sub": None,
                "cn_paraphrase": m.get("cn"),
                "en_paraphrase": m.get("en"),
                "equivalents": json.dumps(per.get("equivalents"))
                if per.get("equivalents")
                else None,
                "synonyms": json.dumps(per.get("synonyms")) if per.get("synonyms") else None,
                "antonyms": json.dumps(per.get("antonyms")) if per.get("antonyms") else None,
                "source": ups["paraphrase"]["source"],
            }
        )
    return rows


def _build_app_sentences(ups: dict, num_meanings: int) -> list[list[dict]]:
    """Return a list parallel to meanings: [i] = [{en, cn}, ...] from examples."""
    payload = ups.get("examples", {}).get("payload", {}) if ups.get("examples") else {}
    if not isinstance(payload, dict):
        return [[] for _ in range(num_meanings)]
    per = payload.get("per_meaning", [])
    if not isinstance(per, list):
        per = []
    # Build an index-keyed lookup, then project onto the meaning count.
    by_idx: dict[int, list[dict]] = {}
    for entry in per:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("meaning_index")
        ex_list = entry.get("examples", [])
        if not isinstance(ex_list, list) or not isinstance(idx, int):
            continue
        cleaned: list[dict] = []
        for e in ex_list:
            if not isinstance(e, dict):
                continue
            en = e.get("en")
            cn = e.get("cn")
            if en:
                cleaned.append({"en": en, "cn": cn})
        by_idx[idx] = cleaned
    return [by_idx.get(i, []) for i in range(num_meanings)]


def _build_app_mnemonics(ups: dict) -> list[dict]:
    """One row from mnemonic payload. content MUST be JSONB-serialized dict."""
    payload = ups["mnemonic"]["payload"]
    mnem_str = payload.get("mnemonic") if isinstance(payload, dict) else None
    if not mnem_str:
        return []
    kind = payload.get("kind", "phonetic")
    return [
        {
            "type": 1,  # DDL CHECK requires type=1
            "content": json.dumps({"text": mnem_str, "kind": kind}),
            "source": ups["mnemonic"]["source"],
        }
    ]


class ConcurrentModificationError(RuntimeError):
    """Raised when UPSERT returns 0 rows — implies concurrent DBA / process
    modified domain.words between step 0a probe and step 1 UPSERT. Runner
    records stage_runs.status='failed'; P7 DLQ replay can single-out this
    subclass for auto-retry vs logic errors."""
