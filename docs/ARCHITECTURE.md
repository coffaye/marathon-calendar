# Architecture

## Phase 1 技术判断

本项目选择 **FastAPI + SQLite + Python 标准库 ICS 生成器**。

原因：

1. 数据采集天然适合 Python，后续可直接复用 `httpx`/调度器；
2. FastAPI 能同时提供健康检查、管理/读取 API 和 `text/calendar` 响应，不需要复杂前端；
3. SQLite 零配置、可备份、足够支撑原型及小规模服务；
4. Repository 接口隔离存储实现，Phase 2/生产部署可换 PostgreSQL，不让业务层依赖 SQLAlchemy 或某个云服务；
5. ICS 生成器自己控制 UID、换行、转义、全天事件和更新语义，避免把客户端兼容性寄托在第三方库默认行为上。

## 分层

```text
Source adapters / snapshots
          |
          v
  Race + RaceSource domain model
          |
          v
 SQLite RaceStore (repository)
          |
          +--> JSON API
          +--> ICS generator --> /calendar/*.ics
          +--> SyncRun / RaceChange / reconciliation issue
          +--> Coverage audit/report (annual catalog vs API and global sources)
          +--> Realtime semantics --> confirmed / planned feeds
```

数据源适配器只负责获取和映射，不直接决定是否覆盖 canonical Race。Phase 2 的同步服务先按 `(source_name, external_id)` 命中，再按不含日期的 canonical identity 辅助匹配；多个候选时写入 reconciliation issue 并停止该条记录的自动合并。权威来源与人工 override 决定 canonical Race 的有效字段，来源原始/归一化数据始终独立保存。

Phase 3 的 AIMS/World Athletics 适配器只读取公开的 ICS/HTML；World Athletics 不调用未文档化的内部 API，也不绕过反爬或登录边界。国际同步保留 source UID/数字 ID、raw payload、归一化字段和 snapshot checksum。跨源匹配要求稳定 ID、名称/别名或中英文城市嵌入等身份证据与距离/日期约束的组合；同城日期巧合不足以合并。无法确认具体比赛日的记录进入 `unresolved_source_records`。

## 更新语义

`Race.id` 是内部稳定 UUID；UID 为：

```text
race_<race.id>@marathon-calendar
```

日期、状态、时间、地点等日历字段变化时：

1. 保持 `id` 和 UID 不变；
2. `race_date` / `status` 等字段更新；
3. `sequence += 1`；
4. 更新 `last_modified`；
5. ICS 输出新的 `DTSTART/DTEND/LAST-MODIFIED/SEQUENCE`。

这样 Outlook、Apple Calendar 和其他订阅客户端有机会将其识别为原事件的更新，而不是新事件。

## Phase 1 API

- `GET /healthz`
- `GET /races`
- `GET /calendar/all.ics`
- `GET /calendar/china.ics`
- `GET /calendar/china-planned.ics`
- `GET /calendar/full-marathon.ics`
- `GET /calendar/world.ics`
- `GET /calendar/international.ics`

没有用户系统和写入 API。Demo 数据通过启动 seed 写入，后续采集器/管理 API 再补充。

## Phase 2 API and operations

- `GET /races/{race_id}/sources`
- `GET /races/{race_id}/changes`
- `GET /sync-runs`
- `GET /sync-runs/{run_id}`
- `python -m marathon_calendar sync china [--year] [--dry-run]`

同步 CLI 是受控写入入口；API 不提供公开同步 POST。原始列表/详情快照按来源和 UTC 日期保存，默认保留 30 天。

国际同步命令为 `python -m marathon_calendar sync aims|world-athletics|international [--year] [--document]`；`audit global-reconciliation --year` 输出来源分布、跨源交集、重复/赛事家族候选、未决记录和差异。国际来源的 `international_calendar` 权重低于中国官方当前来源；World Athletics federation label 高于 AIMS calendar，但两者都不覆盖人工 override。

## Phase 2.5 年度目录管道

年度目录路径为：`official PDF -> normalized sidecar -> china_annual_catalog adapter -> conservative audit -> RaceSource -> canonical Race`。目录 adapter 不直接执行 SQL，也不直接覆盖 API/组委会有效字段。优先级为人工覆盖 > 组委会未来来源 > `china_official` API > `china_annual_catalog`；目录只作为计划基线。命令 `audit china-coverage` 是只读身份审计并生成 JSON/Markdown 报告，`sync china-catalog` 才执行受控本地导入。

## Phase 2.6 实时性语义

`RaceSource.source_role`、`publishable`、authority 和 freshness 共同决定 Feed 发布；`planning_catalog` 永远不能单独产生 `confirmed`。`/calendar/china.ics` 只包含近期可发布的当前来源，`/calendar/china-planned.ics` 只包含没有当前确认来源的年度计划赛事，并显式标记“（计划）”。`last_fetched_at`/`retrieved_at` 与 `last_confirmed_at` 分离，重新读取旧 PDF 不会刷新确认时间。

## Phase 4 部署方向

正式 V1 使用 GitHub-native serverless 发布：GitHub Actions 在临时 runner 中恢复版本化 `state/` 到 SQLite，按 China → AIMS → World Athletics 顺序同步、审计并生成静态 `site/`，再用 GitHub Pages 官方 Actions 发布 HTTPS 订阅。FastAPI 保留用于本地开发、调试、测试和未来 API 部署，但 Pages 不运行 Python/FastAPI，也不发布仓库根目录。

生产环境的持久身份在 `state/` JSON，而不是 runner DB、Actions cache 或 artifact。每次发布前必须通过测试、独立 iCalendar parser、state round-trip、hard duplicate/ambiguous gate；源失败时 fail closed，保留上一版 Pages。具体流程见 [GITHUB_DEPLOYMENT.md](GITHUB_DEPLOYMENT.md)、[STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) 和 [PRODUCTION_OPERATIONS.md](PRODUCTION_OPERATIONS.md)。
