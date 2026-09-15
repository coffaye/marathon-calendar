# International sources / 国际来源

核验日期：2026-09-15（Asia/Shanghai）。适配器只使用公开、低频、串行请求，不登录、不绕过验证码/WAF、不调用未公开 API。

## AIMS

官方订阅页说明可订阅 `webcal://aims-worldrunning.org/events.ics`；实际 HTTP GET `https://aims-worldrunning.org/events.ics` 返回 200，当前快照包含 451 个 VEVENT（本地按 2026 年过滤后 205 条）。

解析字段：`UID`、`SUMMARY`、`DTSTART`、`DTEND`、`STATUS`、`LAST-MODIFIED`、`URL`、`LOCATION`、`DESCRIPTION`、`SEQUENCE`。`RaceSource.external_id` 使用 AIMS UID，例如 `aims-worldrunning.org-6907`，不使用 name+date。

真实快照观察到：`STATUS:TENTATIVE`、`STATUS:CANCELLED`，未发现独立的 `RESCHEDULED` 状态；适配器仍识别 summary/description 中的 postponed/rescheduled。`DTEND` 是全天事件的排他结束日：`DTSTART=2026-09-11, DTEND=2026-09-14` 被保存为 9 月 11–13 日范围，不把 11 日当成马拉松日。无法确认主赛日的多日事件进入 `unresolved_source_records`。`TBC` 使用独立单词匹配，不会把 `JTBC` 误判为 TBC。

角色为 `international_calendar`，可发布但低于 World Athletics federation、国家官方和组委会。AIMS 对赛事信息提示仍应向赛事组织者确认，故不能覆盖更高权威来源。

## World Athletics Label Road Races

官方页面：`https://worldathletics.org/competitions/world-athletics-label-road-races`。实际公开 HTML 返回 Next.js 的 `__NEXT_DATA__`，其中 `props.pageProps.calendarEvents.results` 是页面可见日历数据。2026 快照含 325 条记录，字段包括 numeric `id`、`name`、`venue`、`countryCode`、`startDate`、`endDate`、`competitionSubgroup`、`rankingCategory` 和 `undeterminedCompetitionPeriod`。

适配器使用 numeric `id` 作为 `RaceSource.external_id`，官方结果链接为 `/calendar-results/<id>/result`；保留原始 `competitionSubgroup`（实测包括 `Platinum`、`Gold`、`Silver`、`Bronze`、`Elite`、`Label`），不把等级限制成过窄 enum。页面不是按第三方承诺的稳定 API，schema 缺失时同步失败并保留错误，不绕过站点边界。

角色为 `international_federation`，高于 AIMS。日期相同才认为精确；国家码未知不会猜测。页面 2026 记录中 245 条识别为马拉松/半马，其他距离仍保留 Source Record 但不进入主 Feed。

## Snapshots and commands

```powershell
python -m marathon_calendar sync aims --year 2026 --dry-run
python -m marathon_calendar sync world-athletics --year 2026 --dry-run
python -m marathon_calendar sync international --year 2026
```

AIMS 原始 ICS 按来源/UTC 日期保存；World Athletics 保存 HTML 快照和最小 `evidence.json`，内容含 build id、calendar 参数、字段清单和记录数。当前真实回放证据位于 `data/snapshots/aims/2026-09-15/` 与 `data/snapshots/world_athletics/2026-09-15/`。
