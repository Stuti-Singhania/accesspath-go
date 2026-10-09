from math import isfinite

import httpx

from app.config import Settings
from app.schemas.accessibility import RouteRequest, RouteResponse, RouteSummary
from app.services.scoring import report_is_near_route, score_route


class RoutingUnavailable(Exception):
    def __init__(self, message: str, status_code: int = 503):
        self.status_code = status_code
        super().__init__(message)


async def build_routes(settings: Settings, request: RouteRequest, reports: list[dict]) -> RouteResponse:
    if not settings.ors_api_key:
        raise RoutingUnavailable("Set ORS_API_KEY in backend/.env to request walking routes.")
    body: dict = {
        "coordinates": [list(request.start), list(request.destination)],
        "instructions": True,
        "geometry": True,
        "instructions_format": "text",
        "alternative_routes": {"target_count": 3, "weight_factor": 1.6, "share_factor": 0.6},
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{settings.ors_base_url.rstrip('/')}/v2/directions/foot-walking/geojson",
                headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise RoutingUnavailable("OpenRouteService rejected the API key. Check ORS_API_KEY.", 502) from exc
        if exc.response.status_code == 429:
            raise RoutingUnavailable("OpenRouteService rate limit reached. Try again later.") from exc
        if exc.response.status_code in (400, 404):
            raise RoutingUnavailable("No walking route was found between these points. Try different locations.", 422) from exc
        raise RoutingUnavailable("OpenRouteService could not return a route. Check the locations and try again.", 502) from exc
    except httpx.TimeoutException as exc:
        raise RoutingUnavailable("OpenRouteService took too long to respond. Please try again.", 504) from exc
    except httpx.ConnectError as exc:
        raise RoutingUnavailable("OpenRouteService could not be reached. Check your internet connection.", 502) from exc
    except httpx.HTTPError as exc:
        raise RoutingUnavailable("OpenRouteService request failed. Please try again.", 502) from exc
    except ValueError as exc:
        raise RoutingUnavailable("OpenRouteService returned an invalid route response.", 502) from exc

    features = payload.get("features", []) if isinstance(payload, dict) else []
    if not isinstance(features, list) or not features:
        raise RoutingUnavailable("OpenRouteService returned no walking routes for these locations.", 422)

    routes: list[RouteSummary] = []
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString" or not isinstance(coordinates, list) or len(coordinates) < 2:
            raise RoutingUnavailable("OpenRouteService returned a route without usable line geometry.", 502)
        normalized_coordinates: list[list[float]] = []
        try:
            for point in coordinates:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    raise ValueError("coordinate must contain longitude and latitude")
                longitude, latitude = float(point[0]), float(point[1])
                if (
                    not isfinite(longitude) or not isfinite(latitude)
                    or not -180 <= longitude <= 180 or not -90 <= latitude <= 90
                ):
                    raise ValueError("coordinate is outside geographic bounds")
                normalized_coordinates.append([longitude, latitude])
        except (TypeError, ValueError) as exc:
            raise RoutingUnavailable("OpenRouteService returned invalid route coordinates.", 502) from exc
        geometry = {"type": "LineString", "coordinates": normalized_coordinates}
        props = feature.get("properties")
        summary = props.get("summary") if isinstance(props, dict) else None
        if not isinstance(summary, dict) or "distance" not in summary or "duration" not in summary:
            raise RoutingUnavailable("OpenRouteService returned a route without distance or duration.", 502)
        try:
            distance = float(summary["distance"])
            seconds = float(summary["duration"])
        except (TypeError, ValueError) as exc:
            raise RoutingUnavailable("OpenRouteService returned invalid route metrics.", 502) from exc
        if not isfinite(distance) or not isfinite(seconds) or distance < 0 or seconds < 0:
            raise RoutingUnavailable("OpenRouteService returned invalid route metrics.", 502)
        nearby = [r for r in reports if report_is_near_route(r, geometry)]
        score, notes = score_route(feature, request.preferences, nearby)
        duration = seconds / 60
        routes.append(RouteSummary(
            distance_meters=distance,
            duration_minutes=round(duration, 1),
            score=score,
            stairs="Unknown",
            known_barriers=len([r for r in nearby if r.get("barrier_type") != "none"]),
            accessibility_notes=notes,
            geometry=geometry,
        ))
    return RouteResponse(routes=routes, recommended_index=min(range(len(routes)), key=lambda i: routes[i].score))
