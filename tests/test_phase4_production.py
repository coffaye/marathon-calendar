from __future__ import annotations

from fastapi.testclient import TestClient

from marathon_calendar.main import create_app


def test_production_does_not_seed_fixtures_and_is_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "production")
    monkeypatch.delenv("MARATHON_CALENDAR_TEST_FEED", raising=False)
    app = create_app(tmp_path / "production.db")
    client = TestClient(app)

    assert app.state.environment == "production"
    assert app.state.store.count() == 0
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["schema_current"] is True
    assert health.json()["race_count"] == 0
    assert client.get("/livez").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert client.get("/calendar/test-subscription.ics").status_code == 404


def test_local_test_subscription_preserves_uid_and_sequence(monkeypatch, tmp_path):
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "development")
    monkeypatch.setenv("MARATHON_CALENDAR_TEST_FEED", "1")
    monkeypatch.setenv("MARATHON_CALENDAR_TEST_FEED_VERSION", "1")
    client = TestClient(create_app(tmp_path / "development.db"))
    v1 = client.get("/calendar/test-subscription.ics")

    monkeypatch.setenv("MARATHON_CALENDAR_TEST_FEED_VERSION", "2")
    v2 = client.get("/calendar/test-subscription.ics")

    def field(body: str, name: str) -> str:
        return next(line.split(":", 1)[1] for line in body.splitlines() if line.startswith(name + ":"))

    assert v1.status_code == v2.status_code == 200
    assert field(v1.text, "UID") == field(v2.text, "UID")
    assert field(v1.text, "SEQUENCE") == "0"
    assert field(v2.text, "SEQUENCE") == "1"
    assert "Marathon Calendar Subscription Test V1" in v1.text
    assert "Marathon Calendar Subscription Test V2" in v2.text
    assert "DTSTART;VALUE=DATE:20300115" in v1.text
    assert "DTSTART;VALUE=DATE:20300116" in v2.text
    assert v1.headers["etag"] != v2.headers["etag"]
    assert client.get(
        "/calendar/test-subscription.ics",
        headers={"If-None-Match": v2.headers["etag"]},
    ).status_code == 304


def test_sqlite_production_pragmas_and_read_only_http_surface(monkeypatch, tmp_path):
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "production")
    app = create_app(tmp_path / "pragmas.db")
    with app.state.store._connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    methods = {method for route in app.routes for method in (route.methods or set())}
    assert not methods.intersection({"POST", "PUT", "PATCH", "DELETE"})

    client = TestClient(app)
    head = client.head("/calendar/world.ics")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["etag"]
    assert "attachment" not in head.headers.get("content-disposition", "").lower()


def test_production_build_rejects_fixture_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "development")
    store = create_app(tmp_path / "fixture.db").state.store
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "production")
    from marathon_calendar.export import export_site

    try:
        export_site(store, tmp_path / "site")
    except RuntimeError as exc:
        assert "fixture" in str(exc)
    else:
        raise AssertionError("production exporter accepted fixture data")
