# Global reconciliation / 全球身份解析

## Identity

先命中 `(source_name, external_id)`，再使用保守候选：国家、城市、规范名称/`RaceAlias`、距离交集、精确日期或有限日期变化。中英文城市别名（如 Shanghai/上海、Xiamen/厦门）进入明确转换表；单纯“同城同日”不足以自动合并。多个候选写入 `ReconciliationIssue`，不删除 Race。

每个 source-specific name 都保存为 `RaceAlias`，而不是覆盖 `Race.name`。来源字段始终留在 `RaceSource.raw_data`/`normalized_data`；effective 值按 authority/freshness 选择。来源之间发生 date/city/country/status/distance 冲突时写入 `SourceDiscrepancy`，不静默丢弃。

## Festival and merge safety

同一品牌周末的 Marathon/Half/10K 可以在证据足够时共享一个 Race；Saturday 10K 与 Sunday Marathon 不会因为城市相同而使用 festival 首日。AIMS 多日范围没有可信主赛日时不建 dated Race。规范化 key 去除距离后形成的 full/half 同城候选会列入 festival-family review，不直接作为硬重复。

历史重复只能通过显式 `merge_duplicate_races` 执行：选择有当前发布来源/人工覆盖、然后较早创建、最后 UUID 较小的 survivor；迁移 Source、Alias、Change、override、discrepancy 引用，loser 标记 `merged_into_id`，不 DELETE。UID 始终保留 survivor UID。

## 2026 real replay result

基于 Phase 2.6 数据库前向迁移副本和 2026-09-15 的 AIMS/World Athletics 公开快照，报告见：

- `reports/international_coverage_2026.md` / `.json`
- `reports/global_reconciliation_2026.md` / `.json`

当前 global audit：867 个 active canonical Race；655 个单来源、57 个两来源、1 个三来源；China+AIMS=1、China+WA=28、AIMS+WA=31、三源=1；硬重复=0，开放歧义=0，来源差异=156，未决 source record=35。跨源 Source Record 实际挂入已有多源 Race 的可审计下界为 122。人工抽查样本为 10 个 AIMS-only、10 个 WA-only、30 个 cross-source，满足至少 10 个交叉样本。

同城同日不同距离项目被列为 festival-family 候选，而非重复；明确同名不同日期事件也不会因规范化 key 自动 merge。

## CLI

```powershell
python -m marathon_calendar audit global-reconciliation --year 2026 --db data/phase3_final.db
```

审计输出 source distribution、四类 overlap、重复簇、歧义、字段差异、未决 TBC/多日记录和抽查样本。World/International ICS 的去重单位是 canonical Race，不是 source record。
