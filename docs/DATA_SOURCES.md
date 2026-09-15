# Data Sources — validation history

测试日期：**2026-09-15（Asia/Shanghai）**。以下记录来自实际 HTTP 请求或实际页面读取，不把“理论上可以抓”写成已验证能力。

| Source | Region | Authority | Method | API | Stable ID | Fields | Risk | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 中国马拉松官方网站 `runchina.org.cn` | China | P0 official association platform | Vue SPA + JSON POST | `https://api-changzheng.chinaath.com/changzheng-content-center-api/api/homePage/official/searchCompetitionMls` | `raceId`，实测 `1000452016` | raceName, raceTime, raceAddress, raceGrade, raceItem | 前端使用接口，未看到面向第三方的公开 SLA；可能改字段/限流 | **Verified** |
| 中国马拉松官方网站详情 | China | P0 official association platform | JSON POST | `https://api-changzheng.chinaath.com/changzheng-content-center-api/api/homePage/official/searchById` | `id` / `raceId` | name, gameDate, province, city, project, raceGrade, organizer, WA label asset | 同上；详情字段比列表更丰富 | **Verified** |
| 中国田协 2026 赛事目录公告 | China | P0 official publication | HTML notice, linked publication/image | `https://www.sport.gov.cn/n14471/n14495/n14543/c29312946/content.html` | 公告/目录级，不是单场稳定 ID | 目录统计、赛事名称/时间/主办单位/类别（公告说明） | 页面内容及附件格式可能变化；目录是计划而非实时状态 | **Verified** |
| AIMS official calendar | International | P0 federation calendar | Public ICS | `https://aims-worldrunning.org/events.ics` (webcal advertised by official page) | AIMS UID, e.g. `aims-worldrunning.org-6907` | UID, DTSTART, DTEND, SUMMARY, LOCATION, DESCRIPTION, URL, STATUS, LAST-MODIFIED, SEQUENCE | AIMS member calendar，不覆盖所有赛事；字段/日期需以组委会再核验 | **Verified** |
| World Athletics Label Road Races | International | P0 federation calendar | Public HTML / Next.js `__NEXT_DATA__`; visible table | `https://worldathletics.org/competitions/world-athletics-label-road-races` | 数值 competition ID，结果链接可审计 | date, meeting, venue, country, subgroup/label, ranking category | 页面约 3.5 MB；内部数据请求未作为公开 API 使用；抓取和 ToS 风险仍需运营复核 | **Verified adapter** |
| 闹跑 / NowRun | China | P1 auxiliary | Server-rendered Next.js HTML | `https://www.nowrun.cn/` | 页面未在 Phase 1 依赖稳定 ID | 2026 日历、报名状态、时间、地点、A/B/C；页面注明原始数据来自中国马拉松官方发布 | 第三方服务、页面结构变化；不得作为唯一真相 | **Verified as auxiliary** |

## 详细核验记录

### 中国马拉松官方网站

主页 HTML 实际加载了前端 bundle。bundle 暴露了生产配置 `VITE_MLS_BASE_API=https://runchina-api.chinaath.com`，并调用了上面的 `api-changzheng.chinaath.com` content-center endpoint。Phase 1 直接 POST：

```json
{
  "provinceId": "",
  "cityId": "",
  "districtId": "",
  "raceName": "",
  "raceGrade": "",
  "raceStartTime": "",
  "pageNo": 1,
  "pageSize": 3
}
```

实际返回 HTTP 200 JSON，示例包含：

```json
{
  "raceId": 1000452016,
  "raceName": "2026南宁马拉松",
  "raceGrade": "A",
  "raceTime": "2026-12-20",
  "raceAddress": "广西壮族自治区/南宁市/",
  "raceItem": "[\"全程\",\"半程\"]"
}
```

详情 POST 同样实际返回 HTTP 200，补充了 `province`、`city`、`gameDate`、`project`、`compNameOrganizer`、`worldAthleticsGradeLogoUrl`。这是 Phase 2 最优先适配器，但上线前需要确认接口使用条款、频率限制和授权边界。

### AIMS

官方日历页面明确提供 `webcal://aims-worldrunning.org/events.ics`。HTTP GET `https://aims-worldrunning.org/events.ics` 实测 200，内容约 407 KB，为标准 VCALENDAR。样本实际包含 `UID`、全天 `DTSTART/DTEND`、`STATUS:CANCELLED`、`STATUS:TENTATIVE`、`LAST-MODIFIED`、`DESCRIPTION` 和 `URL`。因此 AIMS 是本项目 ICS 互操作性的参考实现，而不是简单复制源文件。

### World Athletics

官方 Label Road Races 页面实际返回 HTML，页面可见字段是 `Date / Meeting / Venue / Country`，并列出如 Xiamen Marathon、Shanghai Marathon、Shenzhen Marathon 等中国赛事。当前 HTML 的 `__NEXT_DATA__.props.pageProps.calendarEvents.results` 含数值 `id`、`name`、`venue`、`countryCode`、`startDate`、`endDate`、`competitionSubgroup` 和 `rankingCategory`。Phase 3 只解析这个公开页面证据，不调用私有 endpoint、不绕过登录或反爬；页面快照和 evidence JSON 保存在 `data/snapshots/phase3-research/`。

### Phase 3 replay evidence

- AIMS snapshot: `data/snapshots/phase3-research/aims-events-2026.ics`; actual HTTP 200, 451 VEVENT total, 205 records for 2026, 187 exact-date and 18 range records.
- World Athletics snapshot: `data/snapshots/phase3-research/world-athletics-label-road-races-2026.html`; actual HTTP 200, 325 2026 records, with numeric IDs and public labels.
- Combined import and reconciliation details: [reports/international_coverage_2026.md](../reports/international_coverage_2026.md) and [reports/global_reconciliation_2026.md](../reports/global_reconciliation_2026.md).

### 不能在 Phase 1 默认依赖的来源

赛事组委会官网是单场赛事最权威来源，但每个站点结构不同，必须逐赛事建立适配器或人工录入。NowRun、LapLab、最酷等适合查漏和交叉验证，不能覆盖官方记录或直接决定取消/改期。

## Source snapshots

- `tests/fixtures/aims_sample.ics`：从 AIMS 实际响应抽取的一个最小字段 fixture，保留标准字段和转义样式，不保存完整日历。
- `tests/fixtures/china_runchina_sample.json`：从官方接口实际响应抽取的最小列表和详情样本。
- `tests/fixtures/world_athletics_sample.json`：从官方页面实际观察到的字段形状的最小 schema fixture。页面是 HTML，不将 3.5 MB 完整页面提交到仓库。

## Phase 2 中国官方真实核验

在 2026-09-15（Asia/Shanghai）使用当前适配器完成真实分页 dry-run：列表接口返回 `totalCount=2913`，抓取 30 页（`pageSize=100`），再在本地按 `raceTime` 筛出 2026 年记录 285 条；其中抽取详情 20 条，详情请求全部成功。归一化后的分类为 A/B/C/TEN，项目字段同时保存规范值和来源原文。

这 285 条不是中国田协 2026 年度赛事目录的硬性复刻。体育总局官方公告口径为 492 场年度赛事，年度目录是计划/公告集合，而接口是当前平台可查询的动态列表；二者统计口径和更新时间不同，系统保留 API 快照及运行计数，不强行宣称相等。

robots/ToS：对官网 `robots.txt`、`/terms` 和 `/privacy` 的自动访问触发了站点/EdgeOne 防护，未以 Cookie、Token 或其他方式绕过；API 域名的 robots 路径返回 404 JSON。因而 Phase 2 只能记录“未独立确认完整条款/SLA”，不能把它写成“已获授权”。上线前应由运营/法务确认使用边界。

## Phase 2.5 中国田协年度目录第二官方来源

2026 年年度目录使用国家体育总局公告页及其公开 PDF 附件作为官方出版物来源：

- 公告页：`https://www.sport.gov.cn/n14471/n14495/n14543/c29312946/content.html`
- PDF：`https://file.shuzixindong.com/changzheng/84554/fddbe29f6e434035918201d2a17dbcac.pdf`
- 发布日期：2025-12-19；目录行数：492；分类统计：A=254、B=67、C=171。
- 本地证据：`data/source_documents/china_annual_catalog_2026.pdf`；SHA-256：`52633a1251d85324eb269872fdcfca3e3682aa8902f7c9565fb08384ec05128a`。

`china_annual_catalog` adapter 接收 PDF 或同名规范化 JSON sidecar。PDF 每次读取都会校验 SHA-256；sidecar 保存文档 URL、发布日期、抓取时间、提取方法、行号、原始计划日期文本和来源原文。月-only/日期范围不推断具体日；没有精确日期的 CATALOG_ONLY 记录会留在审计报告中，等待后续 API 或人工/组委会来源补齐，不伪造日历日期。

年度目录的 `external_id` 是基于年份、赛事名称、省份、计划日期文本、主办单位、设项和类别的 SHA-256，不使用 PDF 行号，因此跨版本行号变化不会自动制造新的身份。目录记录必须经过覆盖率审计和保守身份匹配后，才进入 `RaceSource`；API 先出现的 Race 会复用，API 后出现时会通过目录来源回溯匹配同一 `Race.id`。

Phase 2.6 将该来源角色明确为 `planning_catalog`：

- `published_at=2025-12-19` 表示公告/文档发布日期；`retrieved_at` 表示本次读取证据的时间；两者都不等于 `last_confirmed_at`。
- `publishable=false`，年度目录不会单独确认赛事，也不会单独进入正式 `/calendar/china.ics`。
- 年度计划赛事仍保留在 canonical `Race` 和 `RaceSource` 中，并通过 `/calendar/china-planned.ics` 单独展示。
