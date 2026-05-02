# wordforge → MySQL 回写 · 调研与决策待定

Status: 调研阶段 · 方向未定
Date: 2026-05-02
Author: Allen + Claude

## 目标一句话

wordforge 产出的单词富化数据（word / meaning / sentence / mnemonic / phrase）
当前存在阿里云 PG (wordforge RDS)，需要回写一份到 MySQL。package 相关表暂不动。

**硬约束**：不回写 momo 的 `word` 库（避免冲突）· 新建独立的 MySQL database 承接。

---

## 当前数据盘点

### momo MySQL（源 · host `120.27.242.42:3306` · db `word`）

| 表 | 行数 | 备注 |
|---|---|---|
| `word` | 122,664 | word_id ∈ [1000000001, ~1000122664]，bigint 外部 id |
| `meaning` | 180,920 | 有 group / pos / pos_sub，`sentences` 字段是 JSON 数组存 sentence_id |
| `sentence` | 80,615 | |
| `mnemonic` | **0** | 人工助记为空 |
| `phrase` | **0** | |
| `mnemonic_LLM` | 111,289 | 独立表，暗示 momo 侧 LLM 产物走另一张表 |
| `package_new / package_unit / package_word` | — | 本次不动 |

### wordforge PG（源 · 阿里云 RDS · db `wordforge`）

| 表 | 行数 | 备注 |
|---|---|---|
| `domain.words` | 121,057 | word_id 是 PG BIGSERIAL，从 100001 起，**非** momo id |
| `domain.meanings` | 241,763 | meaning_id 也是 BIGSERIAL |
| `domain.sentences` | 534,033 | |
| `domain.mnemonics` | 121,057 | 每个词一条 |
| `domain.phrases` | 0 | |
| `serving.word_payload` | 121,057 | 聚合 JSONB，按 word_id 一行一份完整渲染数据 |
| `domain.package*` | 1,443 / 63,064 / 2,036,549 | 本次不动 |

### 关键 gap

1. **ID 空间完全不同**
   - momo `word_id` ∈ 10 亿级（外部 id，bigint 宽空间）
   - wordforge `word_id` ∈ 10 万级（BIGSERIAL，紧凑）
   - 所有下游 id（meaning_id / sentence_id / mnemonic_id / phrase_id）同样两套命名空间
   - **回写时必须定：用哪套 id？是否需要 id 映射表？**

2. **行数不对齐**
   - momo word 122,664 行 vs wordforge 121,057 行 · 差 1,607
   - 推测来源：
     - wordforge ingest 时做过 casefold normalize + 去重（见 `recover_from_momo.py` step_build_inputs）
     - momo word 表内可能有 case 变体、type 差异
   - 结论：wordforge 这侧是"清洗后的权威数据"，但 momo 有 1.6k 历史条目未回写到 wordforge

3. **Schema 模型差异**
   - momo word 表内嵌 `meanings` / `mnemonics` / `phrases` 字段（text，JSON 数组存下游 id 引用）
   - wordforge 全关系化 · 没有内嵌 id 列表
   - momo meaning 表同样有 `sentences` 字段内嵌 JSON
   - momo 有 `group` 字段（用户分群）· wordforge 在关系表没有，只在 `serving.word_payload` JSON 里体现

4. **目标 MySQL 位置**
   - 走同一台 MySQL 实例（`120.27.242.42:3306`）还是另起
   - database 名字
   - 账号复用 `user_service_1` 还是新建

---

## 设计选项矩阵

### 选项 A：目标 DB 放哪

| 选项 | 说明 | 评价 |
|---|---|---|
| A1. 同实例 · 新 database `word_forge`（推荐） | 凭证/网络复用，database 级别隔离已够，命名上区分源 | 默认首选 |
| A2. 同实例 · 新 database `word_next` / `word_v2` | 同上，命名语义略不同 | 看命名偏好 |
| A3. 另起 MySQL 实例 | 物理隔离最强 | 运维成本 ↑，除非有硬合规要求否则不值得 |

### 选项 B：回写的 schema 复刻度

| 选项 | 说明 | 评价 |
|---|---|---|
| B1. 1:1 复刻 momo schema（推荐默认） | 新库表结构跟 momo `word/meaning/sentence/mnemonic/phrase` 完全一致，gozero 后端零改造切过来 | 兼容性最好，但继承 momo 的 JSON-in-text 反模式 |
| B2. 复刻 momo schema + 扩展字段 | 复刻基础上加 wordforge 已有但 momo 没有的字段（如 structure/derivatives/pos_sub 更规范化） | 后端需要少量适配 |
| B3. 重新设计（关系化 · 不含冗余 JSON 字段） | 借机修 momo 的 JSON-in-text 反模式 | 后端适配成本大，本次不建议 |

### 选项 C：ID 策略

| 选项 | 说明 | 评价 |
|---|---|---|
| C1. 保留 wordforge id（10 万级）· 新库里直接用（推荐默认） | 最简单，回写是个纯 ETL | 后端切 DB 要同时切 id 空间，客户端/缓存如果硬编码了 10 亿级 id 会坏 |
| C2. 重新分配 momo 风格 id（10 亿级）· 维护 wordforge_id ↔ mysql_id 映射表 | 对齐 momo 的 id 分布风格，后端"看起来像 momo" | 需要额外映射表，回写复杂度 ↑；meaning/sentence 同样要重分配 |
| C3. 延续 momo 已有 id · 只对 momo 里不存在的新词分配新 id | 最大程度复用 momo id，迁移平滑 | 需要 form+type 关联，wordforge 的 1.6k 缺词 vs momo 的 1.6k 多词要逐条比对 |

### 选项 D：同步模式

| 选项 | 说明 | 评价 |
|---|---|---|
| D1. 全量快照 · 每次 TRUNCATE + INSERT（推荐默认，MVP） | 简单、幂等 · 12 万行 30-60 秒完事 | 期间 MySQL 有短暂数据空窗 |
| D2. upsert 模式（ON DUPLICATE KEY UPDATE） | 可以增量、可以在线运行不断流 | 代码复杂 · 删除场景要特殊处理 |
| D3. CDC / 实时订阅 | PG logical replication → MySQL | 本次不必要，除非后端要求实时 |

### 选项 E：mnemonic 怎么回写

momo 有两张表：`mnemonic` (空) · `mnemonic_LLM` (11 万行)。wordforge 的 mnemonics 全是 LLM 产出。

| 选项 | 说明 |
|---|---|
| E1. 回写到新库的 `mnemonic_LLM`（推荐） | 跟 momo 约定一致 |
| E2. 回写到新库的 `mnemonic` | 把 LLM 产物当"人工"用，跟 momo 语义冲突 |
| E3. 两张表都建，但只写 mnemonic_LLM | 保留扩展性 |

### 选项 F：word 表内嵌的 JSON 字段（meanings / mnemonics / phrases）怎么处理

momo `word` 表里 `meanings` 是 `[{"id": 1020000001}, ...]` 这种 JSON 数组。

| 选项 | 说明 |
|---|---|
| F1. 回写时顺便填上（推荐） | 按关联关系构造 id 数组写入 · 保持 momo schema 语义 |
| F2. 留空 · 后端改走 JOIN | 需要后端配合改 |
| F3. 物化到一个 JSON 聚合字段（类似 wordforge 的 serving.word_payload） | 本次不在范围 |

---

## 推荐默认组合（等待你拍板）

- **A1** + **B1** + **C1** + **D1** + **E1** + **F1**
  - 同实例新建 database `word_forge`
  - Schema 1:1 复刻 momo
  - 沿用 wordforge 的 10 万级 id（不做 id 重分配 · 但你要清楚后端切过来时 id 空间会变）
  - 全量快照同步，可以跑一条命令刷新整库
  - mnemonic 回写到 `mnemonic_LLM`
  - word/meaning 表的内嵌 JSON 字段顺便填好

这组合的核心 trade-off：**后端从 momo 切到 word_forge 时，id 会从 10 亿级变成 10 万级**。
如果后端缓存 key / 客户端记录 / 日志分析 依赖 momo id 的数值特征，这会是个兼容性破坏点。
如果你接受"切 DB 的同时也切 id 空间"，C1 最省事。如果不能接受，就走 C3（延续 momo 已有 id + 新词补号）。

---

## 开放待定问题

1. 新 DB 里是否需要 `group` 字段？wordforge 当前在关系表没存，只在 serving.word_payload 的 meaning 里记了 user_group
2. gozero 后端读这个新库的 schema 事实源在哪？（飞书 wiki？现有 gozero service？）还是由我们新定义
3. 回写是一次性 bootstrap，还是需要定期同步？（决定脚本要不要做 append/upsert 模式）
4. 写完后如何验证？采样对比？全量 checksum？
5. 权限隔离：新 database 是否给一个独立账号（只写 word_forge 不碰 word）

---

## 接下来

等你对 A/B/C/D/E/F 每一项给个明确选择 · 或说"就按推荐默认走"，
我再把这份调研文档收口成 design spec，随后出 writing-plans 的实施计划。
