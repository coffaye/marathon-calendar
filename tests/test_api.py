from fastapi.testclient import TestClient

from marathon_calendar.main import create_app


def test_calendar_endpoint_returns_ics(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    response = client.get("/calendar/all.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.text.startswith("BEGIN:VCALENDAR\r\n")
    assert "UID:race_" in response.text


def test_filters_are_available(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    china = client.get("/calendar/china.ics")
    planned = client.get("/calendar/china-planned.ics")
    full = client.get("/calendar/full-marathon.ics")
    assert china.status_code == 200
    assert "南昌马拉松" not in china.text
    assert "南昌马拉松" not in planned.text
    assert "东京马拉松" not in china.text
    assert full.status_code == 200
    assert "上海马拉松" in full.text


def test_feed_exposes_stable_cache_validators(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    first = client.get("/calendar/china.ics")
    second = client.get("/calendar/china.ics", headers={"If-None-Match": first.headers["etag"]})
    assert first.headers["etag"]
    assert first.headers["last-modified"]
    assert second.status_code == 304
    third = client.get(
        "/calendar/china.ics",
        headers={"If-Modified-Since": first.headers["last-modified"]},
    )
    assert third.status_code == 304
