# wordforge → MySQL 镜像同步 (word_forge DB) — 设计文档

- **日期**: 2026-05-02
- **作者**: allen + Claude
- **目标**: 把 wordforge PG(`domain.*`)产出的 word / meaning / sentence /
  mnemonic / phrase 数据持续镜像到 MySQL 新 database `word_forge`,供 gozero
  后端在过渡期读取。长期 PG 还是 MySQL 由业务后续决定,本设计只管"镜像正确
  且无读空窗"。
- **事实源**:
  - Schema: [飞书 wiki - MySQL 数据模型和定义](https://lpt2q1lbzh.feishu.cn/wiki/wikcnQFiS6CvAj8sfXW86mK1d2G)(SSOT)
  - wordforge PG 数据: prod `rm-cn-*.rwlb.rds.aliyuncs.com:5432/wordforge` `domain.*`
  - momo MySQL 实例版本: **5.7.28**(Ubuntu 18.04)—— 无 `ALGORITHM=INSTANT`,但
    `RENAME TABLE` 多表原子切换 5.7 也支持,不影响本 spec 的 shadow-swap 方案

## 0. 相关文档 / 决策路径

- brainstorm Q1-Q14 讨论记录见对话历史(本 spec 本身是结论)
- 调研阶段文档 `docs/superpowers/specs/2026-05-02-writeback-to-mysql-discovery.md`
- **关键决策差异**: Q2 原定"1:1 复刻 momo MySQL 实例",对齐 wiki 后改为"1:1
  复刻 wiki schema"(wiki 领先 momo 实例,momo 实例不是事实源)

## 1. 范围与非目标

**要做**:
- 新建 MySQL database `word_forge`(同实例 `120.27.242.42:3306`),建 5 张业务表
  (`word / meaning / sentence / mnemonic / phrase`)+ 5 张同构 `_shadow` 副本
- 新增一次性初始化 SQL,创建两个专用账号 `wordforge_writer`(读写)和
  `wordforge_reader`(只读)
- 新增同步脚本 `scripts/replicate/mirror_to_mysql.py`,读 PG `domain.*` →
  灌 5 张 MySQL `_shadow` 表 → 单条多表 `RENAME TABLE` 原子 swap
- 新增对账脚本 `scripts/replicate/verify_mysql_mirror.py`,count + CRC32 checksum
  两侧对拍
- MySQL schema DDL 落盘 `scripts/replicate/mysql_schema.sql`,脚本启动时 sanity
  检查(表存在、列齐),缺了就报错让人工执行 DDL,不自动 DDL

**不做**:
- 不改 wordforge pipeline(`stages/export.py` / `ingest.py` 零改动)
- 不上定时调度(本轮只做一键手跑,cron/systemd 留给用户后续加)
- 不回写到 momo `word` 库(硬约束)
- 不改 wordforge `domain.*` schema
- 不做 pipeline 层双写 / 2PC / CDC(准实时派生即可,PG 是源)
- 不做行级 diff(对账只到 count + checksum 粒度)
- 不改 wordforge mnemonic content 格式(`{"kind":"phonetic","text":"..."}` 沿用)
- 不建 `mnemonic_LLM` / `phrase_bak_*` / `crawler_word` 等 wiki 提到但和
  wordforge 无关的表

## 2. 数据源 + 凭证

| 资源 | 位置 | 用途 |
|---|---|---|
| PG `wordforge` | prod RDS(`~/.wordforge/prod.env`)| 读 `domain.words/meanings/sentences/mnemonics/phrases`|
| MySQL `word_forge`(新) | `120.27.242.42:3306`| 写镜像 + gozero 后端读|
| MySQL 写账号凭证 | `~/.wordforge/mysql_writer.env`(chmod 600,新建)| 同步脚本用|
| MySQL 读账号凭证 | `~/.wordforge/mysql_reader.env`(chmod 600,新建)| 调试/对账用|

**env 文件内容模板**(凭证不进仓):

```bash
# ~/.wordforge/mysql_writer.env
export WORDFORGE_MYSQL_WRITER_DSN='mysql+pymysql://wordforge_writer:<strong-pwd>@120.27.242.42:3306/word_forge?charset=utf8mb4'

# ~/.wordforge/mysql_reader.env
export WORDFORGE_MYSQL_READER_DSN='mysql+pymysql://wordforge_reader:<strong-pwd>@120.27.242.42:3306/word_forge?charset=utf8mb4'
```

同步脚本读 `WORDFORGE_MYSQL_WRITER_DSN`,对账脚本读 `WORDFORGE_MYSQL_READER_DSN` +
`DATABASE_URL`(PG 只读)。

## 3. 一次性初始化(人工跑一次)

### 3.1 建 database + 账号(用 `user_service_1` 账号,无需 root)

`user_service_1` 在 momo 实例上已有 `ALL PRIVILEGES ON *.* WITH GRANT OPTION`
(`mysql -e "SHOW GRANTS FOR CURRENT_USER()"` 可验证),能直接 `CREATE DATABASE` /
`CREATE USER` / `GRANT`。凭证在 `~/.wordforge/momo.env`。

```bash
source ~/.wordforge/momo.env
mysql -h"$MOMO_MYSQL_HOST" -P"$MOMO_MYSQL_PORT" -u"$MOMO_MYSQL_USER" -p"$MOMO_MYSQL_PASSWORD" \
  < scripts/replicate/init_database.sql
```

SQL 内容:

```sql
CREATE DATABASE word_forge DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 生成密码: openssl rand -base64 24
CREATE USER 'wordforge_writer'@'%' IDENTIFIED BY '<pwd-writer>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX ON word_forge.*
  TO 'wordforge_writer'@'%';

CREATE USER 'wordforge_reader'@'%' IDENTIFIED BY '<pwd-reader>';
GRANT SELECT ON word_forge.* TO 'wordforge_reader'@'%';

FLUSH PRIVILEGES;
```

writer 账号需要 `CREATE/DROP/ALTER/INDEX` 是为了首次 schema apply + 未来 schema
变更。日常同步只用 `SELECT/INSERT/DELETE`,TRUNCATE 需要 `DROP`。

### 3.2 建表(执行 `scripts/replicate/mysql_schema.sql`)

用 `wordforge_writer` 身份跑,对每张业务表同时建 `<name>` 和 `<name>_shadow`
两份。DDL 见 §4。

### 3.3 凭证文件(本地)

```bash
umask 077
printf 'export WORDFORGE_MYSQL_WRITER_DSN=...\n' > ~/.wordforge/mysql_writer.env
printf 'export WORDFORGE_MYSQL_READER_DSN=...\n' > ~/.wordforge/mysql_reader.env
chmod 600 ~/.wordforge/mysql_writer.env ~/.wordforge/mysql_reader.env
```

## 4. MySQL Schema(权威引自 wiki,截取与本轮相关的 5 张表)

> **信任链**: wiki > MySQL 实例 DDL。wiki 领先的地方以 wiki 为准(如
> `user_group` 取代 `group`、`source` 列新增、`mnemonic` 单表合并)。

### 4.1 `word` 表

```sql
CREATE TABLE `word` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL COMMENT '单词ID',
  `type` bigint NOT NULL COMMENT '单词类型,1-单词。2-短语',
  `form` varchar(255) NOT NULL COMMENT '单词的形式',
  `phonetic_us` varchar(255) NOT NULL COMMENT '美式kk音标',
  `audio_us` varchar(255) DEFAULT NULL,
  `phonetic_uk` varchar(255) NOT NULL,
  `audio_uk` varchar(255) DEFAULT NULL,
  `meanings` TEXT DEFAULT NULL COMMENT '[{"id":meaning_id},...]',
  `mnemonics` TEXT DEFAULT NULL COMMENT '[{"id":mnemonic_id},...]',
  `plural` varchar(255) DEFAULT NULL,
  `phrases` TEXT DEFAULT NULL,
  `structure` TEXT DEFAULT NULL,
  `third_person` varchar(255) DEFAULT NULL,
  `present_participle` varchar(255) DEFAULT NULL,
  `past_tense` varchar(255) DEFAULT NULL,
  `past_participle` varchar(255) DEFAULT NULL,
  `base` bigint DEFAULT NULL,
  `comparative` varchar(255) DEFAULT NULL,
  `superlative` varchar(255) DEFAULT NULL,
  `derivatives` TEXT DEFAULT NULL,
  `morpheme_derivatives` TEXT DEFAULT NULL,
  `family` TEXT DEFAULT NULL,
  `source` TEXT DEFAULT NULL,
  `status` bigint NOT NULL COMMENT '0=待审核,1=已上线,2=已删除',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `word_id` (`word_id`),
  KEY `form` (`form`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

注意:**和 momo MySQL 实例的差异** — 这里有 `source TEXT`(wiki 新增,实例还没
补)。wordforge `domain.words.source` 直接对应。

### 4.2 `meaning` 表

```sql
CREATE TABLE `meaning` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `meaning_id` bigint NOT NULL,
  `word_id` bigint NOT NULL,
  `user_group` bigint DEFAULT NULL COMMENT '用户群分组(wiki 改名,非 group)',
  `pos` bigint DEFAULT NULL COMMENT '词性枚举: 1-n, 2-v, 3-adj, 4-adv, 5-prep, 6-conj, 7-pron, 8-interj, 9-num, 10-art, 201-phrasal_verb (与 wordforge _POS_MAP 对齐)',
  `pos_sub` bigint DEFAULT NULL,
  `equivalents` TEXT DEFAULT NULL COMMENT '["直译词1","直译词2"] — 纯字符串数组',
  `synonyms` TEXT DEFAULT NULL COMMENT '[{"id":word_id}]',
  `antonyms` TEXT DEFAULT NULL COMMENT '[{"id":word_id}]',
  `phonetic_us` varchar(255) DEFAULT NULL,
  `audio_us` varchar(255) DEFAULT NULL,
  `phonetic_uk` varchar(255) DEFAULT NULL,
  `audio_uk` varchar(255) DEFAULT NULL,
  `cn_paraphrase` TEXT DEFAULT NULL,
  `en_paraphrase` TEXT DEFAULT NULL,
  `sentences` TEXT DEFAULT NULL COMMENT '[{"sentence_id":123}]',
  `source` TEXT DEFAULT NULL,
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `meaning_id` (`meaning_id`),
  KEY `word_id` (`word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

注意:
- 字段名是 **`user_group`**(wiki),不是 momo 实例里的 `group`(保留字)
- `pos` 枚举按 wiki:adj=3,adv=4(与 momo 实例错位)。wordforge `_POS_MAP` 已按
  wiki,直接沿用
- `equivalents` 是纯字符串数组 `["x","y"]`;`synonyms/antonyms/sentences` 是对象
  数组。wordforge 如果没有这些就填 NULL

### 4.3 `sentence` 表

```sql
CREATE TABLE `sentence` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL,
  `meaning_id` bigint NOT NULL,
  `sentence_id` bigint NOT NULL,
  `user_group` bigint DEFAULT NULL,
  `form` TEXT,
  `highlight` varchar(255) DEFAULT NULL COMMENT '[[start,end],...] 整数区间对',
  `translation` TEXT NOT NULL,
  `audio_us` varchar(255),
  `audio_uk` varchar(255),
  `source` TEXT DEFAULT NULL,
  `citation` bigint DEFAULT NULL COMMENT '句子出处 id(wiki 新列,momo 实例的 source 改名)',
  `citation_detail` TEXT COMMENT '出处详情(wiki 改名,实例里是 source_detail)',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `sentence_id` (`sentence_id`),
  KEY `meaning_id` (`meaning_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 4.4 `mnemonic` 表(wiki 合并后单张)

```sql
CREATE TABLE `mnemonic` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `word_id` bigint NOT NULL,
  `mnemonic_id` bigint NOT NULL,
  `type` bigint NOT NULL COMMENT '1-谐音联想',
  `user_group` bigint NOT NULL,
  `content` TEXT NOT NULL COMMENT 'wordforge 产出格式: {"kind":"phonetic","text":"..."}',
  `source` TEXT DEFAULT NULL,
  `creator_id` bigint NOT NULL COMMENT 'LLM 产出填 0; 模型名写 source',
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

注意:
- wiki 没 `mnemonic_LLM` 表(早已合并到 `mnemonic`)
- `content` 字段里的 JSON **不是 momo `{sound_alike,imagination}` 格式**,是
  wordforge 的 `{"kind":"phonetic","text":"..."}`。gozero 后端切到新库读该字段
  需要改 unmarshal 结构体
- `creator_id bigint NOT NULL` 对 LLM 产出无人类 UID,填 `0` 占位;
  `source` 字段写 model 名(如 `LLM:claude_sonnet_4_5_thinking`)以补充出处

### 4.5 `phrase` 表

```sql
CREATE TABLE `phrase` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `phrase_id` bigint NOT NULL,
  `form` varchar(255) NOT NULL,
  `meaning` TEXT NOT NULL,
  `audio_us` varchar(255) NOT NULL,
  `audio_uk` varchar(255) NOT NULL,
  `create_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

wordforge `domain.phrases` 目前 0 行,建表占位即可。

### 4.6 Shadow 副本

对每张业务表再建一份同构的 `<name>_shadow`:

```sql
CREATE TABLE word_shadow LIKE word;
CREATE TABLE meaning_shadow LIKE meaning;
CREATE TABLE sentence_shadow LIKE sentence;
CREATE TABLE mnemonic_shadow LIKE mnemonic;
CREATE TABLE phrase_shadow LIKE phrase;
```

## 5. PG domain.* → MySQL word_forge.* 字段映射

以 wordforge `domain.*` 为输入,MySQL 为输出。未列出的 MySQL 列默认 NULL。

### 5.1 word

| MySQL 列 | 来源 | 说明 |
|---|---|---|
| `word_id` | `domain.words.word_id` | 直传 BIGINT 10 万级 |
| `type` | `domain.words.type` | 直传 |
| `form` | `domain.words.form` | 直传 |
| `phonetic_us / phonetic_uk` | `domain.words.phonetic_us / phonetic_uk` | NULL → `""`(wiki 定义 NOT NULL) |
| `audio_us / audio_uk` | `domain.words.audio_us / audio_uk` | NULL 允许 |
| `source` | `domain.words.source` | 直传 |
| `status` | 固定 `1` | wordforge 能走到这里的都是"已上线"(spec decision) |
| `meanings` | 按关系构造 `[{"id": mid}, ...]` | 从 `domain.meanings WHERE word_id=? ORDER BY meaning_id` 查 id,json.dumps |
| `mnemonics` | 按关系构造 `[{"id": mid}, ...]` | 同上,从 `domain.mnemonics` |
| `phrases` | 按关系构造 `[{"id": pid}, ...]` | 同上,从 `domain.phrases`(0 行即空数组) |
| 其余字段 (`plural/past_tense/...`) | 全部 NULL | wordforge 没产出,按 wiki"临时设置 NULL"清单的约定 |

### 5.2 meaning

| MySQL 列 | 来源 | 说明 |
|---|---|---|
| `meaning_id / word_id` | `domain.meanings.meaning_id / word_id` | 直传 |
| `user_group` | NULL | wordforge 关系表无该字段 |
| `pos` | `domain.meanings.pos` | 直传(wordforge 已按 wiki 枚举) |
| `pos_sub` | NULL | 按 wiki"临时设置 NULL"清单 |
| `equivalents` | `domain.meanings.equivalents` | 已是 JSONB 数组,`json.dumps` 字符串版 |
| `synonyms / antonyms` | `domain.meanings.synonyms / antonyms` | 同上,JSONB → 字符串 |
| `cn_paraphrase / en_paraphrase` | 同名字段 | 直传 |
| `sentences` | 构造 `[{"sentence_id": sid}, ...]` | 从 `domain.sentences WHERE meaning_id=? ORDER BY sentence_id` |
| `phonetic_us/uk / audio_us/uk` | `domain.meanings.*` 或 NULL | meaning 有自己的音标时用,否则 NULL |
| `source` | `domain.meanings.source` | 直传 |

### 5.3 sentence

| MySQL 列 | 来源 | 说明 |
|---|---|---|
| `sentence_id / word_id / meaning_id` | `domain.sentences.*` | 直传 |
| `form / translation / highlight` | 同名字段 | `highlight` 如果 wordforge 有就直传 `[[start,end]]` JSON,否则 NULL |
| `audio_us / audio_uk` | NULL | 按 wiki"临时设置 NULL" |
| `source` | `domain.sentences.source` | 直传 |
| `citation / citation_detail` | NULL | wordforge 暂无该字段,wiki 约定可 NULL |
| `user_group` | NULL | 同 meaning |

### 5.4 mnemonic

| MySQL 列 | 来源 | 说明 |
|---|---|---|
| `mnemonic_id / word_id` | `domain.mnemonics.mnemonic_id / word_id` | 直传 |
| `type` | `domain.mnemonics.type` | wordforge 全是 1(谐音联想) |
| `user_group` | `0` | NOT NULL,填 0 作默认组 |
| `content` | `domain.mnemonics.content::text` | 直接 dump JSONB,格式 `{"kind":"phonetic","text":"..."}` |
| `source` | `domain.mnemonics.source` | wordforge 形如 `pipeline:stages.mnemonic` / model 名 |
| `creator_id` | `0` | NOT NULL,LLM 产出无 UID,统一占位;模型名靠 `source` |

### 5.5 phrase

wordforge `domain.phrases` 0 行,镜像时 SELECT 空、INSERT 空、RENAME 空 shadow
表。表结构存在即满足后端 schema 期望。

## 6. 同步流程

```
stage 0: 环境校验
  - require env: DATABASE_URL, WORDFORGE_MYSQL_WRITER_DSN
  - sanity check: 所有 <name> 和 <name>_shadow 表存在(SELECT 1 FROM ... LIMIT 0);
    缺了报错退出(不自动 DDL,强制人工跑 mysql_schema.sql)

stage 1: 清空 shadow 表
  FOREACH t in (word, meaning, sentence, mnemonic, phrase):
    TRUNCATE TABLE <t>_shadow

stage 2: 从 PG 读 + 向 shadow 批量 INSERT
  - phrase 空表直接跳过
  - word / meaning / sentence / mnemonic:
      按 PK 升序 SELECT,每 5000 行 batch INSERT,不走 upsert(shadow 是干净的)
  - word.meanings / word.mnemonics / word.phrases / meaning.sentences 这些
    JSON 聚合字段,单独一次 SELECT + group_by_word_id 构造 map,INSERT 时查 map 注入

stage 3: 原子 swap(两条 RENAME 语句)
  -- 语句 A: 原子切换主表 + 把当前主表挪到 _old
  RENAME TABLE
    word     TO word_old,     word_shadow     TO word,
    meaning  TO meaning_old,  meaning_shadow  TO meaning,
    sentence TO sentence_old, sentence_shadow TO sentence,
    mnemonic TO mnemonic_old, mnemonic_shadow TO mnemonic,
    phrase   TO phrase_old,   phrase_shadow   TO phrase;

  -- 语句 B: _old 改回 _shadow,供下轮重用(非关键路径,读路径已在语句 A 切完)
  RENAME TABLE
    word_old     TO word_shadow,
    meaning_old  TO meaning_shadow,
    sentence_old TO sentence_shadow,
    mnemonic_old TO mnemonic_shadow,
    phrase_old   TO phrase_shadow;

  注意:
    - MySQL 不允许在一条 RENAME TABLE 里对同一表做两次改名,所以必须两条语句
    - 读服务在语句 A 原子结束那一刻就已经看到新数据;语句 B 只影响本进程
      下轮重用的 shadow 槽位,不影响 gozero 读
    - 若语句 B 失败(极罕见),下轮 stage 0 会检测到 `*_old` 存在,清理后继续

stage 4: 内层 sanity check
  FOREACH t:
    pg_count = SELECT count(*) FROM domain.<t>
    mysql_count = SELECT count(*) FROM <t>
  不等就 WARN 到 stderr,写一条到 ./replicate_run.jsonl

stage 5: 完成,打印 summary
```

### 6.1 读无空窗证明

MySQL `RENAME TABLE ... A TO B, C TO A, ...` 单语句原子
(<https://dev.mysql.com/doc/refman/8.0/en/rename-table.html>):
> "the rename operation is done atomically; no other session can access any of
> the tables while the rename is in progress"

gozero 后端读的永远是"上一轮完整 swap 后"或"本轮完整 swap 后"的数据,不存在空窗。

### 6.2 事务边界

- stage 1-2 对 shadow 表写,整个批次 **不包** 事务(shadow 本身是"本轮重灌",
  中途挂了下轮重跑即可,允许脏 shadow)
- stage 3 RENAME 是 DDL 隐式提交,天然原子,不需要也不能放进事务

## 7. 对账策略(L3 双层)

### 7.1 内层 — 同步脚本尾部 count check

见 §6 stage 4。只查 count、不查 checksum,极快。

### 7.2 外层 — 独立对账脚本 `verify_mysql_mirror.py`

对每张表做:

```sql
-- PG 侧(按关键字段哈希)
SELECT md5(string_agg(
  word_id || '|' || form || '|' || type || '|' || coalesce(source, ''),
  ',' ORDER BY word_id
)) FROM domain.words;

-- MySQL 侧
SELECT BIT_XOR(CRC32(CONCAT_WS('|',
  word_id, form, type, IFNULL(source, '')
))) FROM word_forge.word;
```

两边不能直接等(哈希算法不同),做法:

- 两边 `COUNT(*)` 必须相等
- 两边按关键字段分别 `MD5` 一组和(PG)+ `BIT_XOR(CRC32)` 一组和(MySQL),
  记在报告里,**每天第一次 baseline、之后比当天结果**:两边各自的 checksum
  只要和上轮自己一致即可(因为 PG 源稳定时 MySQL 镜像 checksum 也应稳定)

差异写 `./drift_report.jsonl`,stdout 带 `DRIFT:` 前缀便于 cron 监控。

## 8. CLI / 脚本形态

### 8.1 同步脚本

```
scripts/replicate/
├── __init__.py
├── mirror_to_mysql.py           # 主同步脚本
├── verify_mysql_mirror.py       # 对账脚本
└── mysql_schema.sql             # DDL 落盘
```

```
uv run python scripts/replicate/mirror_to_mysql.py [--dry-run]
  # 不带 --dry-run 就是实跑。dry-run 做 SELECT 对拍 + 构造 batch 但不 INSERT/RENAME
```

### 8.2 对账脚本

```
uv run python scripts/replicate/verify_mysql_mirror.py [--baseline path]
  # 不传 --baseline 则只打印当前两边 checksum;传了就和历史比较
```

### 8.3 日志

- 进度到 stdout(每处理 10000 行打一次时间戳 + 进度)
- WARN / ERROR 到 stderr,同时 append 到 `./replicate_run.jsonl`

## 9. 错误处理

- **env 缺失**: stage 0 报清晰错误 + 用法提示,退出 1
- **表不存在**: stage 0 报错,提示跑 `mysql_schema.sql`,退出 1
- **PG 连接失败**: 同样 stage 0 就会炸,不进 stage 1
- **MySQL 连接失败 / 批量 INSERT 失败**: 整个 run 中止,shadow 可能脏,留给下次
  重跑(shadow 不保留中间状态)。不写 `./replicate_run.jsonl` 为"成功"
- **RENAME 失败**: 极罕见(通常是被并发 DDL 锁或 mdl 冲突)。报错退出,shadow 已填
  但主表未换。下次重跑:TRUNCATE shadow、重灌、再 rename,幂等
- **count mismatch**: WARN + 写 jsonl,不中止。靠对账脚本和人工兜底
- **禁止裸 except**: 只捕具体异常(`sqlalchemy.exc.SQLAlchemyError` 等)

## 10. 手动一键跑 Playbook

```bash
# 首次:初始化 database + 账号(人工,root 身份)
mysql -h 120.27.242.42 -u root -p < scripts/replicate/init_database.sql
# ↑ init_database.sql 是人工生成,含 3.1 那段,<pwd-writer/reader> 用
#   openssl rand -base64 24 填好,不进仓

# 首次:建表
mysql -h 120.27.242.42 -u wordforge_writer -p word_forge \
  < scripts/replicate/mysql_schema.sql

# 凭证落盘
umask 077
cat > ~/.wordforge/mysql_writer.env <<EOF
export WORDFORGE_MYSQL_WRITER_DSN='mysql+pymysql://wordforge_writer:<pwd>@120.27.242.42:3306/word_forge?charset=utf8mb4'
EOF
cat > ~/.wordforge/mysql_reader.env <<EOF
export WORDFORGE_MYSQL_READER_DSN='mysql+pymysql://wordforge_reader:<pwd>@120.27.242.42:3306/word_forge?charset=utf8mb4'
EOF
chmod 600 ~/.wordforge/mysql_*.env

# 日常:一键同步
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_writer.env
uv run python scripts/replicate/mirror_to_mysql.py

# 日常:对账
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_reader.env
uv run python scripts/replicate/verify_mysql_mirror.py
```

## 11. 测试策略

- `tests/replicate/test_field_mapping.py` — 纯函数单测,每张表的
  `_row_to_mysql(domain_row, *context)` 输入 PG 行 dict + 相关关系 id list,
  输出 MySQL row dict。覆盖:
  - word: meanings/mnemonics/phrases 聚合字段构造
  - meaning: pos 枚举直传、equivalents 字符串数组、sentences 对象数组
  - sentence: highlight 整数数组、NULL 字段按 wiki "临时设置 NULL" 清单填空
  - mnemonic: content JSON 透传、creator_id=0、user_group=0
  - phrase: 空 list 输入 → 空输出
- 不 mock 真实 PG / MySQL。字段映射是纯函数
- 手工验证: dry-run 跑一次对第一轮输出做 spot check;正式跑后靠对账脚本

## 12. 风险与权衡

| 风险 | 评估 | 缓解 |
|---|---|---|
| wiki schema 和实例 DDL drift | 本次发现 5 处 | 本 spec 以 wiki 为准;发现新 drift 登记到 feishu-wiki-index.md"已知失真" |
| gozero 后端切库时 mnemonic content 格式不兼容 | 必现 | spec 里明示,后端 unmarshal 改成 `{kind,text}` |
| gozero pos 枚举 hardcode 了 momo 实例版 | 可能 | 告知后端 pos 按 wiki 版对齐(wordforge 已是 wiki 版) |
| 首轮 swap 前无 `_old` 表,RENAME 缺操作数 | 不存在 | §6 stage 3 统一用两条 RENAME 语句(A: `<name>↔<name>_shadow` 经 `<name>_old` 中转;B: `<name>_old → <name>_shadow`),首轮非首轮语法一致,无需分路径 |
| 121k+241k+534k+121k ≈ 1M 行 INSERT 耗时 | 30-60s 可接受 | 不加索引、batch 5000、autocommit=off 批提交 |
| RENAME 撞上长查询 MDL wait | 低概率 | MySQL 默认 `lock_wait_timeout` 60s,失败退出,下次自然重试 |

### 异常清理

如果上一轮执行到语句 A 后、语句 B 前崩溃,`<name>_old` 会残留。stage 0 sanity
check 新增一条: `SHOW TABLES LIKE '%_old'` 若非空,先 `DROP TABLE` 清理掉
`<name>_old`,再继续本轮流程。语句 B 失败通常因为 MDL 冲突或进程中断,残留
`_old` 是 drop 安全的(已不是读路径)。

## 13. 非目标再次强调

- 不动 pipeline
- 不做定时调度(留给用户)
- 不做 CDC / logical replication
- 不做行级 diff
- 不建 wiki 列出但本轮不需要的表(category / package_* / morpheme / crawler_word 等)
- 不改 wiki(除非发现明确失真,且另开 PR)
