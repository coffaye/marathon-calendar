# Operations / 同步运行手册

## 1. 当前中国官方适配器

适配器名称是 `china_official`，使用中国马拉松官方网站前端当前实际调用的两个接口：

- 列表：`POST https://api-changzheng.chinaath.com/changzheng-content-center-api/api/homePage/official/searchCompetitionMls`
- 详情：`POST https://api-changzheng.chinaath.com/changzheng-content-center-api/api/homePage/official/searchById`

列表请求固定包含 `provinceId`、`cityId`、`districtId`、`raceName`、`raceGrade`、`raceStartTime`、`pageNo` 和 `pageSize`。当前接口对年份字符串筛选返回空结果，因此适配器分页抓取后在本地按 `raceTime` 年份过滤。默认 `pageSize=100`，不会无限分页。

详情请求使用 `{id, pageTitleLevelTwo, type}`，其中 `type=SS`。列表的 `raceId` 是来源 ID，写入唯一键 `(source_name, external_id)`，不直接作为 canonical `Race.id`。

## 2. 执行命令

先使用隔离数据库做真实 dry-run：

```powershell
python -m marathon_calendar sync china --year 2026 --dry-run `
  --db data/phase2_dry_run.db --snapshot-root data/snapshots
```

确认 `status=success`、`errors=[]` 和质量计数后，再执行写入：

```powershell
python -m marathon_calendar sync china --year 2026 `
  --db data/marathon_calendar.db --snapshot-root data/snapshots
```

开发或小流量核验可限制详情请求：

```powershell
python -m marathon_calendar sync china --year 2026 --details-limit 20
```

`--details-limit 0` 表示所有需要详情的记录；`--page-size` 应保持在已实测且合理的范围。同步器的 HTTP 客户端默认超时 15 秒，最多 3 次重试；只对网络错误、429 和 5xx 重试，4xx、业务错误、JSON/schema 错误不盲目重试。重试使用短指数退避，详情之间默认间隔 0.1 秒。

## 3. 运行结果判读

每次同步写入 `SyncRun`，并可通过以下接口查看：

```text
GET /sync-runs
GET /sync-runs/{run_id}
```

核心计数包括：列表总量与本地筛选量、详情成功/失败、归一化、MATCHED/NEW/AMBIGUOUS、质量问题、created/updated/unchanged、missing 以及结构化 errors。

- `success`：列表完整成功，且没有详情失败或其他错误。
- `partial_success`：列表成功，但存在详情失败或记录级错误；已成功解析的记录仍可落库。
- `failed`：列表/协议级失败，通常不应改变 canonical 数据；需要查看 `errors` 和原始日志。
- `dry_run=true`：只保存 `SyncRun` 与快照；不写 `Race`、`RaceSource`、`RaceChange`。

任何 `AMBIGUOUS` 记录都会创建待处理 `reconciliation_issue`，不会自动合并。人工处理前不要强行复用某个 `Race.id`。

## 4. 快照与保留

快照默认位于：

```text
data/snapshots/<source>/<UTC-date>/list.json
data/snapshots/<source>/<UTC-date>/manifest.json
data/snapshots/<source>/<UTC-date>/details.ndjson
```

`list.json` 保留分页 API 响应，`details.ndjson` 保留成功详情响应，manifest 记录请求、页数、数量和观察时间。`SnapshotStore` 默认清理 30 天以前的日期目录；清理只作用于配置的 snapshot root，不删除数据库。

## 5. 缺失记录处理

只有在列表分页完整成功后，才会将该年份本次未出现的来源记录 `missing_count + 1`。单次缺失不删除、不自动标记取消、不改变 canonical Race。达到默认 3 次完整成功同步后，`RaceSource.flag_for_review=true`，等待人工复核。

## 6. 人工覆盖

可覆盖字段为 `race_date`、`name`、`status`、`city`、`province`、`official_url`。命令示例：

```powershell
python -m marathon_calendar override set <race-uuid> race_date 2026-12-20 --reason "组委会公告"
python -m marathon_calendar override set <race-uuid> status postponed --reason "官方公告"
python -m marathon_calendar override clear <race-uuid> race_date
```

覆盖值在下次来源同步时优先应用。来源变化仍会写入 `RaceChange(scope=race_source)`；日历有效值变化写入 `RaceChange(scope=race)`，并驱动稳定 UID 的 `SEQUENCE` 更新。清除覆盖后，下一次同步会重新采用当前来源值。

## 7. 合规和边界

Phase 2 实际验证了接口方法、payload、分页、错误 envelope 和 HTTP 行为，但未从公开页面独立确认第三方调用 SLA、稳定版本或完整 ToS。对 `runchina.org.cn/robots.txt`、`/terms`、`/privacy` 的访问触发了站点/EdgeOne 反爬边界，未保存 Cookie、未绕过验证。默认策略是低频、有限并发/串行请求、短缓存、无 Cookie/Token、不绕过 anti-bot；若部署前得到更严格条款，应以条款为准并暂停不符合的抓取。

## 8. 备份和故障恢复建议

SQLite 生产环境使用持久卷，并在同步前后备份数据库。同步快照与数据库应一起保存，至少保留最近 30 天快照和更长周期的数据库备份，以便从 `SyncRun`、`RaceChange` 和原始响应重放/解释一次更新。

## 9. Phase 2.5 中国年度目录审计与导入

先验证官方 PDF 和同名 sidecar：

```powershell
python -m marathon_calendar audit china-coverage --year 2026 `
  --document data/source_documents/china_annual_catalog_2026.pdf `
  --db data/phase2_final.db --report-dir reports
```

确认 JSON/Markdown 报告中的目录结果总数等于 492、`AMBIGUOUS` 已逐条处理、API-only 已分析，再使用隔离数据库 dry-run：

```powershell
python -m marathon_calendar sync china-catalog --year 2026 --dry-run `
  --document data/source_documents/china_annual_catalog_2026.pdf `
  --db data/phase25_dry_run.db --report-dir reports
```

最后执行本地导入：

```powershell
python -m marathon_calendar sync china-catalog --year 2026 `
  --document data/source_documents/china_annual_catalog_2026.pdf `
  --db data/phase25_final.db --report-dir reports
```

精确日期的目录-only 记录会创建 `Race(status=date_tentative)` 并附加目录 `RaceSource`；月-only/范围日期不创建伪日期，直到 API 或人工来源提供具体日。API 匹配的目录 source 不具备覆盖权；API 后续出现时会优先复用已有目录 Race，人工 override 仍最高。重复运行应保持 source 数量、Race.id、ICS UID 和 `RaceChange` 不变。

## 10. Phase 2.6 实时性与 Feed

年度目录的 `source_role=planning_catalog`、`publishable=false` 是固定语义。正式中国 Feed：

```text
/calendar/china.ics
X-WR-CALNAME:中国马拉松赛事日历
```

只发布近期且可验证的 `live_official`、`official_organizer` 或其他明确 publishable 来源；规划目录不能单独入选。规划 Feed：

```text
/calendar/china-planned.ics
X-WR-CALNAME:中国马拉松年度规划（含未确认赛事）
```

会使用“🟡（计划）”标记，并在描述中写明“年度计划，尚未通过当前赛事平台或组委会再次确认”。

实时分类可通过以下命令生成：

```powershell
python -m marathon_calendar audit china-realtime --year 2026 `
  --db data/phase25_final.db --report-dir reports
```

`retrieved_at`/`fetched_at` 只说明证据被读取，`last_confirmed_at` 只由可靠当前来源设置；重新读取年度 PDF 不会把目录赛事变成 confirmed。没有可信总量 denominator 时，Live Calendar Coverage 只报告 live/future/planned 数量并明确 denominator unknown。

## 11. Phase 3 国际同步与全球审计

先把官方原始证据保存为快照，再在前向迁移的验证数据库中回放：

```powershell
python -m marathon_calendar sync aims --year 2026 `
  --document data/snapshots/phase3-research/aims-events-2026.ics `
  --db data/phase3_validation.db --snapshot-root data/snapshots

python -m marathon_calendar sync world-athletics --year 2026 `
  --document data/snapshots/phase3-research/world-athletics-label-road-races-2026.html `
  --db data/phase3_validation.db --snapshot-root data/snapshots

python -m marathon_calendar audit global-reconciliation --year 2026 `
  --db data/phase3_validation.db --report-dir reports
```

也可用 `sync international` 连续导入两类来源。真实网络抓取使用各适配器默认的公开 URL；不带 `--document` 时应低频执行并保留返回快照。第二次回放相同快照应保持 Source Record、Race.id、ICS UID 和 `SEQUENCE` 不变；国际来源更新只有在其权威等级足以改变 canonical 日历字段时才递增序号。AIMS 的 `DTEND` 按 RFC 5545 作为排他结束日解析；多日事件、TBC 和未知国家进入 unresolved，不进入带伪日期的主 Feed。

发布检查：`/calendar/china.ics` 仍由当前可确认中国来源控制；年度目录只能进入 `/calendar/china-planned.ics`。`/calendar/world.ics` 发布所有国家的全马/半马主赛事，`/calendar/international.ics` 排除中国；10K 等其他距离保留来源证据但不进入这两个主国际 Feed。运行 `audit global-reconciliation`，确认 duplicate clusters 和 ambiguous open issues 为 0，再将报告与数据库/快照一起归档。
