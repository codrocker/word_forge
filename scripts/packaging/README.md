# Sailing Words SQLite Packager

打包 prod `domain.*` → flutter 可读的 `words.db.zip`。

## 跨仓位置

本脚本产出的 zip 是 **离线打包产物**,与 `../../../docs/shared/data-flow.md`
描述的在线 `[阿里云 RDS] → [阿里云 OSS PackageWordsOss] → [words_core] →
[sailing_words]` 主数据流 **并存**:在线流按 package 粒度供应内容,本包走的
是一次性冷启动/离线兜底。改 schema 或流向时两条都要考虑到。

## 运行

```bash
source ~/.wordforge/prod.env  # 载入 prod 只读 DATABASE_URL
.venv/bin/python -m scripts.packaging.export_sailing_sqlite
```

默认输出到
`/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip`。

必须用 `-m` 模块模式跑,直接路径会 `ModuleNotFoundError: scripts`(CLAUDE.md 硬约定)。

## 参数

- `--output PATH` 自定义输出 zip 路径。
- `--limit N` 只打前 N 个词(调试)。
- `--dry-run` 只构 JSON,不写 SQLite 和 zip。

## 产物结构

zip 内单一条目 `words.db`,SQLite 表:

```sql
CREATE TABLE word (
  word_id INTEGER PRIMARY KEY,
  word_json TEXT NOT NULL
);
```

`word_json` 遵循 **word-v1** 约定,schema 事实源在飞书 wiki
`https://lpt2q1lbzh.feishu.cn/wiki/U0w0wzWvdihbH1kUYltc1kjPn6c`,
字段映射以 `builder.py` / `pos_map.py` 为准。

## 坑 & 约定

- **不要并行跑本脚本和 pytest** — CLAUDE.md 的 DB 隔离只挡 pytest 改写
  prod,本脚本读 prod,pytest 在本地 5434 挖 test DB;理论上物理隔离,但
  LLM quota、代理、本地端口池等仍共享。
- **`_POS_MAP` 与 `pos_map.py` 反映射表同步**:新增 pos(如 `phrasal_verb`=201)
  两边都要加。`src/wordforge/stages/export.py::_POS_MAP` 与
  `scripts/packaging/pos_map.py::_POS_DISPLAY` 是对偶关系。
- **zip 内条目名是 `words.db`**:旧版前端读的是 `sailing.db`;前端仓库要同步
  把资产路径改成 `words.db`。
- **Q1 `mnemonics[].creator` 先固定 `{}`**:等前端给出具体 shape 后回填。
- **运行时 SQLite pragma**(VACUUM / page_size / journal_mode=DELETE)未启用;
  等前端实测 flutter 端启动/查询耗时基线后再开。

## 范围与边界

**打包**:`domain.words` / `domain.meanings` / `domain.sentences` / `domain.mnemonics`。

**跳过**:
- `domain.phrases`(prod 当前 0 行)
- `domain.package_word`(前端本地包无需 package 维度,在线流已覆盖)
- `serving.word_payload`(字段命名与 word-v1 不兼容,不做翻译层)
