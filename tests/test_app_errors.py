"""Flask route error handling tests using test client."""
import json
import pytest
import app as flask_app_module
from app import app


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset module-level globals before each test."""
    flask_app_module.progress_state = {"running": False, "phase": "", "done": 0, "total": 0}
    flask_app_module.last_results = []
    flask_app_module.last_analysis = None
    yield


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _json(resp):
    return json.loads(resp.data)


# ─── /api/fetch validation ─────────────────────────────────────────────

class TestApiFetchValidation:

    def test_bad_start_date_returns_400(self, client):
        resp = client.post("/api/fetch", json={
            "asset": "BTC", "interval": "5m",
            "start_date": "not-a-date", "end_date": "2026-04-18",
        })
        assert resp.status_code == 400
        assert "error" in _json(resp)

    def test_bad_end_date_returns_400(self, client):
        resp = client.post("/api/fetch", json={
            "asset": "BTC", "interval": "5m",
            "start_date": "2026-04-17", "end_date": "tomorrow",
        })
        assert resp.status_code == 400
        assert "error" in _json(resp)

    def test_start_after_end_returns_400(self, client):
        resp = client.post("/api/fetch", json={
            "asset": "BTC", "interval": "5m",
            "start_date": "2026-04-18", "end_date": "2026-04-17",
        })
        assert resp.status_code == 400
        data = _json(resp)
        assert "error" in data
        assert "start_date" in data["error"]

    def test_error_message_no_traceback(self, client):
        resp = client.post("/api/fetch", json={
            "start_date": "bad", "end_date": "2026-04-18",
        })
        body = resp.data.decode()
        assert "Traceback" not in body
        assert "File \"" not in body

    def test_bad_date_does_not_set_progress_running(self, client):
        client.post("/api/fetch", json={
            "start_date": "not-a-date", "end_date": "2026-04-18",
        })
        assert flask_app_module.progress_state["running"] is False


# ─── /api/import validation ────────────────────────────────────────────

class TestApiImportValidation:

    def test_non_list_returns_400(self, client):
        resp = client.post("/api/import",
            data=json.dumps({"slug": "abc"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "error" in _json(resp)

    def test_string_body_returns_400(self, client):
        resp = client.post("/api/import",
            data='"just a string"',
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_list_of_non_dicts_returns_400(self, client):
        resp = client.post("/api/import",
            data=json.dumps(["not", "dicts"]),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_missing_slug_returns_400(self, client):
        resp = client.post("/api/import",
            data=json.dumps([{"up_snapshots": []}]),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = _json(resp)
        assert "slug" in data["error"].lower()

    def test_valid_import_returns_200(self, client):
        payload = [{"slug": "btc-updown-5m-12345", "up_snapshots": [], "down_snapshots": []}]
        resp = client.post("/api/import",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert _json(resp)["count"] == 1

    def test_valid_import_sets_last_results(self, client):
        payload = [{"slug": "s1"}, {"slug": "s2"}]
        client.post("/api/import", data=json.dumps(payload), content_type="application/json")
        assert len(flask_app_module.last_results) == 2


# ─── /api/simulate validation ──────────────────────────────────────────

class TestApiSimulateValidation:

    def test_no_results_returns_error(self, client):
        resp = client.post("/api/simulate", json={"insight": {"title": "t"}, "bet_size": 100})
        data = _json(resp)
        assert "error" in data

    def test_no_insight_returns_400(self, client):
        flask_app_module.last_results = [{"slug": "x", "error": None,
                                           "up_snapshots": [], "down_snapshots": []}]
        resp = client.post("/api/simulate", json={"bet_size": 100})
        assert resp.status_code == 400
        assert "error" in _json(resp)

    def test_zero_bet_size_returns_400(self, client):
        flask_app_module.last_results = [{"slug": "x"}]
        resp = client.post("/api/simulate",
            json={"insight": {"title": "BTC +$100 at min 1 → BUY UP"}, "bet_size": 0})
        assert resp.status_code == 400
        data = _json(resp)
        assert "bet_size" in data["error"]

    def test_negative_bet_size_returns_400(self, client):
        flask_app_module.last_results = [{"slug": "x"}]
        resp = client.post("/api/simulate",
            json={"insight": {"title": "t"}, "bet_size": -50})
        assert resp.status_code == 400

    def test_no_traceback_in_error_response(self, client):
        resp = client.post("/api/fetch", json={
            "start_date": "bad-date", "end_date": "2026-04-18",
        })
        body = resp.data.decode()
        assert "Traceback" not in body
        assert "line " not in body or "error" in _json(resp)
