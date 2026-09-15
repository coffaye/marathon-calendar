# Marathon Calendar / 马拉松赛事订阅日历

一个面向中国及国际马拉松赛事的持续更新日历服务。项目不是一次性下载 `.ics` 文件，而是将赛事与多个来源关联、保留稳定 UID，并通过长期订阅地址输出 iCalendar。

当前状态：**Phase 4 GitHub-native 静态发布包已完成**。当前仓库没有 GitHub remote/Pages 实例，因此 `GITHUB_DEPLOYMENT = BLOCKED_BY_INFRASTRUCTURE`；Outlook 实际客户端验证为 `REQUIRES_HUMAN`。

## 技术栈

- Python 3.11+
- FastAPI + Uvicorn：提供健康检查、赛事 JSON 和 ICS 订阅接口
- SQLite：Phase 1 的零运维持久化存储；Repository 层隔离了未来 PostgreSQL 迁移
- Python 标准库 `sqlite3`、`zoneinfo`、`datetime`：减少依赖，方便小型服务部署

选择理由与边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn marathon_calendar.main:app --reload
```

开发模式启动时会在空库中写入少量明确标记为 `fixture` 的 Demo 赛事。GitHub Actions 使用 `state/` 恢复 canonical state 到临时 SQLite，再同步和生成 `site/`；生产模式绝不写入 Demo fixtures。FastAPI 保留用于本地开发、调试和测试，GitHub Pages 只发布 `site/`。

## 访问地址

```text
http://127.0.0.1:8000/healthz
http://127.0.0.1:8000/livez
http://127.0.0.1:8000/readyz
http://127.0.0.1:8000/
http://127.0.0.1:8000/races
http://127.0.0.1:8000/calendar/all.ics
http://127.0.0.1:8000/calendar/china.ics
http://127.0.0.1:8000/calendar/china-planned.ics
http://127.0.0.1:8000/calendar/full-marathon.ics
http://127.0.0.1:8000/calendar/world.ics
http://127.0.0.1:8000/calendar/international.ics
```

本地静态站点导出：

```powershell
$env:PYTHONPATH = "src"
python -m marathon_calendar state import --state state --db tmp/runtime.db
python -m marathon_calendar export-site --db tmp/runtime.db --output site --base-url "http://localhost:8000"
```

GitHub Pages 订阅地址应是部署 metadata 对应的 HTTPS 项目地址，例如：

```text
https://<username>.github.io/<repo>/calendar/world.ics
```

请选择新版 Outlook 的 “Subscribe from web / 从 Web 订阅”，不要只下载后 Import；只有订阅才能继续获得改期更新。静态站点还提供 `calendar/test-subscription.ics`，用于 V1→V2 同 UID 更新演练，不进入 world feed。

## 测试与 Demo ICS

```powershell
pytest
python -m marathon_calendar state export --db data/phase3_final.db --output state
python -m marathon_calendar state import --state state --db tmp/runtime.db
python -m marathon_calendar export-site --db tmp/runtime.db --output site
python -m marathon_calendar test-feed --version 2
python scripts/validate_ics.py site/calendar/world.ics
python scripts/generate_demo_ics.py
python -m marathon_calendar sync china --year 2026 --dry-run
python -m marathon_calendar sync china --year 2026 --details-limit 20
python -m marathon_calendar audit china-coverage --year 2026
python -m marathon_calendar sync china-catalog --year 2026 --dry-run
python -m marathon_calendar sync china-catalog --year 2026
python -m marathon_calendar audit china-realtime --year 2026
python -m marathon_calendar sync aims --year 2026 --document data/snapshots/phase3-research/aims-events-2026.ics
python -m marathon_calendar sync world-athletics --year 2026 --document data/snapshots/phase3-research/world-athletics-label-road-races-2026.html
python -m marathon_calendar sync international --year 2026 --aims-document data/snapshots/phase3-research/aims-events-2026.ics --world-athletics-document data/snapshots/phase3-research/world-athletics-label-road-races-2026.html
python -m marathon_calendar audit global-reconciliation --year 2026
python -m marathon_calendar runs
```

`sync china` 默认使用官方列表接口分页抓取；`--dry-run` 只写 `SyncRun` 和原始快照，不写 `Race`、`RaceSource`、`RaceChange`。详情请求可用 `--details-limit` 控制，0 表示抓取所有需要详情的记录。运行手册见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

测试覆盖 ICS 合法结构、全天事件的 exclusive `DTEND`、文本转义、中文、稳定 UID、延期/取消、`SEQUENCE` 更新、多个来源、去重身份规则、HTTP 重试、幂等同步、人工覆盖、缺失阈值、dry-run 隔离、production 无 fixtures、SQLite WAL/busy timeout、state round-trip 和静态导出语义复用。GitHub Actions 另用独立 `icalendar` parser 做 CI gate。

## 目录

```text
.
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CHINA_COVERAGE.md
│   ├── DATA_MODEL.md
│   ├── DATA_SOURCES.md
│   ├── ICS_BEHAVIOR.md
│   ├── OPERATIONS.md
│   ├── ROADMAP.md
│   ├── PUBLICATION_RULES.md
│   ├── INTERNATIONAL_SOURCES.md
│   ├── GLOBAL_RECONCILIATION.md
│   ├── GITHUB_DEPLOYMENT.md
│   ├── STATE_MANAGEMENT.md
│   ├── OUTLOOK_VALIDATION.md
│   └── PRODUCTION_OPERATIONS.md
├── state/
│   ├── canonical_races.json
│   ├── source_links.json
│   ├── aliases.json
│   ├── manual_overrides.json
│   ├── reconciliation.json
│   ├── history.json
│   └── metadata.json
├── site/                 # generated Pages artifact; ignored by Git
├── .github/workflows/update-calendar.yml
├── src/marathon_calendar/
│   ├── domain/models.py
│   ├── identity.py
│   ├── ics.py
│   ├── migrations.py
│   ├── repository.py
│   ├── snapshots.py
│   ├── catalog_sync.py
│   ├── authority.py
│   ├── coverage.py
│   ├── realtime.py
│   ├── international_sync.py
│   ├── global_reconciliation.py
│   ├── publication.py
│   ├── export.py
│   ├── state.py
│   ├── test_feed.py
│   ├── sync.py
│   ├── sources/
│   ├── seed.py
│   └── main.py
├── scripts/
│   ├── generate_demo_ics.py
│   └── validate_ics.py
├── tests/
│   ├── fixtures/
│   ├── test_api.py
│   ├── test_dedup.py
│   ├── test_ics.py
│   ├── test_models.py
│   └── test_phase3_international.py
└── pyproject.toml
```

## 重要设计约束

- `Race` 与 `RaceSource` 分离；一个 Race 可以有多个 Source Record。
- 来源权威性可人工覆盖，系统不把优先级硬编码成不可修改的规则。
- Race 的 UID 只由稳定内部 ID 生成，不包含日期；改期只改变日期、`LAST-MODIFIED` 和 `SEQUENCE`。
- Phase 1 的赛事均为全天事件；`DTEND` 是 exclusive。
- 未知开赛时间保持为空，不猜测 07:30 或 08:00。
- 取消赛事保留在数据库和 ICS 中，不删除。
- 中国官方 `raceId` 只写入 `RaceSource(source_name, external_id)`；canonical `Race.id` 始终是内部 UUID。
- 来源原始响应、归一化字段、来源 hash、同步运行、来源变更和身份歧义均可审计。
- 官方来源暂不通过一次缺失直接取消赛事；连续 3 次完整成功列表同步后才标记来源待复核。
- 人工覆盖字段（日期、名称、状态、城市、省份、官方 URL）优先于来源值；来源变化仍写入来源级变更历史。

## Phase 2 边界

中国官方站点的列表/详情接口已按当前前端实际调用方式适配，并完成真实 dry-run。接口属于前端使用的生产接口，未发现公开 SLA、稳定版本或可独立确认的第三方使用条款；同步器因此默认低频、有限重试、不携带 Cookie/Token，也不绕过站点反爬边界。详情见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) 与 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

Phase 2 不把中国田协年度目录数字当成 API 列表结果的硬性断言：2026 年官方目录公告为 492 场，而本次 API 分页返回并按 `raceTime` 本地筛出的记录为 285 条，两者口径不同，均保留证据。

## Phase 2.5 边界与真实结果

已保存并核验中国田协 2026 年官方年度目录 PDF 及其规范化解析副本：目录 492 行，PDF SHA-256 为 `52633a1251d85324eb269872fdcfca3e3682aa8902f7c9565fb08384ec05128a`。目录通过独立的 `china_annual_catalog` source adapter 进入 `RaceSource`，不直接更新 `Race`；与 API 的身份匹配保留方法、分数和证据。

真实审计结果与差异解释见 [docs/CHINA_COVERAGE.md](docs/CHINA_COVERAGE.md) 及 [reports/china_coverage_2026.md](reports/china_coverage_2026.md)。

## Phase 2.6 实时性语义

年度目录现在明确是 `planning_catalog`：它保留历史计划日期和发现价值，但不会单独进入正式 `/calendar/china.ics`，也不会把抓取时间当成确认时间。正式中国 Feed 仅发布近期可验证的 live/organizer 等来源；未确认计划单独发布到 `/calendar/china-planned.ics`，并使用“（计划）”标记。当前实时性分类见 [reports/china_realtime_2026.md](reports/china_realtime_2026.md)。

## Phase 3 国际来源与全球对账

AIMS 的公开 `events.ics` 和 World Athletics Label Road Races 官方公开页面均有真实快照、checksum 和解析证据。AIMS 的稳定 UID、状态、`DTSTART/DTEND`、`SEQUENCE` 和 `LAST-MODIFIED` 被保留；World Athletics 使用页面公开的数值赛事 ID、日期、城市、国家和 Label 字段。TBC、多日范围和未知国家不会猜测成可发布日期，而是进入 unresolved Source Record。

国际同步使用保守身份匹配：先按来源稳定 ID，再结合国家、名称/别名、城市语义、距离和日期；单凭“同城同日”不自动合并。中国官方 `Race.id`/ICS UID 不被重建。主 Feed 只发布全马/半马，10K 等其他项目保留来源记录但不进入主国际 Feed。详见 [docs/INTERNATIONAL_SOURCES.md](docs/INTERNATIONAL_SOURCES.md)、[docs/PUBLICATION_RULES.md](docs/PUBLICATION_RULES.md) 和 [docs/GLOBAL_RECONCILIATION.md](docs/GLOBAL_RECONCILIATION.md)。
