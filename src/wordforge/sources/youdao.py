"""YoudaoClient: fetch Youdao jsonapi result (cache-aware).

Hits `https://dict.youdao.com/jsonapi` — returns a rich JSON envelope with
`simple` (IPA + audio), `ec` (EN→CN meanings), `ee` (EN→EN), `phrs`, etc.
Raw JSON goes into `pipeline.external_call_cache` and into `stage_artifacts`
as `{"raw_json": {...}}`. Downstream stages parse this dict; no HTML scraping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from wordforge.cache import CacheStore, canonical_cache_key

# Full mobile-client `dicts` param. Decoded form:
#   {"count":99,"dicts":[["ec","ee","phrs","simple","wordform",
#                         "collins","syno","rel_word","blng_sents_part"]]}
# blng_sents_part is youdao's bilingual example corpus — primary source of
# reference examples for the examples stage. cache_key is word-only, so
# adding a dict here does NOT invalidate existing cache rows; previously
# cached words keep their old response (no blng_sents_part field). New
# lookups populate it. examples stage tolerates either shape.
_YOUDAO_PATH = "/jsonapi"
_YOUDAO_DICTS = (
    "%7B%22count%22%3A99%2C%22dicts%22%3A%5B%5B%22ec%22%2C%22ee%22%2C%22phrs%22%2C"
    "%22simple%22%2C%22wordform%22%2C%22collins%22%2C%22syno%22%2C%22rel_word%22%2C"
    "%22blng_sents_part%22%5D%5D%7D"
)
_KIND = "dict:youdao"


@dataclass
class YoudaoClient:
    store: CacheStore
    http: httpx.Client

    def fetch(self, word: str, *, bypass_cache: bool = False) -> dict[str, Any]:
        key = canonical_cache_key(
            kind=_KIND,
            model="",
            request_params={},
            rendered_prompt="",
            input_payload={"word": word},
        )
        if not bypass_cache:
            row = self.store.get(_KIND, key)
            if row is not None:
                return row["response"]

        # Pre-encoded URL preserves the `%7B` in `dicts=` — httpx.params= would
        # re-encode it and Youdao's server rejects the mangled form.
        from urllib.parse import quote

        url = (
            f"{_YOUDAO_PATH}?jsonversion=2&client=mobile&q={quote(word)}"
            f"&dicts={_YOUDAO_DICTS}&keyfrom=mdict.7.2.0.android&model=honor&mid=5.6.1"
        )
        resp = self.http.get(url)
        resp.raise_for_status()
        raw_json = resp.json()
        payload = {"raw_json": raw_json}

        self.store.put(kind=_KIND, cache_key=key, response=payload, cost_usd=0.0)
        return payload
