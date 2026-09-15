# Roadmap

## Phase 1 — completed

- 基础 Python/FastAPI/SQLite 结构
- Race + RaceSource 数据模型
- 可解释 canonical identity 规则
- 中国官方接口、AIMS ICS、World Athletics 页面和 NowRun 实际核验
- `/calendar/all.ics`、中国和全程筛选 Demo endpoint
- 稳定 UID、全天事件、更新 `SEQUENCE` 测试

## Phase 2 — China ingestion — completed

- 中国官方列表/详情已封装为低频、有限重试 adapter；年份在本地过滤
- `SyncRun`、分页原始快照、详情 ndjson、source hash 和字段归一化已落地
- `RaceSource(source_name, external_id)` 保存官方 `raceId`，与 canonical `Race.id` 分离
- 人工覆盖日期、名称、状态、城市、省份和官方 URL；覆盖优先且来源变化可审计
- `RaceChange` 区分 source-level 与 calendar-visible 变化，日历字段变化递增 `SEQUENCE`
- 完整成功列表达到 3 次缺失后才标记来源待复核，不因单次缺失删除/取消
- `MATCHED/NEW/AMBIGUOUS` 和 reconciliation issue 已实现，歧义不自动合并
- `/sync-runs`、CLI dry-run、真实同步和 ICS `ETag`/`Last-Modified` 已验证
- 报名窗口、组委会公告二次校验和真实授权仍属于后续运营流程，不由官方列表臆测

## Phase 2.5 — China coverage audit — completed

- 重新获取并保存 2026 中国田协年度目录 PDF、文档 checksum、发布日期、抓取时间和解析 sidecar
- 独立 `china_annual_catalog` adapter；年度目录记录经 `RaceSource` 进入身份解析，不直接更新 `Race`
- `audit china-coverage --year 2026` 输出 492 行全量分类、API-only、匹配证据、日期/名称变更和数据质量检查
- 精确日期的目录-only 建立 `date_tentative` Race；后续 API 通过来源回溯复用稳定 Race.id/ICS UID
- 目录/API 权威优先级、人工 override、来源级变更历史和重复导入幂等性已测试
- 中国覆盖率报告：`reports/china_coverage_2026.json` 和 `.md`

## Phase 2.6 — Realtime semantics — completed

- `china_annual_catalog` 明确降级为 `planning_catalog`，保留历史计划证据但不再是当前 authoritative source
- 新增 `source_role`、`publishable`、`published_at`、`retrieved_at`、`last_confirmed_at` 和 Race verification status
- `/calendar/china.ics` 只发布近期当前可靠来源；`/calendar/china-planned.ics` 单独发布未确认年度计划
- 当前/计划分类与 future 指标输出到 `reports/china_realtime_2026.json` 和 `.md`
- 年度目录/API/人工覆盖优先级、稳定 Race.id/UID、日期语义和旧 PDF 重读不刷新确认时间均已测试

## Phase 3 — International ingestion and reconciliation — completed

- AIMS ICS parser（保留源 UID 和状态）
- World Athletics 公开 HTML 合规 adapter；只读取官方页面 `__NEXT_DATA__`，不绕过私有 API/反爬
- 官方赛事链接、AIMS UID、World Athletics 数字 ID、Label、状态和 source hash
- `timezone=null`、TBC/多日/未知国家 unresolved 语义；主 Feed 仅全马/半马
- RaceAlias、SourceDiscrepancy、软合并字段和保守跨源身份审计
- `/calendar/world.ics`、`/calendar/international.ics` 与 `audit global-reconciliation`
- 2026 真实快照回放、幂等性、Feed 去重、中国 Race.id/UID 保持不变

## Phase 4 — GitHub-native static deployment — packaged; infrastructure pending

- Version-controlled deterministic `state/` export/import preserves Race IDs, source links, aliases, overrides, reconciliation, `SEQUENCE` and `LAST-MODIFIED`.
- Static exporter reuses the existing ICS generator and writes `site/` with Pages index, five public feeds, status metadata, robots and `.nojekyll`.
- GitHub Actions workflow provides one ordered daily sync, manual dispatch modes, concurrency, timeouts, independent iCalendar parsing and fail-closed deployment gates.
- GitHub Pages official Actions are wired for artifact upload/deploy; the repository is not connected to GitHub in this workspace, so `GITHUB_DEPLOYMENT = BLOCKED_BY_INFRASTRUCTURE`.
- Outlook client acceptance remains `REQUIRES_HUMAN` until the user subscribes from the new Windows 11 Outlook UI.

## Phase 5 — Reconciliation / deduplication — next hardening

- 外部 ID、组织方、城市、历史名称和距离项目的更完整组合匹配
- 更完整的人工 merge/split workflow 和冲突 override UI
- 每次身份决策的人工审批、可回滚操作界面
- 改期、取消、恢复举办的状态历史

## Phase 5 — Subscription filters

- 更完整的按距离/省份/国家/标签过滤和用户可组合筛选
- 用户 token 订阅地址
- 客户端刷新观察、缓存命中监控和订阅滥用保护

## Phase 6 — User custom calendar

- 用户自定义关注城市/赛事类型
- token 生命周期、撤销、隐私和滥用保护

## Phase 7 — Registration / lottery / reminders

- 报名开始/结束、抽签结果等非比赛日期
- 明确区分赛事日与提醒事件
- 仅在有可靠来源和变更历史时生成提醒
