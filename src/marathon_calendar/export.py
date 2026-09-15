"""Static GitHub Pages exporter built on the existing domain and ICS renderer."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain.models import SyncStatus
from .ics import races_to_ics
from .migrations import CURRENT_SCHEMA_VERSION
from .publication import races_for_feed
from .repository import RaceStore
from .state import assert_production_safe
from .test_feed import test_subscription_race


FEEDS = (
    ("china", "中国赛事", "中国当前已确认的赛事", "中国马拉松赛事日历", False),
    ("world", "全球赛事", "所有国家的可发布全马/半马", "全球马拉松赛事日历", False),
    ("international", "国际赛事", "排除中国的可发布全马/半马", "国际马拉松赛事日历", False),
    ("china-planned", "中国年度规划", "中国田协年度规划，尚待当前来源确认", "中国马拉松年度规划（含未确认赛事）", True),
    ("full-marathon", "全马赛事", "明确包含全程马拉松的赛事", "Marathon Calendar — Full Marathon", False),
)


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}" if base_url else path


def _stable_stamp(races: list[Any]) -> datetime:
    return max((race.last_modified for race in races), default=datetime(1970, 1, 1, tzinfo=timezone.utc))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def _latest_source_status(store: RaceStore) -> dict[str, dict[str, Any]]:
    result = {
        "china_official": {"last_success": None, "last_status": None},
        "aims": {"last_success": None, "last_status": None},
        "world_athletics": {"last_success": None, "last_status": None},
    }
    for run in store.list_all_sync_runs():
        if run.source not in result:
            continue
        timestamp = run.finished_at or run.started_at
        result[run.source]["last_status"] = run.status.value
        # A partial_success still has a usable, validated source snapshot;
        # expose its timestamp as last_success while retaining last_status so
        # the unresolved/record-level caveat remains visible.
        if run.status in {SyncStatus.success, SyncStatus.partial_success}:
            result[run.source]["last_success"] = timestamp.isoformat()
    now = datetime.now(timezone.utc)
    for source in result.values():
        last_success = source["last_success"]
        source["stale"] = (
            last_success is None
            or (now - datetime.fromisoformat(last_success)).total_seconds() > 48 * 60 * 60
        )
    return result


def _index_html(*, base_url: str, feed_status: dict[str, dict[str, Any]], generated_at: str) -> str:
    cards = []
    for key, label, description, _calendar_name, _planned in FEEDS[:4]:
        feed = feed_status[key]
        url = feed["url"]
        safe_url = html.escape(url, quote=True)
        cards.append(
            f'''<article class="card"><h2>{html.escape(label)}</h2>
<p>{html.escape(description)}</p><p><strong>{feed["events"]}</strong> events · generated {html.escape(generated_at)}</p>
<code id="url-{key}">{safe_url}</code>
<p><a href="{safe_url}">Open feed</a> <button type="button" data-copy="{safe_url}">Copy subscription URL</button></p></article>'''
        )
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="favicon.svg" type="image/svg+xml"><title>Marathon Calendar / 马拉松赛事订阅日历</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1040px;margin:0 auto;padding:2rem;line-height:1.55;background:#f6f7f9;color:#17202a}}header{{margin-bottom:2rem}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}}.card{{background:white;border:1px solid #dfe3e8;border-radius:12px;padding:1rem;box-shadow:0 2px 8px #17202a12}}code{{display:block;overflow-wrap:anywhere;background:#f0f2f4;padding:.5rem;border-radius:6px;font-size:.85rem}}button{{cursor:pointer;padding:.4rem .6rem}}a{{color:#075985}}</style></head>
<body><header><h1>🏃 Marathon Calendar</h1><p>马拉松赛事订阅日历</p><p>持续更新的中国及国际马拉松日历。请使用 <strong>Subscribe from web / 从 Web 订阅</strong>，不要只下载后 Import；只有订阅才能继续获得改期更新。</p></header>
<section class="grid">{"".join(cards)}</section>
<section><h2>Windows 11 新版 Outlook</h2><p>Calendar → Add calendar → Subscribe from web → 粘贴上面的 HTTPS 地址。</p><p>Last generated: {html.escape(generated_at)}</p></section>
<script>document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',async()=>{{await navigator.clipboard.writeText(b.dataset.copy);b.textContent='Copied';}}));</script>
</body></html>
'''


def export_site(
    store: RaceStore,
    output_dir: str | Path = "site",
    *,
    base_url: str = "",
    test_feed_version: int = 1,
) -> dict[str, Any]:
    assert_production_safe(store)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    feed_status: dict[str, dict[str, Any]] = {}
    for key, _label, _description, calendar_name, planned in FEEDS:
        races = list(races_for_feed(store, key))
        body = races_to_ics(races, calendar_name=calendar_name, now=_stable_stamp(races), planned=planned)
        path = output / "calendar" / f"{key}.ics"
        _write(path, body)
        feed_status[key] = {
            "path": f"calendar/{key}.ics",
            "url": _url(base_url, f"calendar/{key}.ics"),
            "events": len(races),
            "bytes": len(body.encode("utf-8")),
        }

    test_race = test_subscription_race(test_feed_version)
    test_body = races_to_ics(
        [test_race],
        calendar_name="Marathon Calendar Subscription Test",
        now=test_race.last_modified,
    )
    _write(output / "calendar" / "test-subscription.ics", test_body)
    feed_status["test-subscription"] = {
        "path": "calendar/test-subscription.ics",
        "url": _url(base_url, "calendar/test-subscription.ics"),
        "events": 1,
        "bytes": len(test_body.encode("utf-8")),
    }

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    status = {
        "generated_at": generated_at,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "feeds": feed_status,
        "sources": _latest_source_status(store),
        "policy": {
            "canonical_state_only": True,
            "raw_snapshots_published": False,
            "internal_database_published": False,
        },
    }
    _write(output / "data" / "status.json", json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write(
        output / "data" / "feeds.json",
        json.dumps({"generated_at": generated_at, "feeds": feed_status}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write(output / "index.html", _index_html(base_url=base_url, feed_status=feed_status, generated_at=generated_at))
    _write(output / "robots.txt", "User-agent: *\nDisallow: /calendar/\nDisallow: /data/\n")
    _write(output / "404.html", "<!doctype html><meta charset=\"utf-8\"><title>Not found</title><p>Marathon Calendar page not found.</p>\n")
    _write(
        output / "favicon.svg",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><text y="52" font-size="52">🏃</text></svg>\n',
    )
    _write(output / ".nojekyll", "")
    return status


def export_test_feed(version: int, output_path: str | Path = "site/calendar/test-subscription.ics") -> dict[str, Any]:
    race = test_subscription_race(version)
    body = races_to_ics(
        [race],
        calendar_name="Marathon Calendar Subscription Test",
        now=race.last_modified,
    )
    path = Path(output_path)
    _write(path, body)
    return {
        "path": str(path),
        "version": 2 if version == 2 else 1,
        "uid": f"race_{race.id}@marathon-calendar",
        "sequence": race.sequence,
        "bytes": len(body.encode("utf-8")),
    }
