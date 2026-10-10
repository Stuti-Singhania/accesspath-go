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
        "avoid_steep_slopes": True, "avoid_unpaved": False, "max_slope": 5.0,
        "target_duration_minutes": 25,
    }
    explicit = ai.explicit_preference_fields(text)
    assert explicit == ["mobility_mode", "avoid_stairs", "avoid_steep_slopes", "target_duration_minutes"]


@pytest.mark.parametrize(("text", "expected"), [
    ("Wheelchair-friendly walk", ("wheelchair", True, True, True, False, 5.0)),
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


@pytest.mark.parametrize("text", ["Avoid unpaved paths", "Please avoid unpaved surfaces"])
def test_explicit_unpaved_avoidance_is_enabled_and_marked_explicit(text):
    result = ai.normalize_preferences(WalkPreferences(), text)
    assert result.avoid_unpaved is True
    assert "avoid_unpaved" in ai.explicit_preference_fields(text)


@pytest.mark.parametrize("text", [
    "Avoid stairs, steep slopes, and unpaved paths.",
    "Avoid stairs and steep slopes, and unpaved paths.",
    "Avoid unpaved paths.",
])
def test_preference_api_marks_coordinated_unpaved_avoidance_as_explicit(client, monkeypatch, text):
    async def model_defaults(*_args, **_kwargs):
        return WalkPreferences().model_dump()

    monkeypatch.setattr(ai, "_ollama", model_defaults)
    response = client.post("/api/preferences", json={"text": text})
    assert response.status_code == 200
    data = response.json()
    assert data["preferences"]["avoid_unpaved"] is True
    assert "avoid_unpaved" in data["explicit_fields"]
    assert "avoid_unpaved" not in data["suggested_fields"]


def test_explicit_preference_for_paved_paths_enables_unpaved_avoidance():
    result = ai.normalize_preferences(WalkPreferences(), "I prefer paved paths")
    assert result.avoid_unpaved is True
    assert "avoid_unpaved" in ai.explicit_preference_fields("I prefer paved paths")


def test_wheelchair_model_cannot_infer_unpaved_restriction():
    model_result = WalkPreferences(mobility_mode="wheelchair", avoid_unpaved=True)
    result = ai.normalize_preferences(model_result, "Wheelchair-friendly walk")
    assert result.avoid_unpaved is False


def test_explicit_mobility_mode_overrides_conflicting_model_value():
    model_result = WalkPreferences(mobility_mode="wheelchair", avoid_unpaved=True)
    result = ai.normalize_preferences(model_result, "I use a walker")
    assert result.mobility_mode == "walker"
    assert result.avoid_unpaved is False


def test_explicit_stairs_slope_and_duration_requests_are_preserved():
    text = "Wheelchair walk for 25 minutes; avoid stairs and steep slopes"
    result = ai.normalize_preferences(WalkPreferences(), text)
    assert result.avoid_stairs is True
    assert result.avoid_steep_slopes is True
    assert result.target_duration_minutes == 25
    assert {"avoid_stairs", "avoid_steep_slopes", "target_duration_minutes"}.issubset(
        ai.explicit_preference_fields(text)
    )


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
    assert preferences.avoid_unpaved is False


@pytest.mark.asyncio
async def test_model_unpaved_inference_is_overridden_and_metadata_is_deterministic(client, monkeypatch):
    async def inferred_unpaved(*_args, **_kwargs):
        return {**WHEELCHAIR_RESPONSE, "avoid_unpaved": True}

    monkeypatch.setattr(ai, "_ollama", inferred_unpaved)
    response = client.post("/api/preferences", json={
        "text": "I want a 25-minute wheelchair-friendly walk. Avoid stairs and steep slopes."
    })
    assert response.status_code == 200
    data = response.json()
    assert data["preferences"]["avoid_unpaved"] is False
    assert data["explicit_fields"] == [
        "mobility_mode", "avoid_stairs", "avoid_steep_slopes", "target_duration_minutes"
    ]
    assert "prefer_ramps" in data["suggested_fields"]
    assert "max_slope" in data["suggested_fields"]
    assert "avoid_unpaved" in data["suggested_fields"]


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
@pytest.mark.parametrize(("image", "expected_read_timeout", "expected_tokens"), [
    (None, 180, 320), (b"test image bytes", 600, 256),
])
async def test_ollama_uses_bounded_phase_timeouts_and_keeps_model_loaded(
    monkeypatch, image, expected_read_timeout, expected_tokens
):
    calls = {}

    class FakeOllamaClient:
        def __init__(self, *, timeout):
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json):
            calls["json"] = json
            return type("Response", (), {
                "raise_for_status": lambda _self: None,
                "json": lambda _self: {"response": '{"ok": true}'},
            })()

    monkeypatch.setattr(httpx, "AsyncClient", FakeOllamaClient)
    result = await ai._ollama(Settings(_env_file=None), "prompt", image)

    timeout = calls["timeout"]
    assert timeout.read == expected_read_timeout
    assert timeout.connect == 10
    assert timeout.write == 30
    assert timeout.pool == 10
    assert calls["json"]["keep_alive"] == "10m"
    assert calls["json"]["options"]["num_predict"] == expected_tokens
    assert ("images" in calls["json"]) is (image is not None)
    expected_format = BarrierAnalysis.model_json_schema() if image is not None else "json"
    assert calls["json"]["format"] == expected_format
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_image_inference_timeout_is_an_error_without_fallback(monkeypatch):
    class TimeoutClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            request = httpx.Request("POST", "http://localhost:11434/api/generate")
            raise httpx.ReadTimeout("slow multimodal inference", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)
    with pytest.raises(ai.LocalAIUnavailable, match="inference timed out"):
        await ai.analyze_image(Settings(_env_file=None), b"image bytes")


@pytest.mark.asyncio
async def test_invalid_barrier_response_logs_only_safe_validation_diagnostics(monkeypatch, caplog):
    async def invalid_response(*_args, **_kwargs):
        return {
            "barrier_type": "PRIVATE_MODEL_VALUE",
            "severity": "high",
            "description": "A visible barrier.",
            "confidence": 0.8,
        }

    monkeypatch.setattr(ai, "_ollama", invalid_response)
    with pytest.raises(ai.LocalAIUnavailable, match="required fields or value ranges"):
        await ai.analyze_image(Settings(_env_file=None), b"private image bytes")

    assert "barrier_type" in caplog.text
    assert "literal_error" in caplog.text
    assert "PRIVATE_MODEL_VALUE" not in caplog.text
    assert "private image bytes" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_result", "expected_type"),
    [
        ({"barrier_type": "steep_slope", "severity": "high",
          "description": "A steep grassy hillside with a narrow dirt trail is visible.",
          "confidence": 0.94}, "steep_slope"),
        ({"barrier_type": "other", "severity": "low",
          "description": "A stone wall is visible; whether it blocks the walking route is unclear.",
          "confidence": 0.31}, "other"),
        ({"barrier_type": "steep_slope", "severity": "medium",
          "description": "A steep-looking hillside trail is visible; its slope cannot be measured from this image.",
          "confidence": 0.58}, "steep_slope"),
        ({"barrier_type": "none", "severity": "low",
          "description": "The hillside is steep and the path is narrow and challenging.",
          "confidence": 0.42}, "steep_slope"),
        ({"barrier_type": "none", "severity": "low",
          "description": "A grassy path leads up a steep, green hillside with rocky outcrops. "
          "Dark clouds fill the sky. The hillside appears uneven and potentially challenging.",
          "confidence": 0.46}, "steep_slope"),
        ({"barrier_type": "none", "severity": "low",
          "description": "A path might be steep, but slope severity cannot be determined from this image.",
          "confidence": 0.22}, "none"),
        ({"barrier_type": "none", "severity": "low",
          "description": "A steep-looking hillside is visible, but whether it affects the walking route is unclear.",
          "confidence": 0.28}, "none"),
        ({"barrier_type": "obstruction", "severity": "high",
          "description": "A stone wall is visible, but whether it blocks the route is unclear.",
          "confidence": 0.4}, "none"),
        ({"barrier_type": "pothole", "severity": "high",
          "description": "A stone wall is visible; no hole is visible.",
          "confidence": 0.7}, "none"),
        ({"barrier_type": "none", "severity": "low",
          "description": "A level, clear paved path with no visible barriers.",
          "confidence": 0.96}, "none"),
    ],
    ids=[
        "steep-hillside", "ambiguous-wall", "unmeasured-slope",
        "hillside-none-contradiction", "exact-hillside-output", "uncertain-slope-severity",
        "hedged-hillside", "uncertain-wall-obstruction", "wall-not-pothole", "barrier-free",
    ],
)
async def test_image_analysis_grounding_regressions(monkeypatch, model_result, expected_type):
    calls = {}

    async def mocked_model(_settings, prompt, image):
        calls.update(prompt=prompt, image=image)
        return model_result

    monkeypatch.setattr(ai, "_ollama", mocked_model)
    result = await ai.analyze_image(Settings(_env_file=None), b"mock image")

    assert result.barrier_type == expected_type
    assert calls["image"] == b"mock image"
    assert "observations separate from interpretations" in calls["prompt"]
    assert "Never invent obstruction, measurements, dimensions" in calls["prompt"]
    assert "not a verified probability" in calls["prompt"]
    assert result.confidence == model_result["confidence"]
    if expected_type == "steep_slope" and model_result["barrier_type"] == "none":
        assert result.severity == "medium"
    if expected_type == "none" and model_result["barrier_type"] in {"obstruction", "pothole"}:
        assert result.severity == "low"


@pytest.mark.asyncio
async def test_preference_endpoint_reports_source_without_returning_raw_output(client, monkeypatch):
    async def malformed(*_args, **_kwargs):
        return {"untrusted": "raw output must not be returned", "target_duration_minutes": 25}

    monkeypatch.setattr(ai, "_ollama", malformed)
    response = client.post("/api/preferences", json={"text": "25-minute wheelchair walk"})
    assert response.status_code == 200
    assert response.json()["source"] == "local_fallback"
    assert response.json()["preferences"]["target_duration_minutes"] == 25
    assert response.json()["preferences"]["avoid_unpaved"] is False
    assert response.json()["explicit_fields"] == ["mobility_mode", "target_duration_minutes"]
    assert "avoid_unpaved" in response.json()["suggested_fields"]
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

    schema = BarrierAnalysis.model_json_schema()
    assert set(schema["properties"]) == {"barrier_type", "severity", "description", "confidence"}
    assert "not a verified probability" in schema["properties"]["confidence"]["description"]


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


def test_analyze_image_rejects_excessive_dimensions_without_calling_model(client, monkeypatch):
    import struct

    async def should_not_run(*_args):
        raise AssertionError("model must not receive oversized images")

    monkeypatch.setattr("app.api.routes.analyze_image", should_not_run)
    png_header = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 6000, 3000)
        + b"\x08\x02\x00\x00\x00"
    )
    response = client.post("/api/analyze-barrier", files={
        "image": ("large-dimensions.png", png_header, "image/png"),
    })
    assert response.status_code == 413
    assert "16 megapixels" in response.json()["detail"]


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
