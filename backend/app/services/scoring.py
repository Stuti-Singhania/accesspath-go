from itertools import pairwise
from math import cos, radians, sqrt

from app.schemas.accessibility import WalkPreferences

NEAR_ROUTE_METERS = 30.0
SEVERITY_PENALTY = {"low": 2.0, "medium": 6.0, "high": 15.0}


def _point_segment_distance_m(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Approximate local point-to-segment distance in meters for nearby coordinates."""
    lat_scale = 111_320.0
    lon_scale = lat_scale * cos(radians(point[1]))
    px, py = 0.0, 0.0
    ax, ay = (a[0] - point[0]) * lon_scale, (a[1] - point[1]) * lat_scale
    bx, by = (b[0] - point[0]) * lon_scale, (b[1] - point[1]) * lat_scale
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    t = 0.0 if denominator == 0 else max(0.0, min(1.0, -((ax - px) * dx + (ay - py) * dy) / denominator))
    return sqrt((ax + t * dx) ** 2 + (ay + t * dy) ** 2)


def report_is_near_route(report: dict, geometry: dict, radius_m: float = NEAR_ROUTE_METERS) -> bool:
    coordinates = geometry.get("coordinates", [])
    if not coordinates:
        return False
    point = (float(report["longitude"]), float(report["latitude"]))
    if len(coordinates) == 1:
        return _point_segment_distance_m(point, tuple(coordinates[0]), tuple(coordinates[0])) <= radius_m
    return any(
        _point_segment_distance_m(point, tuple(a), tuple(b)) <= radius_m
        for a, b in pairwise(coordinates)
    )


def score_route(feature: dict, preferences: WalkPreferences, nearby_barriers: list[dict]) -> tuple[float, list[str]]:
    """Lower is better; only ORS duration and confirmed nearby reports affect the score."""
    props = feature.get("properties", feature)
    summary = props.get("summary", {})
    duration = max(0.0, float(summary.get("duration", 0))) / 60
    distance = max(0.0, float(summary.get("distance", 0)))
    # Fixed, auditable duration deviation penalty; route characteristics are deliberately not guessed.
    score = round(abs(duration - preferences.target_duration_minutes) * 0.25, 2)
    notes = ["Stairs: unknown from available route data.", "Slope: unknown from available route data.",
             "Surface: unknown from available route data."]
    active_reports = [r for r in nearby_barriers if r.get("barrier_type") != "none"]
    score += sum(SEVERITY_PENALTY.get(r.get("severity", "medium"), 6.0) for r in active_reports)
    if active_reports:
        notes.append(f"{len(active_reports)} known barrier report(s) near this route were included in the score.")
    else:
        notes.append("No known barrier reports were found near this route; this does not mean it is barrier-free.")
    notes.append(f"Distance {distance:.0f} m; ORS estimated duration {duration:.0f} min.")
    return round(score, 2), notes
