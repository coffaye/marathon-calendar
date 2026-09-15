# Data Model

## Race

`Race` 是内部 canonical event，不是某个网站的一行数据。

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | 稳定内部身份；不包含日期 |
| `name` / `name_en` | string / nullable string | 显示名称及英文名 |
| `year` | integer | 赛事年份 |
| `country` | string | ISO 3166-1 alpha-3 preferred, e.g. `CHN` |
| `province` / `city` | nullable string | 结构化地点 |
| `race_date` | date | 明确日期，不用字符串 |
| `start_time` | nullable time | 未知时为空，不猜测 |
| `timezone` | nullable IANA timezone | e.g. `Asia/Shanghai`; 国际来源未知时保持 `null` |
| `distance_types` | string[] | e.g. `全程`, `半程`, `10K` |
| `race_type` | enum-like string | Phase 1 默认 `road_race` |
| `organization` | nullable string | 主办/承办单位 |
| `association_level` | nullable string | 中国田协 A/B/C 等 |
| `world_athletics_label` | nullable string | WA label |
| `status` | enum | scheduled, registration_open, registration_closed, postponed, cancelled, completed, date_tentative |
| `verification_status` | enum | confirmed, planned, stale, needs_verification, cancelled |
| `registration_start/end` | nullable date | 报名窗口 |
| `lottery_result_date` | nullable date | 抽签结果日期 |
| `official_url` | nullable URL | 官方链接 |
| `latitude/longitude` | nullable float | 不猜测坐标 |
| `canonical_identity_key` | string | 与日期无关的去重键 |
| `sequence` | non-negative integer | ICS 更新序号 |
| `created_at/updated_at/last_verified_at/last_modified` | datetime | UTC 存储 |
| `last_confirmed_at` | nullable datetime | 最近一次由可靠当前来源确认的时间；年度目录不会设置 |
| `confirmed_by_source_id` | nullable UUID | 最近一次确认所用的 Source Record |
| `merged_into_id` / `merged_at` / `merge_reason` | nullable UUID/datetime/string | 软合并审计字段；不删除 loser Race |

## RaceSource

一个 `Race` 可以有多个 `RaceSource`：

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Source Record 身份 |
| `race_id` | UUID | 外键到 Race |
| `source_type` | string | official_association, race_organizer, aims, world_athletics, third_party, fixture |
| `source_name` | string | 人类可读来源名 |
| `source_url` | URL | 页面或 API URL |
| `external_id` | nullable string | 如 CAA `raceId`、WA 数字 ID、AIMS UID |
| `source_race_name` | string | 原始名称 |
| `source_race_date` | nullable date | 原始日期，允许与 canonical 日期不一致 |
| `raw_data` | JSON | 最小原始响应或摘要 |
| `source_hash` | string | 规范化来源响应 hash，用于判断列表/详情变化 |
| `source_year` | integer | 该来源记录的赛事年份 |
| `source_document` | nullable URL/string | 官方出版物或附件 URL |
| `source_document_checksum` | nullable string | 文档 SHA-256 |
| `source_row_number` | nullable integer | 文档中的审计行号，不参与 source external ID |
| `source_publication_date` | nullable date | 官方出版物发布日期 |
| `source_role` | enum | live_official, official_organizer, planning_catalog, international_federation, secondary |
| `publishable` | boolean | 是否允许来源进入正式 Feed；规划目录固定为 false |
| `published_at` | nullable date | 来源/文档发布日 |
| `retrieved_at` | nullable datetime | 本次证据读取时间；不等于确认时间 |
| `last_confirmed_at` | nullable datetime | 该来源最近一次确认赛事事实的时间 |
| `normalized_data` | JSON | 来源归一化后的字段；保留 `source_category_text` 等原始语义 |
| `source_distance_text` | string[] | 原始项目文本，不被归一化覆盖 |
| `last_seen_at` | datetime | 最近一次完整列表中出现时间 |
| `missing_count` / `flag_for_review` | integer / boolean | 连续完整同步缺失计数和人工复核标记 |
| `fetched_at/verified_at` | datetime | 记录抓取和核验时间；年度目录文档原始 `retrieved_at` 与提取方法也保留在 `normalized_data`/`raw_data.document` |
| `confidence` | float 0..1 | 关联置信度 |
| `is_authoritative` | boolean | 允许人工覆盖；同一 Race 最多一个 |
| `match_reason` | nullable string | 为什么判断为同一赛事 |

## RaceAlias, unresolved source and discrepancy

`RaceAlias` 保存来源原名、语言、规范名和关联 Source Record，使跨语言名称匹配可解释且可重放。`UnresolvedSourceRecord` 保存 TBC、真正的多日范围、未知国家或其他无法安全落成 canonical 比赛日的原始证据；它不创建带猜测日期的 Race。`SourceDiscrepancy` 保存同一 Race 的不同来源在日期、名称、地点、距离、状态或 Label 上的冲突；低权威来源产生差异记录，但不会因差异单独递增 ICS `SEQUENCE`。

## Identity resolution

Phase 1 使用可解释规则：去除年份、标点、赛事后缀，统一中英文已知城市别名，再结合国家、城市和赛事名称主体生成 SHA-256 key。日期不进入 key，因此改期不产生新身份。Phase 2.5 的目录/API 审计进一步要求省份、项目重叠、名称相等或高相似度和日期/计划月份证据；Phase 3 再要求跨源名称/别名或城市语义证据与距离/日期约束组合。候选接近时标记 `AMBIGUOUS`，绝不靠同城日期自动合并。

例如：

```text
2026南昌马拉松
南昌马拉松
2026 Nanchang Marathon
```

会归一化为同一 canonical identity。规则不是最终真相：Phase 4 应加入人工合并/拆分、外部 ID 优先、别名表和可审计决策记录。

## RaceFieldOverride

人工覆盖允许字段为 `race_date`、`name`、`status`、`city`、`province`、`official_url`。覆盖只改变 canonical Race 的有效值，不覆盖来源原始/归一化数据；来源后续变化仍通过 `RaceChange(scope=race_source)` 保存。

## RaceChange

每条字段变更保存旧值、新值、来源、原因、时间和关联 `SyncRun`。`scope=race_source` 表示来源记录变化，`scope=race` 表示会影响日历输出的 canonical 变化。只有后者的日历可见字段会递增 `Race.sequence`。

## SyncRun and ReconciliationIssue

`SyncRun` 保存一次同步的状态、分页数量、详情结果、质量计数、结构化错误和快照路径。`ReconciliationIssue` 保存 `AMBIGUOUS` 的来源 ID、候选 Race UUID 和待处理状态；系统不会在候选多于一个时自动合并。
