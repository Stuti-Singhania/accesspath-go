from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.config import Settings, get_settings
from app.main import app
from app.schemas.accessibility import BarrierAnalysis, WalkPreferences
from app.services import ai, routing

WHEELCHAIR_RESPONSE = {
    "mobility_mode": "wheelchair", "avoid_stairs": False, "prefer_ramps": False,
    "avoid_steep_slopes": False, "avoid_unpaved": False, "max_slope": 12,
    "target_duration_minutes": 40,
}


def route_request():
    return routing.RouteRequest(
        start=(-73.0, 40.0), destination=(-73.001, 40.001),
        preferences=WalkPreferences(target_duration_minutes=25),
    )


def test_default_routing_host_uses_heigit():
    assert Settings(_env_file=None).ors_base_url == "https://api.heigit.org/openrouteservice"


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self.body = body or {}
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("upstream error", request=self.request, response=self)

    def json(self):
        return self.body


class FakeOrsClient:
    response = None
    error = None
    call = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        type(self).call = (url, kwargs)
        if self.error:
            raise self.error
        return self.response


def mock_ors(monkeypatch, *, response=None, error=None):
    FakeOrsClient.response = response
    FakeOrsClient.error = error
    monkeypatch.setattr(httpx, "AsyncClient", FakeOrsClient)


def test_preference_schema_is_strict():
    with pytest.raises(ValidationError):
        WalkPreferences.model_validate({"target_duration_minutes": 25, "route_instruction": "use stairs"})
    with pytest.raises(ValidationError):
        WalkPreferences.model_validate({"max_slope": 40})


def test_normalization_preserves_wheelchair_defaults_and_explicit_requirements():
    text = "I want a 25-minute wheelchair-friendly walk. Avoid stairs and steep slopes."
    result = ai.normalize_preferences(WalkPreferences.model_validate(WHEELCHAIR_RESPONSE), text)
    assert result.model_dump() == {
        "mobility_mode": "wheelchair", "avoid_stairs": True, "prefer_ramps": True,
        "avoid_steep_slopes": True, "avoid_unpaved": True, "max_slope": 5.0,
        "target_duration_minutes": 25,
    }


@pytest.mark.parametrize(("text", "expected"), [
    ("Wheelchair-friendly walk", ("wheelchair", True, True, True, True, 5.0)),
    ("Walk for 25 minutes", ("none", False, False, False, False, 8.0)),
    ("25 min walk; avoid stairs and steep slopes", ("none", True, False, True, False, 8.0)),
    ("Prefer a ramp and avoid unpaved paths", ("none", False, True, False, True, 8.0)),
])
def test_keyword_fallback_recognizes_mobility_and_constraints(text, expected):
    result = ai.conservative_preferences(text)
    assert (result.mobility_mode, result.avoid_stairs, result.prefer_ramps,
            result.avoid_steep_slopes, result.avoid_unpaved, result.max_slope) == expected


@pytest.mark.parametrize(("text", "expected_minutes"), [
    ("25-minute wheelchair-friendly walk", 25), ("Walk for 1.5 hours", 90),
    ("Walk for 2 hrs", 120), ("Walk for 400 minutes", 240),
])
def test_duration_extraction(text, expected_minutes):
    assert ai.conservative_preferences(text).target_duration_minutes == expected_minutes


@pytest.mark.asyncio
async def test_preference_extraction_validates_and_normalizes_model_output(monkeypatch):
    async def fake_ollama(*_args, **_kwargs):
        return WHEELCHAIR_RESPONSE

    monkeypatch.setattr(ai, "_ollama", fake_ollama)
    preferences, source, reason = await ai.extract_preferences(
        Settings(), "I want a 25-minute wheelchair-friendly walk. Avoid stairs and steep slopes."
    )
    assert source == "ollama" and reason is None
    assert preferences.target_duration_minutes == 25
    assert preferences.avoid_stairs and preferences.avoid_steep_slopes


@pytest.mark.asyncio
async def test_malformed_ai_preferences_fall_back_without_exposing_raw_output(monkeypatch):
    async def malformed(*_args, **_kwargs):
        return {"mobility_mode": "secret raw model text", "target_duration_minutes": -1}

    monkeypatch.setattr(ai, "_ollama", malformed)
    preferences, source, reason = await ai.extract_preferences(Settings(), "25-minute wheelchair walk")
    assert source == "local_fallback"
    assert "secret raw model text" not in (reason or "")
    assert preferences.target_duration_minutes == 25
    assert preferences.mobility_mode == "wheelchair"


@pytest.mark.asyncio
async def test_unavailable_model_falls_back_with_visible_reason(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise ai.LocalAIUnavailable("Ollama is not reachable")

    monkeypatch.setattr(ai, "_ollama", unavailable)
    preferences, source, reason = await ai.extract_preferences(Settings(), "25-minute wheelchair walk")
    assert source == "local_fallback"
    assert reason == "Ollama is not reachable"
    assert preferences.mobility_mode == "wheelchair"


@pytest.mark.asyncio
async def test_preference_endpoint_reports_source_without_returning_raw_output(client, monkeypatch):
    async def malformed(*_args, **_kwargs):
        return {"untrusted": "raw output must not be returned", "target_duration_minutes": 25}

    monkeypatch.setattr(ai, "_ollama", malformed)
    response = client.post("/api/preferences", json={"text": "25-minute wheelchair walk"})
    assert response.status_code == 200
    assert response.json()["source"] == "local_fallback"
    assert response.json()["preferences"]["target_duration_minutes"] == 25
    assert "untrusted" not in response.text
    assert response.json()["fallback_reason"]


def test_route_scoring_uses_only_duration_and_nearby_reports():
    feature = {"properties": {"summary": {"distance": 1000, "duration": 1500}},
               "geometry": {"type": "LineString", "coordinates": [[-73.0, 40.0], [-73.001, 40.0]]}}
    preferences = WalkPreferences(target_duration_minutes=25, avoid_stairs=True)
    near = {"latitude": 40.00001, "longitude": -73.0005, "barrier_type": "pothole", "severity": "high"}
    far = {"latitude": 41.0, "longitude": -73.0, "barrier_type": "stairs", "severity": "high"}
    score, notes = routing.score_route(feature, preferences, [near])
    assert score == 15
    assert any("Stairs: unknown" in note for note in notes)
    assert any("Slope: unknown" in note for note in notes)
    assert routing.report_is_near_route(near, feature["geometry"])
    assert not routing.report_is_near_route(far, feature["geometry"])


@pytest.mark.asyncio
async def test_ors_geojson_response_parsing_and_hei_git_url(monkeypatch):
    response = FakeResponse({"features": [{
        "geometry": {"type": "LineString", "coordinates": [[-73, 40], [-73.001, 40.001]]},
        "properties": {"summary": {"distance": 1100, "duration": 1500}, "segments": []},
    }]})
    mock_ors(monkeypatch, response=response)
    settings = Settings(ors_api_key="test-key", ors_base_url="https://api.heigit.org/openrouteservice/")
    result = await routing.build_routes(settings, route_request(), [])
    assert result.routes[0].distance_meters == 1100
    assert result.routes[0].duration_minutes == 25
    assert result.routes[0].stairs == "Unknown"
    assert result.recommended_index == 0
    url, kwargs = FakeOrsClient.call
    assert url == "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
    assert kwargs["headers"]["Authorization"] == "test-key"
    assert kwargs["json"]["coordinates"] == [[-73.0, 40.0], [-73.001, 40.001]]


@pytest.mark.asyncio
async def test_ors_missing_route_geometry_is_rejected(monkeypatch):
    mock_ors(monkeypatch, response=FakeResponse({"features": [{
        "properties": {"summary": {"distance": 100, "duration": 60}},
    }]}))
    with pytest.raises(routing.RoutingUnavailable, match="without usable line geometry") as error:
        await routing.build_routes(Settings(ors_api_key="test-key"), route_request(), [])
    assert error.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "message", "expected_status"), [
    (401, "rejected the API key", 502), (429, "rate limit", 503), (404, "No walking route", 422),
])
async def test_ors_status_errors_are_actionable(monkeypatch, status, message, expected_status):
    mock_ors(monkeypatch, response=FakeResponse(status_code=status))
    with pytest.raises(routing.RoutingUnavailable, match=message) as error:
        await routing.build_routes(Settings(ors_api_key="test-key"), route_request(), [])
    assert error.value.status_code == expected_status


@pytest.mark.asyncio
async def test_ors_timeout_is_reported(monkeypatch):
    mock_ors(monkeypatch, error=httpx.ReadTimeout("slow"))
    with pytest.raises(routing.RoutingUnavailable, match="too long") as error:
        await routing.build_routes(Settings(ors_api_key="test-key"), route_request(), [])
    assert error.value.status_code == 504


def test_routes_missing_key_has_actionable_error(client):
    app.dependency_overrides[get_settings] = lambda: Settings(ors_api_key="")
    response = client.post("/api/routes", json={
        "start": [-73, 40], "destination": [-73.001, 40.001],
        "preferences": {"target_duration_minutes": 25},
    })
    assert response.status_code == 503
    assert "ORS_API_KEY" in response.json()["detail"]
    app.dependency_overrides.pop(get_settings, None)


def test_barrier_response_validation_and_malformed_model_result(monkeypatch):
    result = BarrierAnalysis.model_validate({"barrier_type": "blocked_ramp", "severity": "high",
        "description": "A bin blocks the ramp.", "confidence": 0.91})
    assert result.confidence == 0.91
    with pytest.raises(ValidationError):
        BarrierAnalysis.model_validate({"barrier_type": "maybe", "severity": "high",
            "description": "unknown", "confidence": 1.4})


def test_analyze_image_endpoint_validates_and_handles_model_errors(client, monkeypatch):
    async def valid(*_args):
        return BarrierAnalysis(barrier_type="pothole", severity="medium",
                               description="A visible pothole.", confidence=0.8)

    monkeypatch.setattr("app.api.routes.analyze_image", valid)
    response = client.post("/api/analyze-barrier", files={"image": ("barrier.png", b"pixels", "image/png")})
    assert response.status_code == 200
    assert response.json()["barrier_type"] == "pothole"

    async def unavailable(*_args):
        raise ai.LocalAIUnavailable("Ollama is not reachable")

    monkeypatch.setattr("app.api.routes.analyze_image", unavailable)
    response = client.post("/api/analyze-barrier", files={"image": ("barrier.png", b"pixels", "image/png")})
    assert response.status_code == 503
    assert "Ollama is not reachable" in response.json()["detail"]


@pytest.mark.parametrize(("filename", "mime", "contents", "status"), [
    ("barrier.gif", "image/gif", b"gif", 415), ("empty.png", "image/png", b"", 400),
])
def test_analyze_image_rejects_unsupported_or_invalid_files(client, filename, mime, contents, status):
    response = client.post("/api/analyze-barrier", files={"image": (filename, contents, mime)})
    assert response.status_code == status


def test_analyze_image_rejects_oversized_files(client):
    response = client.post("/api/analyze-barrier", files={
        "image": ("large.png", b"x" * (8 * 1024 * 1024 + 1), "image/png"),
    })
    assert response.status_code == 413


def test_report_creation_retrieval_bbox_and_timezone(client):
    payload = {"latitude": 40.0, "longitude": -73.0, "barrier_type": "pothole", "severity": "medium",
               "description": "Visible pothole near the curb.", "confidence": 0.8}
    created = client.post("/api/reports", json=payload)
    assert created.status_code == 201
    report = created.json()
    assert report["id"] == 1
    assert datetime.fromisoformat(report["created_at"]).tzinfo is not None
    assert client.get("/api/reports").json()[0]["barrier_type"] == "pothole"
    assert client.get("/api/reports", params={"min_lon": -74, "min_lat": 39,
        "max_lon": -72, "max_lat": 41}).json()[0]["id"] == 1
    assert client.get("/api/reports", params={"min_lon": 10, "min_lat": 10,
        "max_lon": 11, "max_lat": 11}).json() == []
    assert client.get("/api/reports", params={"min_lon": 0}).status_code == 422


def test_database_report_failures_return_clean_http_errors(client, monkeypatch):
    def fail_commit(self):
        raise OperationalError("save", {}, RuntimeError("private db detail"))

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = client.post("/api/reports", json={
        "latitude": 40, "longitude": -73, "barrier_type": "pothole", "severity": "low",
        "description": "Visible hole", "confidence": 0.7,
    })
    assert response.status_code == 503
    assert "private db detail" not in response.text

    def fail_query(self, *args, **kwargs):
        raise OperationalError("read", {}, RuntimeError("private db detail"))

    monkeypatch.setattr(Session, "exec", fail_query)
    response = client.get("/api/reports")
    assert response.status_code == 503
    assert "private db detail" not in response.text


def test_route_endpoint_maps_upstream_errors_to_http_status(client, monkeypatch):
    async def timeout(*_args, **_kwargs):
        raise routing.RoutingUnavailable("OpenRouteService took too long to respond.", 504)

    monkeypatch.setattr("app.api.routes.build_routes", timeout)
    app.dependency_overrides[get_settings] = lambda: Settings(ors_api_key="test-key")
    response = client.post("/api/routes", json={
        "start": [-73, 40], "destination": [-73.001, 40.001],
        "preferences": {"target_duration_minutes": 25},
    })
    assert response.status_code == 504
    assert "too long" in response.json()["detail"]
    app.dependency_overrides.pop(get_settings, None)


def test_health_endpoint_does_not_expose_configuration_secrets(client):
    result = client.get("/health")
    assert result.status_code == 200
    assert result.json()["status"] == "ok"
    assert isinstance(result.json()["ollama_configured"], bool)
    assert "api_key" not in result.text.lower()
