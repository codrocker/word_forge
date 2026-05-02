# Sailing Words SQLite 打包脚本 — 设计文档

- **日期**: 2026-05-02
- **作者**: allen + Claude
- **目标**: 把 prod `domain.*` 全量投影成 flutter 端可直读的 SQLite,替换现在
  `/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip`
  里那份仅含半份数据的旧包。
- **背景**: 现有 zip 内的 `sailing.db` 表结构是 `word(word_id INTEGER PK, word_json TEXT)`,
  前端直接反序列化 `word_json`。飞书 wiki
  `https://lpt2q1lbzh.feishu.cn/wiki/U0w0wzWvdihbH1kUYltc1kjPn6c`(doc id
  `PJP9dcEXPoNrfgxPZltc6co4nmb`)约定了 `word_json` 的 schema(下称 **word-v1**)。
  当前版本没打全(只 ~若干条),需要用 prod 的 121057 词做全量重建。

## 1. 范围与非目标

**要做**:
- 新增打包脚本 `scripts/packaging/export_sailing_sqlite.py`,从 prod `domain.*` 全量读出 → 构建 word-v1 JSON → 写入 SQLite(schema 与现状兼容) → zip。
- 扩展 `src/wordforge/stages/export.py::_POS_MAP`,从现有 8 种扩到 10 种(`num`/`art`)+ `phrasal_verb`,使 LLM 后续返回这些 pos 时能正确写进 `domain.meanings.pos`。
- `scripts/packaging/README.md`:怎么跑、输入输出、常见坑。

**不做**:
- 不改 `domain.*` 的 schema、不改 export stage 的导出逻辑、不改 `serving.word_payload`。
- 不做增量打包(全量覆盖 sqlite 文件即可,121k 词单次生成秒级)。
- 不直接调用 `serving.word_payload` — 它的 JSONB 字段命名与 word-v1 不兼容(`phonetic: {us,uk,...}` vs `phonetic_us: {form, audio}`),不值得做 schema 翻译层。
- 不做 SQLite 性能优化(VACUUM / PRAGMA 调优):代码里挂 TODO,之后在前端实测启动耗时后再定。

## 2. 数据源

- prod RDS `wordforge` 库,`domain.words` / `domain.meanings` / `domain.sentences` / `domain.mnemonics`。
- 凭证:运行前 `source ~/.wordforge/prod.env`(只读 DATABASE_URL)。脚本本身不碰任何凭证文件。
- 读取用 SQLAlchemy 单 engine;只 SELECT,不写任何表。
- **跳过** `domain.phrases`(prod 实测 0 行)和 `domain.package_word`(前端本地打包无需 package 维度)。

## 3. word-v1 目标 schema(来自飞书 wiki)

```json
{
  "id": 123,
  "type": 1,
  "form": "hello",
  "phonetic_us": {"form": "[həˈloʊ]", "audio": "https://..."},
  "phonetic_uk": {"form": "[həˈloʊ]", "audio": "https://..."},
  "meanings": [
    {
      "id": 123,
      "user_group": 0,
      "pos_en": "n.",
      "pos_cn": "名词",
      "phonetic_us": {"form": "[həˈloʊ]", "audio": ""},
      "phonetic_uk": {"form": "[həˈloʊ]", "audio": ""},
      "pos_meanings": ["你好", "您好"],
      "sentences": [
        {"id": 123, "user_group": 0, "form": "Hello world", "meaning": "你好世界", "audio": "", "is_collected": 0}
      ]
    }
  ],
  "mnemonics": [
    {"id": 123, "type": 1, "user_group": 0, "creator": {}, "is_pinned": 0, "content": "..."}
  ]
}
```

## 4. 字段映射表

| word-v1 字段 | 来源 | 规则 |
|---|---|---|
| `id` | `domain.words.word_id` | 直传 (BIGINT) |
| `type` | `domain.words.type` | 直传 (1 单词 / 2 phrase) |
| `form` | `domain.words.form` | 直传 |
| `phonetic_us.form` | `domain.words.phonetic_us` | NULL → `""` |
| `phonetic_us.audio` | `domain.words.audio_us` | NULL → `""` |
| `phonetic_uk.form` | `domain.words.phonetic_uk` | NULL → `""` |
| `phonetic_uk.audio` | `domain.words.audio_uk` | NULL → `""` |
| `meanings[].id` | `domain.meanings.meaning_id` | 直传 |
| `meanings[].user_group` | — | 固定 `0`(本地打包无用户分组) |
| `meanings[].pos_en` | `domain.meanings.pos` via 反映射表 | 见 §5 |
| `meanings[].pos_cn` | `domain.meanings.pos` via 反映射表 | 见 §5 |
| `meanings[].phonetic_us` | 复用 word 级 | 同 word.phonetic_us(DB 里 meaning 无自己的音标列) |
| `meanings[].phonetic_uk` | 复用 word 级 | 同上 |
| `meanings[].pos_meanings` | `domain.meanings.cn_paraphrase` 拆分 | 见 §6 |
| `meanings[].sentences[].id` | `domain.sentences.sentence_id` | 直传 |
| `meanings[].sentences[].user_group` | — | 固定 `0` |
| `meanings[].sentences[].form` | `domain.sentences.form` | 直传(英文例句) |
| `meanings[].sentences[].meaning` | `domain.sentences.translation` | 直传(中文翻译) |
| `meanings[].sentences[].audio` | — | 固定 `""`(DB 无句级音频) |
| `meanings[].sentences[].is_collected` | — | 固定 `0`(打包期未知用户状态) |
| `mnemonics[].id` | `domain.mnemonics.mnemonic_id` | 直传 |
| `mnemonics[].type` | `domain.mnemonics.type` | 直传(DB CHECK type=1) |
| `mnemonics[].user_group` | — | 固定 `0` |
| `mnemonics[].creator` | — | 固定 `{}`;**TODO**: 等前端确认具体字段形状 |
| `mnemonics[].is_pinned` | — | 固定 `0` |
| `mnemonics[].content` | `domain.mnemonics.content`(JSONB `{"kind","text"}`) | 取 `text`(非 str / 缺失 / 空 → `""` + log warning);`kind` 丢弃 |

**关于 `en_paraphrase`/`equivalents`/`synonyms`/`antonyms`**:word-v1 没对应槽位,**本版本丢弃**。若将来前端要展示可加 `meanings[].en_paraphrase`(非 breaking)。

## 5. POS 映射表(双向)

### 5.1 反映射(DB int → word-v1 字符串),用在 packager

| DB `pos` | `pos_en` | `pos_cn` |
|---|---|---|
| 1 | `n.` | `名词` |
| 2 | `v.` | `动词` |
| 3 | `adj.` | `形容词` |
| 4 | `adv.` | `副词` |
| 5 | `prep.` | `介词` |
| 6 | `conj.` | `连词` |
| 7 | `pron.` | `代词` |
| 8 | `interj.` | `感叹词` |
| 9 | `num.` | `数词` |
| 10 | `art.` | `冠词` |
| 201 | `phrase` | `短语动词` |
| NULL / 未知 | `""` | `""`(log warning) |

**实况**:prod 当前只有 1-8 有数据,pos=NULL 有 2091 条(多为 `vs`/`i'm`/`oz` 这类缩写/边界词,LLM 当时没打 pos)。9/10/201 目前 0 条,但按 CLAUDE.md 约定声明,随 LLM 能力升级可自然落数据。

### 5.2 正映射(LLM 字符串 → DB int),扩展 `src/wordforge/stages/export.py::_POS_MAP`

```python
_POS_MAP = {
    "n": 1, "v": 2, "adj": 3, "adv": 4,
    "prep": 5, "conj": 6, "pron": 7, "interj": 8,
    "num": 9, "art": 10,
    "phrasal_verb": 201,
}
```

**风险**:扩充后若 LLM prompt 没教过 `num`/`art`/`phrasal_verb`,不会有新 case 出现;不会影响存量数据。无向后兼容问题(未使用键无负面作用)。

**测试**:在 `tests/stages/test_export.py` 补 3 条 assertion 覆盖新 key。

## 6. `pos_meanings` 拆分规则(决策:Q2(b) 保守)

只按 **全角/半角分号 `；;`** 切;中文逗号 `，`、英文逗号 `,`、顿号 `、` **不拆**,留在段内。去首尾空白,丢空段。

```python
def _split_cn(cn: str) -> list[str]:
    if not cn:
        return []
    parts = re.split(r"[；;]", cn)
    return [p.strip() for p in parts if p.strip()]
```

**例**:
- `"黑体，粗体"` → `["黑体，粗体"]`(不拆)
- `"[wear 过去分词] 穿，戴"` → `["[wear 过去分词] 穿，戴"]`(不拆)
- `"见面；相遇；遇到"` → `["见面", "相遇", "遇到"]`

## 7. SQLite 输出

### 7.1 Schema(兼容现状)

```sql
CREATE TABLE word (
  word_id INTEGER PRIMARY KEY,
  word_json TEXT NOT NULL
);
```

**TODO(代码里挂):** 待前端实测启动与查询耗时后再定是否加:
- `VACUUM`(减文件大小)
- `PRAGMA page_size = 4096`、`PRAGMA journal_mode = DELETE`(移动端无 WAL sidecar)
- `CREATE INDEX` — JSON TEXT 无合适列,PRIMARY KEY 已带索引,预期不需要

### 7.2 打包流程

全程用 stdlib(`sqlite3` + `zipfile`),不依赖 shell 工具,跨平台可跑。

1. 建临时目录 `<tmp>/words.db`(`tempfile.TemporaryDirectory`),避免部分生成时污染最终 zip。
2. `sqlite3.connect(<tmp>/words.db)`;`CREATE TABLE` 同 §7.1;设置仅用于 bulk insert 期间的 pragma(`synchronous=OFF`、`journal_mode=MEMORY`)。注:这些只是临时加速写入,**与 §7.1 挂的"运行时 pragma TODO"是两回事** —— 前者在打包机器上生效,后者关心前端 flutter 打开 DB 时的行为。
3. 从 prod 流式读词 + 内存聚合 + 构 JSON。
4. `executemany("INSERT INTO word VALUES (?, ?)", ...)`,batch=5000。
5. `COMMIT`、`conn.close()`。
6. `zipfile.ZipFile(<output>, 'w', ZIP_DEFLATED, compresslevel=9)` 把 `words.db` 写进去,内部条目名固定 `words.db`。
7. 移动到 `--output`(默认
   `/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip`);临时目录自动清理。
8. 日志打 word 数、生成 sqlite 文件大小、zip 文件大小、耗时。

**命名**:zip 内条目改为 `words.db`(从旧 `sailing.db` 改名)。前端改动留给前端仓库。

### 7.3 数据取法(SQL 大纲)

一次批量拉 + 内存按 word_id 聚合,比逐词 N+1 查询快一个数量级。

```sql
-- 1. 全量 words
SELECT word_id, type, form, phonetic_us, phonetic_uk, audio_us, audio_uk
FROM domain.words ORDER BY word_id;

-- 2. 全量 meanings(按 word_id 分组进内存)
SELECT meaning_id, word_id, pos, cn_paraphrase
FROM domain.meanings ORDER BY word_id, meaning_id;

-- 3. 全量 sentences(按 meaning_id 分组进内存)
SELECT sentence_id, meaning_id, form, translation
FROM domain.sentences ORDER BY meaning_id, sentence_id;

-- 4. 全量 mnemonics(按 word_id 分组进内存,每词取第一条)
SELECT mnemonic_id, word_id, type, content
FROM domain.mnemonics ORDER BY word_id, mnemonic_id;
```

内存峰值估算:121k words × ~2 KB / word ≈ 250 MB。对本地打包机器完全 OK。

## 8. CLI

```bash
# 默认:走 prod,输出到前端仓库的既定路径
python -m scripts.packaging.export_sailing_sqlite

# 或直接当脚本跑
./.venv/bin/python scripts/packaging/export_sailing_sqlite.py

# 自定义输出
./.venv/bin/python scripts/packaging/export_sailing_sqlite.py \
  --output /tmp/words.db.zip

# Dry run(只构 JSON,不写文件,校验无崩溃)
./.venv/bin/python scripts/packaging/export_sailing_sqlite.py --dry-run
```

参数:
- `--output PATH` (默认
  `/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip`)
- `--dry-run` (不写 sqlite / zip,仅构建 JSON + 统计)
- `--limit N` (调试用,只处理前 N 个 word)

## 9. 文件布局

```
scripts/packaging/
  __init__.py
  export_sailing_sqlite.py   # 主脚本
  README.md                  # 使用说明 + 坑点
```

## 10. 幂等性 / 可重跑

- 脚本是纯"构建产物"类,不读不写 DB 外的东西。每次全量覆盖 zip 文件。
- 如果 prod 数据变,跑一遍 zip 就更新。
- 输出目录如不存在 → 自动 `mkdir -p`。
- 已存在的 zip → 覆盖(无备份,前端仓库本身有 git 历史可回溯)。

## 11. 验收标准

执行脚本后必须全部满足:

1. 脚本退出码 0。
2. `words.db.zip` 存在且可解压出 `words.db`。
3. `SELECT COUNT(*) FROM word` == prod `SELECT COUNT(*) FROM domain.words`(当前 121057)。
4. 随机抽 10 条 `word_json`,`json.loads` 无异常;必含 keys:`id`, `type`, `form`, `phonetic_us`, `phonetic_uk`, `meanings`, `mnemonics`。
5. 抽样里至少有 1 条 `meanings` 非空 + 1 条 `mnemonics` 非空(hello/the/hack 等高频词必定覆盖)。
6. 对 `form='hello'` 的 `word_json`,按以下口径 diff 通过:
   - `id` == `domain.words.word_id`;`type`、`form` 相同
   - `phonetic_us/uk` 的 `form`/`audio` 等于 `domain.words.phonetic_us/uk` + `audio_us/uk`(NULL 映射为 `""`)
   - `meanings` 长度 == `SELECT COUNT(*) FROM domain.meanings WHERE word_id=<hello_id>`
   - 每条 meaning 的 `pos_en`/`pos_cn` 对照 §5.1 表匹配
   - 每条 meaning 的 `sentences` 长度 == `SELECT COUNT(*) FROM domain.sentences WHERE meaning_id=<mid>`
   - `mnemonics[0].content` 等于 `domain.mnemonics.content->>'text'`
7. 产物 zip 大小与旧 4.4 MB 不同量级属正常(全量后预期 10-40 MB)。

## 12. 风险 & 注意

- **prod 读压力**:单次连 RDS 全量 4 查询,走 ORDER BY 主键/外键索引,估计 30-90 秒完成,对生产读库压力可忽略。如在业务高峰执行仍建议择时。
- **pos=NULL 2091 条**:反映射表 fallback `""`。flutter 端若根据 `pos_en` 非空做过滤会漏掉这批,验收前同前端确认 UI 表现。
- **type=2(phrase)69524 条全无音标音频**:前端 UI 若对 phrase 也展示音标框要接受 `{"form":"","audio":""}`。
- **mnemonics.content JSONB → 字符串**:若 DB 里存在非 `{"kind","text"}` 形状的历史行(不太可能但防御),取不到 `text` 时填 `""` + log warning。
- **现有 `sailing.db` 改名为 `words.db`**:需要同步让前端工程把读取路径从 `sailing.db` 改成 `words.db`。属前端侧变更,本 spec 不负责;README 里要写清楚。

## 13. 待跟进 TODO(写进代码 `# TODO:`)

- [ ] Q1 mnemonics.creator 空对象 `{}` → 等前端给出明确 shape(`id`/`name`/`avatar` 等)后回填
- [ ] SQLite 打包优化实测(VACUUM / page_size / journal_mode),等前端给出 flutter 端启动耗时基线
- [ ] `serving.word_payload` schema 与 word-v1 对齐(长期):让前端用同一套 JSON,减少重投影成本
