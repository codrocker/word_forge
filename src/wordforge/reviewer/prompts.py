"""Prompt templates for the review pipeline.

Five narrow-focus haiku checkers (parallel) + one opus fixer that
aggregates their issues and emits a JSON patch. Each checker's remit
is intentionally narrow — disagreement between checkers is OK because
the opus fixer sees all their reports together.
"""
# ruff: noqa: E501  (long prompt strings verbatim for LLM)
from __future__ import annotations

CHECKER_FIELD_QUALITY = """You are a lexicographer doing a FAST scan for field-level bugs in a vocabulary entry. Flag only these clearly broken things (do NOT flag style/preference):

- cn_paraphrase is EMPTY when it should exist
- cn_paraphrase is Collins-style verbose (e.g. >25 chars of nested parentheticals, "用于名词词组前，指前面已经提及的...")
- cn_paraphrase has unbalanced/misplaced parentheses (e.g. "款项) 相当大的")
- cn_paraphrase has obvious typos (e.g. "雄雄燃烧" should be "熊熊燃烧")
- en_paraphrase is EMPTY
- en_paraphrase is truncated mid-sentence (ends with " ('" or "A gold is the same as a .")
- synonyms or antonyms clearly don't match the meaning (e.g. synonym of "apple" is "walk")

Input:
```
{blob_json}
```

Output strict JSON (no markdown, no prose):
{"issues": [{"path": "meanings[<i>].cn_paraphrase|en_paraphrase", "issue": "<short reason>"}]}

Empty list if everything is fine. Max 10 issues.
"""


CHECKER_MORPHOLOGY = """You are a lexicographer checking if a meaning truly belongs to THIS word form, or is a morphological variant that should live under a different form.

English examples of mis-filed meanings:
- "happy" should NOT carry an adverb sense "快乐地" — that belongs to "happily" (pos=adv).
- "good" should NOT carry "更好地/最好地" — those are "better"/"best".
- "run" should NOT list an n. sense "(Run) 鲁恩 (人名)" — proper noun.
- "saw" DOES carry "see 的过去式" meanings — that's correct (saw IS past-tense of see), keep them but they should be tagged.

BUT some words are genuinely both adj and adv (no -ly variant needed): fast, hard, late, early, straight, close, right, wrong, deep, high. Do NOT flag those.

Also flag: entries where pos is a proper-noun / surname / place (Chinese like "(Run)(塞)鲁恩(人名)" dropping in as a meaning).

Input (form is the primary key — judge each meaning against it):
```
{blob_json}
```

Output:
{"issues": [{"path": "meanings[<i>]", "issue": "<short reason — e.g. 'belongs to happily'>"}]}

Empty list if all meanings truly belong to this form. Max 10 issues.
"""


CHECKER_EXAMPLES = """You are a lexicographer checking example sentences for a vocabulary entry. Flag only clear problems:

- Example sentence doesn't actually demonstrate the stated meaning (talks about a different sense)
- Example is stilted/unnatural English
- Chinese translation is word-for-word / machine-translated / distorts the sense
- Example is offensive / politically charged / inappropriate for a learning app
- Example is far above B2 level (academic jargon, ancient text) when the meaning is everyday

Do NOT flag stylistic preference (e.g. "could be more vivid"). Only flag errors.

Input:
```
{blob_json}
```

Output:
{"issues": [{"path": "meanings[<i>].examples[<j>]", "issue": "<short reason>"}]}

Empty list if fine. Max 10 issues.
"""


CHECKER_MNEMONIC = """You are checking the Chinese-phonetic-pun mnemonic for a vocabulary entry. Flag only clear problems:

- Mnemonic is empty or a placeholder
- Mnemonic contains offensive / vulgar / sexual / political / violent content (e.g. "干恁妈的政府")
- Mnemonic is nonsense (unrelated to word meaning, random characters like "把我citrus")
- Mnemonic clearly violates the expected JSON structure
- Phonetic hook is completely wrong (the Chinese sound-alike bears no resemblance to the English word's sound)

Do NOT flag for subjective quality ("could be more creative"). Only factual/policy issues.

Input:
```
{blob_json}
```

Output:
{"issues": [{"path": "mnemonic.text", "issue": "<short reason>"}]}

Empty list if fine.
"""


CHECKER_POS_CONSISTENCY = """You are checking part-of-speech tags on a vocabulary entry. Flag only clear mismatches.

pos encoding used in this app:
  1=noun, 2=verb, 3=adj, 4=number, 5=pronoun, 6=adverb,
  7=article, 8=preposition, 9=conjunction, 10=interjection,
  201=phrasal verb

Flag if:
- pos number doesn't match the grammatical role demonstrated in the cn/en paraphrase
  (e.g. pos=noun but the cn says "迅速地" — that's clearly an adverb)
- pos is missing (null/0) when meaning is otherwise complete

Do NOT flag morphological-variant issues (that's a different checker's job).

Input:
```
{blob_json}
```

Output:
{"issues": [{"path": "meanings[<i>].pos", "issue": "<short reason>"}]}
"""


CHECKERS: list[tuple[str, str]] = [
    ("field_quality", CHECKER_FIELD_QUALITY),
    ("morphology", CHECKER_MORPHOLOGY),
    ("examples", CHECKER_EXAMPLES),
    ("mnemonic", CHECKER_MNEMONIC),
    ("pos_consistency", CHECKER_POS_CONSISTENCY),
]


OPUS_FIXER = """You are a senior bilingual lexicographer fixing a flawed vocabulary-app entry. Multiple fast scanners have flagged issues. Produce concrete fixes as a JSON patch.

Rules:
- ONLY address issues that are genuinely problems. You MAY disagree with a scanner and leave a field alone.
- Be conservative: don't rewrite for style, only fix clear errors.
- For `mnemonic.text`: follow "leverage association" Chinese-phonetic-pun style
  (e.g. apple→「阿婆了」，bridges sound + vivid scene). Never offensive/political.
- For morphology-flagged meanings (meaning belongs to another form): use op=delete
  to remove them from this word (they'll be re-filed separately by infra).
- For normal value updates: use op=update with old_value + new_value. old_value
  must match exactly so we can detect DB drift.
- For meaning-level delete: path = "meanings[<i>]" with op=delete

Input blob:
```
{blob_json}
```

Scanner issues (from haiku checkers):
```
{issues_json}
```

Output strict JSON (no markdown fence):
{
  "patches": [
    {"op": "update", "path": "<path>", "old_value": "<exact current>", "new_value": "<fix>"},
    {"op": "delete", "path": "meanings[<i>]"}
  ]
}

Supported paths for `op=update`:
  - meanings[i].cn_paraphrase
  - meanings[i].en_paraphrase
  - meanings[i].examples[j].en
  - meanings[i].examples[j].cn
  - mnemonic.text

Supported paths for `op=delete`:
  - meanings[i]         (removes meaning + its sentences)
  - meanings[i].examples[j]

Empty `patches` array if no fix is warranted.
"""
