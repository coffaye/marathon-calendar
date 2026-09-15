# Publication rules / Feed 语义

## 中国 Feed 的基线解释

在 Phase 2.6 基线（还没有国际来源回放）中，2026 年中国 Race 分类为：

- `LIVE_CONFIRMED = 61`：未来日期且有当前可靠来源；
- `NEEDS_REVIEW = 222`：API 已返回的过去/当前记录，但生命周期字段没有明确写出“已完赛”；
- `/calendar/china.ics = 283`：两者都来自当前、可发布的 live source。

因此 `61 + 222 = 283` 是有意的语义，不是把 `NEEDS_REVIEW` 错当成计划赛事。这里的 publication gate 是“来源当前且可发布”，不是“未来日期且状态字段完整”。222 条必须继续进入正式 Feed，但在实时性报告中单独标记为待复核。

年度目录 `china_annual_catalog` 是 `planning_catalog`，永久 `publishable=false`，不能单独让赛事进入正式中国 Feed。没有当前来源的精确日期计划赛事进入 `/calendar/china-planned.ics`，标题附带“（计划）”。

Phase 3 的 AIMS / World Athletics 也是当前可发布来源，因此它们可以为原本只有计划证据的中国 Race 提供当前国际来源；这会使中国正式 Feed 在国际回放后增加有效事件，但不会改变上述 gate，也不会把年度目录本身升级为 authoritative。

## Global / international Feed

`/calendar/world.ics` 只输出：

1. 未被软合并的 canonical Race；
2. 至少一个当前、可发布的来源；
3. `marathon` 或 `half_marathon`；
4. 每个 canonical Race 只输出一次，UID 为 `race_<Race.id>@marathon-calendar`。

`/calendar/international.ics` 使用相同规则并要求 `country != CHN`。纯 10K、5K、mile、road race、ultra 的来源原文与 Source Record 会保留，但默认不进入这两个主 Feed。TBC 和无法确定主赛日的多日记录进入 unresolved Source Record，不创建伪日期。

## Authority and update

有效字段优先级为：

```text
manual override > official organizer > live national official
> international federation > international calendar > planning catalog > secondary
```

来源值不同但没有赢得 effective canonical value 时，只写来源历史/`SourceDiscrepancy`，不增加 `SEQUENCE` 或 `LAST-MODIFIED`。确实改变日历可见值时，保持 Race.id/UID，递增 `SEQUENCE`。
