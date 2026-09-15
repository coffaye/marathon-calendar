# 中国赛事覆盖率审计说明

Phase 2.5 使用两个独立官方口径：

1. `china_annual_catalog`：中国田协 2026 年度计划目录，官方 PDF 共 492 行，包含计划中的赛事名称、计划时间、省份、主办单位、设项和 A/B/C 类别。
2. `china_official`：中国马拉松官方网站当前可查询 API，Phase 2 真实分页结果为 2,913 条，按 `raceTime` 在本地筛得 2026 年 285 条 `RaceSource`。

它们不是同一张表的两个分页：年度目录是年度规划/公告口径，官方原文使用“计划举办”“预计将举办”，API 是当前平台可查询口径。年度目录在系统中是 `planning_catalog`，即 planning baseline / discovery source / historical evidence，不是 current confirmed calendar。报告同时保存每一条目录行的结果、匹配 API external ID/Race UUID、方法、分数和证据，并对每条 API-only 做分析。

## 可复现命令

```powershell
python -m marathon_calendar audit china-coverage --year 2026 `
  --document data/source_documents/china_annual_catalog_2026.pdf `
  --db data/phase2_final.db --report-dir reports
```

导入顺序：官方 PDF/sidecar 校验 → dry-run → 覆盖率审计 → 本地写入。

## 当前真实结果

最终导入数据库 `data/phase25_final.db` 的报告为 [reports/china_coverage_2026.md](../reports/china_coverage_2026.md)，机器可读明细为 [reports/china_coverage_2026.json](../reports/china_coverage_2026.json)。报告中的分类总和严格等于 492；`CATALOG_ONLY` 的前 30 行、全部 `AMBIGUOUS`、所有 `API_ONLY`、日期变更和名称变更均在 JSON 中保留。

本次 reconciliation 指标为 407/492（82.72%），API enrichment 为 246/492（50.00%）。这两个数字是目录对账技术指标，不是“中国当前赛事覆盖率”；当前 Feed 的实时性与数量见 [reports/china_realtime_2026.md](../reports/china_realtime_2026.md)，其总量 denominator 明确为 unknown。

年度目录的精确日期只作为 `date_tentative` Race 的计划基线；月-only/日期范围文本仍保留在 `RaceSource`，不推断具体日，因此不会伪造 ICS 日期。目录 source 的 `publishable=false`，所以 CATALOG_ONLY 不再进入正式 `/calendar/china.ics`，只进入规划 Feed。
