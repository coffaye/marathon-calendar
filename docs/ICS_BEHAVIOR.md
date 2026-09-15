# ICS Behavior / 订阅行为

本项目输出 RFC 5545 风格的 iCalendar 文本。订阅接口是只读接口；同步由受控 CLI 或外部调度器执行，不提供公开的 `POST /sync`。

## Endpoint

- `GET /calendar/all.ics`
- `GET /calendar/china.ics`
- `GET /calendar/full-marathon.ics`
- `GET /calendar/world.ics`：所有国家的可发布全马/半马
- `GET /calendar/international.ics`：排除中国的可发布全马/半马

The same selector and `races_to_ics()` implementation is used by FastAPI and `marathon-calendar export-site`; static files therefore preserve the dynamic endpoint's event semantics and stable UIDs. Static Pages does not need to reproduce application HTTP headers.

响应媒体类型为 `text/calendar`。事件按赛事日期、名称和内部 ID 稳定排序。

## Identity and update semantics

每个 canonical `Race` 使用稳定 UID：

```text
race_<Race.id>@marathon-calendar
```

`Race.id` 是内部 UUID，与中国官方的 `raceId` 或其他来源 ID 不同。赛事改期、状态变化、地点变化或其他日历可见字段变化时，保留 UID，递增 `SEQUENCE`，并更新 `LAST-MODIFIED`。这样客户端会把它识别为原事件更新，而不是新事件。

## Event fields

- Phase 1/2 默认输出全天事件。
- `DTSTART;VALUE=DATE` 是赛事日。
- `DTEND;VALUE=DATE` 是次日，遵循全天事件的 exclusive end 语义。
- 未知开赛时间保持为空，不猜测时间。
- `LOCATION` 按省份、城市、国家组成。
- `URL` 使用 canonical Race 的 `official_url`（如果存在）。
- 国际来源未知时区保持 `TZID` 为空，不将国家码机械映射成 IANA timezone；国家 alpha-2 仅用于审计展示。
- `scheduled`、`registration_*`、`completed` 等正常状态输出 `CONFIRMED`。
- `postponed` 和 `date_tentative` 输出 `TENTATIVE`。
- `cancelled` 输出 `CANCELLED`，并在标题和描述中明确标记；取消记录不会被删除。

## Caching

每个 ICS 响应带有：

- `ETag`：由完整响应正文 SHA-256 生成；
- `Last-Modified`：取当前筛选结果中最大的 `Race.last_modified`；
- `Cache-Control: public, max-age=900`。

客户端发送匹配的 `If-None-Match` 或不早于 `Last-Modified` 的 `If-Modified-Since` 时返回 `304 Not Modified`。由于 `DTSTAMP` 使用相同的稳定时间戳，单纯重复请求不会导致 ETag 漂移。静态 Pages 的 ETag、Last-Modified、Cache-Control 和 `.ics` Content-Type 由 GitHub/CDN 实际产生，必须上线后用 `curl -I` 记录，不能从 FastAPI 结果推断。

## Compatibility notes

订阅客户端可能自行决定刷新频率，服务端不能保证客户端立即拉取。生产部署仍应使用 HTTPS，并避免在 URL 中放置个人信息或可长期滥用的管理凭证。

国际 Feed 只选择 `is_main_distance` 的 Race 和当前 publishable 来源。AIMS/World Athletics 的 source status、TBC/range 精度和原始日期都保留在 Source Record；没有精确比赛日的记录不生成可发布 ICS 事件。全马与半马在同一周末可能属于同一 festival family，审计会列为 review candidate，不自动折叠成一个 Race。
