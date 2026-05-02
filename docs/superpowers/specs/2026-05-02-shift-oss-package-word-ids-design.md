# OSS package-words 批量 shift word_id — 设计文档

- **日期**: 2026-05-02
- **作者**: allen + Claude
- **目标**: 把阿里云 OSS bucket `sailing-words-package-words` 里 1443 个 package
  object 的 `words[].id` 从 10 亿级减去 `999_900_000` 变成 10 万级，对齐
  wordforge PG `domain.words.word_id` 的空间。单次任务。
- **背景**: PG 这侧 `domain.package_word.word_id` 已由 `mirror_momo_packages.py` 按
  `WORDFORGE_WORD_ID_SHIFT = 999_900_000` 减完；`words_core/scripts/migrate_two_packages/migrate.py`
  也确认新写入 OSS 时要减同一个 offset。但历史上 OSS 里的 1443 个 object 绝大多
  数是 momo 原始值未转换过（抽样证实 1440/1443 仍是 10 亿级，只有 `migrate.py`
  手跑过的 3 个 `1050000373 / 1050001179 / 1050000006` 已 shift）。

## 1. 范围与非目标

**要做**:
- 新增脚本 `scripts/packaging/shift_oss_package_word_ids.py`,从 OSS 逐个 object
  读出 → 校验 word_id 映射合法 → 减 offset → 覆盖原 key 写回。
- 处理前先全量 dump 到 `./bak/<package_id>.json` 做本地备份。
- 已 shift 过的 package 自动跳过（幂等）。
- 映射缺失的 id 写 dead-letter jsonl 供人工处理，不静默放行。

**不做**:
- 不建新 OSS bucket、不改 key 命名（object key 保持纯 `<package_id>`，无后缀）。
- 不改 JSON schema、不动 unit 顺序 / `id` / `title`、不改 `words[].weight`。
- 不建 PG 临时 schema / 物化表，`valid_word_ids` 纯内存 set。
- 不改 packaging pipeline、不改 `mirror_momo_packages.py`、不改 `migrate.py`。
- 不做并发 / 分片（1443 × ~40KB 小文件，单线程 5-10 分钟足够）。
- 不做 checkpoint：幂等靠"min(id) < 10^9 即已 shift"自然保证，挂了直接重跑。

## 2. 数据源与凭证

| 资源 | 位置 | 用途 |
|---|---|---|
| OSS bucket `sailing-words-package-words` | `oss-cn-hangzhou.aliyuncs.com` | 读写 1443 个 package object |
| OSS 凭证 | `~/.wordforge/oss.env`（chmod 600） | `OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET` |
| PG `wordforge.domain.words` | prod RDS（`~/.wordforge/prod.env`） | 只读 `SELECT word_id`，构建校验集合 |

脚本运行前需 `source ~/.wordforge/oss.env` + `source ~/.wordforge/prod.env`。

OSS 凭证来源：一次性从 `words_core/.env` 的 `WORDS_CORE_OSS_*` 同步过来，之后
`~/.wordforge/oss.env` 是本仓唯一事实源。

## 3. 数据现状（验证过）

- Bucket object 总数 = **1443**，和 `domain.package` 行数完全对齐。
- Key = 纯 `<package_id>`，无后缀。
- Body = JSON：
  ```json
  [
    {"id": <unit_id>, "title": "...", "words": [{"id": <word_id>, "weight": <int>}, ...]},
    ...
  ]
  ```
- 单文件 size ~20KB – 250KB，bucket 总量 ~60MB。
- word_id 空间：
  - 原始（10 亿级）：`[1_000_000_001, 1_000_121_863]`（1440 个 package）
  - 已 shift（10 万级）：`[100_003, 121_x]`（3 个 package — 373 / 1179 / 0006）

## 4. 映射规则与校验

**规则**：`new_id = old_id - 999_900_000`，`WORD_ID_OFFSET` 常量与
`mirror_momo_packages.py::WORDFORGE_WORD_ID_SHIFT` / `migrate.py::WORD_ID_OFFSET`
保持一致。脚本内复制常量，不引入新配置项。

**校验集合**：启动时 `SELECT word_id FROM domain.words` 拉进 `set[int]`（~121k
行，~1MB 内存）。对每个 package，把减完 offset 的 new_id 集合和这个 set 求差：

- 差为空 → 该 package 合法，进入写入阶段。
- **差非空但有合法 id**(**2026-05-02 dry-run 后决策**:方案 A 容忍缺失) → 把缺失 id 从
  `u["words"]` 里移除,保留合法 id,正常 shift + 上传;details 里记录
  `filtered_count / filtered_unique_missing` 便于审计。
  **动因**:wordforge ingest 做过 casefold 去重,momo 原始 122664 词被压到 121057,
  1607 个"洞"历史就在那里。OSS 保留这些 id 也没用,前端查后端会 404。
- 差覆盖全部 id(清空后 package 为空) → 仍然 `dead_letter`,不上传。
- 起初 word_id 已经 < 10^9 → 标记 `already_shifted`,不写(跳过即可,备份照做)。

## 5. 流程

```
stage 0: 加载 valid_new_ids = set(SELECT word_id FROM domain.words)
stage 1: List bucket → all_keys (~1443)
stage 2: 对每个 key 顺序处理（先无脑备份,再判定分支）：
    a. bucket.get_object(key).read()  → body
    b. 写 ./bak/<key>.json（文件已存在则 skip;全量 1443 个都备份,包括
       已 shift 的 3 个,保险优先）
    c. parsed = json.loads(body)
    d. ids = [w["id"] for u in parsed for w in u["words"]]
       if max(ids) < 10**9:  → already_shifted, continue
    e. new_ids = [i - WORD_ID_OFFSET for i in ids]
       missing = [n for n in new_ids if n not in valid_new_ids]
       if missing: → dead_letter, continue
    f. 就地改 parsed:  for u in parsed: for w in u["words"]: w["id"] -= WORD_ID_OFFSET
    g. new_body = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    h. 仅在 --i-am-writing-prod 时 bucket.put_object(key, new_body)；
       否则 dry-run 仅记日志。
stage 3: 打印汇总 ok / already_shifted / dead_letter 计数；
         建议 post-check: 抽 5 个随机 package 确认 max(id) < 10**7。
```

**字节保真标准**（已在 brainstorm 阶段与 allen 对齐）：不追求 byte-for-byte，
只追求语义等价（字段齐、unit/word 顺序不变、weight 不动、字符集正确）。统一
使用 `ensure_ascii=False, separators=(",", ":")` 输出 compact JSON 即可。

## 6. CLI

```
uv run python scripts/packaging/shift_oss_package_word_ids.py \
    [--bak-dir ./bak/] \
    [--dead-letter ./oss_shift_dead_letter.jsonl] \
    [--i-am-writing-prod]
```

- 默认行为 = dry-run：**不加** `--i-am-writing-prod` 时完整做备份、校验、transform，
  只是跳过 `put_object`。无需单独 `--dry-run` flag。
- Guard：和 `mirror_momo_packages.py::--i-am-mirroring-prod` 同风格，显式写入
  prod 的动作必须带 flag。
- 日志：进度到 stdout（每处理 100 个打一次带时间戳的进度条），致命错误 stderr。

## 7. 错误处理

- **PG 连接失败**：stage 0 直接 `sys.exit(1)`，带清晰报错。
- **OSS 列 bucket 失败 / 凭证错误**：stage 1 直接退出。
- **单个 object 下载失败**：用 `oss2` 默认 retry；连续 3 次失败把该 key 写到 dead-letter
  并继续下一个，不让整批挂。
- **单个 object 上传失败**：同上 retry，失败写 dead-letter。
- **禁止裸 except**：遵循本仓硬规矩，只捕具体异常（`oss2.exceptions.OssError` 等）。
- **中断**：`KeyboardInterrupt` 正常退出，日志打印已处理多少个；重跑靠 `already_shifted`
  幂等。

## 8. 测试策略

**单元测试** `tests/packaging/test_shift_oss_package_word_ids.py`：

- `transform_body(raw_json_text, valid_new_ids) -> (new_body | None, status, details)`：
  - 输入全部合法 10 亿级 id → status=`ok`，new_body 里所有 id 都是 10 万级
  - 输入部分 id 不在 valid set → status=`dead_letter`，details 含 missing_new_ids
  - 输入 id 已是 10 万级 → status=`already_shifted`，new_body=None
  - 输入混合 10 亿 + 10 万级（理论不该出现，但要有防御）→ status=`dead_letter` 或显式 raise（二选一，spec 定为 raise，因为这代表数据异常）
- 不 mock 真实 OSS / PG；上述函数是纯函数，便于断言。

**手工验证流程**：

1. `source ~/.wordforge/{oss,prod}.env`
2. dry-run 扫全量，期望 summary：`ok=1440 / already_shifted=3 / dead_letter=0`
3. 抽查 `./bak/` 应有 **1443** 个原始备份文件（先无脑全量下载 + 备份,再做
   already_shifted 判定,保险优先）。
4. `--i-am-writing-prod` 正式跑
5. post-check：`bucket.get_object(random.choice(keys))` 抽 5 个 verify `max(id) < 10**7`
6. 再跑一次 dry-run，期望 `ok=0 / already_shifted=1443 / dead_letter=0`（幂等验证）

## 9. 文件 / 目录约定

- `./bak/` — 全量原始备份（脚本执行目录下），`.gitignore` 排除
- `./oss_shift_dead_letter.jsonl` — 失败条目，`.gitignore` 排除
- 凭证不进仓

## 10. 风险与权衡

| 风险 | 评估 | 缓解 |
|---|---|---|
| 误处理已 shift 的 3 个 package | 高 | 运行时 `max(id) < 10^9` 判定 + 单测覆盖 |
| OSS 写入失败只能单个重试 | 低 | dead-letter + 人工重跑幂等 |
| `domain.words` 和 OSS 不同步（近期有 word 被删） | 低 | dead-letter 捕获，人工决定是否补 |
| 网络中断 | 低 | 已处理的 key 下次 `already_shifted` 跳过 |
| JSON 格式细节漂移（前端如果真 byte-敏感） | 低 | 前端仅 `json.Unmarshal`，不敏感 |

## 11. 非目标再次强调

- 不加一次性脚本到 packaging pipeline（`scripts/packaging/packager.py` 不 import 本脚本）
- 不写 README 大段说明（脚本顶部 docstring 足够）
- 不做并发；不优化；跑完即弃
