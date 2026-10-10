import base64
import json
import logging
import re

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.accessibility import BarrierAnalysis, WalkPreferences


class LocalAIUnavailable(Exception):
    """A local model could not provide a usable, structured response."""


logger = logging.getLogger(__name__)


_DURATION = re.compile(
    r"\b(?P<value>\d{1,3}(?:\.\d+)?)\s*[- ]?\s*(?P<unit>minutes?|mins?|hours?|hrs?|h)\b",
    re.IGNORECASE,
)
_WHEELCHAIR = re.compile(r"\bwheel\s*chair(?:\s*[- ]?friendly)?\b", re.IGNORECASE)
_WALKER = re.compile(r"\bwalker\b", re.IGNORECASE)
_CANE = re.compile(r"\bcane\b", re.IGNORECASE)
_AVOID_STAIRS = re.compile(r"\b(?:avoid|without|no)\s+(?:any\s+)?stairs?\b|\bstair[- ]free\b", re.IGNORECASE)
_AVOID_SLOPE = re.compile(r"\b(?:avoid|without|no)\b[^.!?;\n]{0,60}\bsteep\s+(?:slopes?|hills?)\b", re.IGNORECASE)
_PREFER_RAMPS = re.compile(r"\bprefer\s+(?:a\s+)?ramps?\b", re.IGNORECASE)
_AVOID_UNPAVED = re.compile(r"\b(?:avoid|without|no)\s+unpaved(?:\s+(?:surfaces?|paths?))?\b", re.IGNORECASE)
_AVOID_UNPAVED_LIST = re.compile(
    r"\bavoid\b[^.!?;\n]{0,120}\bunpaved(?:\s+(?:surfaces?|paths?))?\b",
    re.IGNORECASE,
)
_PREFER_PAVED = re.compile(
    r"\b(?:prefer|want|use|stick to|only)\s+(?:a\s+)?paved(?:\s+(?:surfaces?|paths?))?\b"
    r"|\bpaved\s+(?:surfaces?|paths?)\b",
    re.IGNORECASE,
)
_UNPAVED_OKAY = re.compile(
    r"\b(?:unpaved|gravel|dirt)\s+(?:surfaces?|paths?)\s+(?:are\s+)?(?:okay|fine|acceptable)\b"
    r"|\bprefer\s+unpaved\b",
    re.IGNORECASE,
)
_STAIRS_OKAY = re.compile(r"\bstairs?\s+(?:are\s+)?(?:okay|fine|acceptable|allowed)\b", re.IGNORECASE)
_SLOPES_OKAY = re.compile(
    r"\b(?:steep\s+slopes?|hills?)\s+(?:are\s+)?(?:okay|fine|acceptable|allowed)\b", re.IGNORECASE
)
_RAMPS_NOT_PREFERRED = re.compile(r"\b(?:do not|don't|no need to)\s+(?:prefer|use|need)\s+ramps?\b", re.IGNORECASE)
_MAX_SLOPE = re.compile(
    r"\b(?:max(?:imum)?\s+slope(?:\s+of)?|slope\s+limit(?:\s+of)?|no more than)\s*(\d+(?:\.\d+)?)\s*%?"
    r"|\b(\d+(?:\.\d+)?)\s*%\s*(?:max(?:imum)?\s+)?slope\b",
    re.IGNORECASE,
)


def _duration_minutes(text: str) -> int | None:
    match = _DURATION.search(text)
    if not match:
        return None
    value = float(match.group("value"))
    unit = match.group("unit").lower()
    minutes = round(value * 60) if unit.startswith(("h",)) else round(value)
    return max(5, min(minutes, 240))


def _explicitly_avoids_unpaved(text: str) -> bool:
    return bool(_AVOID_UNPAVED.search(text) or _AVOID_UNPAVED_LIST.search(text))


def explicit_preference_fields(text: str) -> list[str]:
    """Return preference field names explicitly supported by deterministic text matching."""
    explicit: set[str] = set()
    if _WHEELCHAIR.search(text) or _WALKER.search(text) or _CANE.search(text):
        explicit.add("mobility_mode")
    if _AVOID_STAIRS.search(text) or _STAIRS_OKAY.search(text):
        explicit.add("avoid_stairs")
    if _AVOID_SLOPE.search(text) or _SLOPES_OKAY.search(text):
        explicit.add("avoid_steep_slopes")
    if _PREFER_RAMPS.search(text) or _RAMPS_NOT_PREFERRED.search(text):
        explicit.add("prefer_ramps")
    if _explicitly_avoids_unpaved(text) or _PREFER_PAVED.search(text) or _UNPAVED_OKAY.search(text):
        explicit.add("avoid_unpaved")
    if _MAX_SLOPE.search(text):
        explicit.add("max_slope")
    if _duration_minutes(text) is not None:
        explicit.add("target_duration_minutes")
    return [field for field in WalkPreferences.model_fields if field in explicit]


def normalize_preferences(preferences: WalkPreferences, text: str) -> WalkPreferences:
    """Combine validated model fields with stable mobility defaults and explicit user constraints."""
    values = preferences.model_dump()
    explicit_mode = (
        "wheelchair" if _WHEELCHAIR.search(text) else
        "walker" if _WALKER.search(text) else
        "cane" if _CANE.search(text) else None
    )
    if explicit_mode:
        values["mobility_mode"] = explicit_mode
    wheelchair = values["mobility_mode"] == "wheelchair"
    if wheelchair:
        values.update(
            mobility_mode="wheelchair",
            avoid_stairs=True,
            prefer_ramps=True,
            avoid_steep_slopes=True,
            max_slope=5,
        )
    if _AVOID_STAIRS.search(text):
        values["avoid_stairs"] = True
    elif _STAIRS_OKAY.search(text):
        values["avoid_stairs"] = False
    if _AVOID_SLOPE.search(text):
        values["avoid_steep_slopes"] = True
    elif _SLOPES_OKAY.search(text):
        values["avoid_steep_slopes"] = False
    if _PREFER_RAMPS.search(text):
        values["prefer_ramps"] = True
    elif _RAMPS_NOT_PREFERRED.search(text):
        values["prefer_ramps"] = False

    # This field is never inferred from Gemma: an explicit request enables it,
    # while no mention always leaves the optional restriction disabled.
    if _explicitly_avoids_unpaved(text) or _PREFER_PAVED.search(text):
        values["avoid_unpaved"] = True
    elif _UNPAVED_OKAY.search(text):
        values["avoid_unpaved"] = False
    else:
        values["avoid_unpaved"] = False

    slope_match = _MAX_SLOPE.search(text)
    if slope_match:
        explicit_slope = float(slope_match.group(1) or slope_match.group(2))
        values["max_slope"] = max(0.0, min(30.0, explicit_slope))

    explicit_duration = _duration_minutes(text)
    if explicit_duration is not None:
        values["target_duration_minutes"] = explicit_duration
    return WalkPreferences.model_validate(values)


def conservative_preferences(text: str) -> WalkPreferences:
    """Safe local fallback used only when local model inference cannot be used."""
    base = WalkPreferences()
    return normalize_preferences(base, text)


async def _ollama(settings: Settings, prompt: str, image: bytes | None = None) -> dict:
    content: dict = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "format": BarrierAnalysis.model_json_schema() if image is not None else "json",
        "keep_alive": "10m",
        "options": {"num_predict": 256 if image is not None else 320},
    }
    if image is not None:
        content["images"] = [base64.b64encode(image).decode("ascii")]
    read_timeout = (
        settings.ollama_image_read_timeout_seconds
        if image is not None
        else settings.ollama_text_read_timeout_seconds
    )
    # CPU-only multimodal inference can take several minutes. Keep connect/write/pool
    # waits bounded while allowing a longer response read for image requests.
    timeout = httpx.Timeout(read_timeout, connect=10, write=30, pool=10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{settings.ollama_base_url.rstrip('/')}/api/generate", json=content)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LocalAIUnavailable("Local Gemma inference timed out. Try again or check Ollama's status.") from exc
    except httpx.ConnectError as exc:
        raise LocalAIUnavailable("Ollama is not reachable at the configured local endpoint.") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise LocalAIUnavailable(f"Ollama model '{settings.ollama_model}' is unavailable. Pull it with ollama.") from exc
        raise LocalAIUnavailable("Ollama could not complete the inference request.") from exc
    except httpx.HTTPError as exc:
        raise LocalAIUnavailable("Ollama could not complete the inference request.") from exc

    try:
        payload = response.json()
        result = json.loads(payload["response"])
        if not isinstance(result, dict):
            raise TypeError("Model response must be a JSON object")
        return result
    except (KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LocalAIUnavailable("Ollama returned invalid JSON. Please retry the request.") from exc


async def extract_preferences(settings: Settings, text: str) -> tuple[WalkPreferences, str, str | None]:
    prompt = (
        "Convert the user's walking request to ONLY a JSON object with keys: mobility_mode "
        "(wheelchair|walker|cane|none), avoid_stairs, prefer_ramps, avoid_steep_slopes, "
        "avoid_unpaved (booleans), max_slope (number percent), target_duration_minutes (integer). "
        "Do not add keys. User request: " + text
    )
    try:
        parsed = await _ollama(settings, prompt)
    except LocalAIUnavailable as exc:
        return conservative_preferences(text), "local_fallback", str(exc)

    try:
        validated = WalkPreferences.model_validate(parsed)
        return normalize_preferences(validated, text), "ollama", None
    except ValidationError:
        return (
            conservative_preferences(text),
            "local_fallback",
            "Gemma returned preferences outside the supported format; the local fallback was used.",
        )


async def analyze_image(settings: Settings, image: bytes) -> BarrierAnalysis:
    prompt = (
        "Return exactly one JSON object matching the supplied schema and no other text. "
        "Describe only visual features actually present in the uploaded image. Keep direct "
        "observations separate from interpretations: name what is visible, then say when its "
        "effect on the walking route is uncertain. For example, a visible stone wall is not "
        "proof that the route is blocked; classify it as an obstruction only when the image "
        "clearly shows it blocking the route. A visible steep-looking hillside may be described "
        "as appearing steep, but do not infer an exact slope. Do not infer access needs, hidden "
        "conditions, route continuity, or that a route is accessible. Never invent obstruction, "
        "measurements, dimensions, surface defects, or other details not established visually. "
        "Use a cautious description and low confidence when evidence or its accessibility "
        "meaning is ambiguous; use barrier_type 'other' only for a clearly relevant visible "
        "barrier without a more specific category, and 'none' when no relevant barrier is "
        "visible. The category and description must agree. Confidence is the model's own "
        "rough estimate, not a verified probability; do not inflate it. Keep the description "
        "concise and within the schema's 500-character limit."
    )
    try:
        analysis = BarrierAnalysis.model_validate(await _ollama(settings, prompt, image))
        return _check_barrier_consistency(analysis)
    except ValidationError as exc:
        diagnostics = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "type": error["type"],
            }
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        logger.warning("Gemma barrier response failed validation: %s", diagnostics)
        raise LocalAIUnavailable(
            "Gemma's barrier result did not match the required fields or value ranges. "
            "No report was created. Try another image."
        ) from exc


def _check_barrier_consistency(analysis: BarrierAnalysis) -> BarrierAnalysis:
    """Resolve only explicit, multi-part contradictions in validated model output."""
    description = analysis.description.lower()

    # Require a direct steep-terrain assertion tied to the walking surface. Do not
    # let hedging about a separate feature (for example, unevenness) cancel it.
    uncertain_steepness = re.search(
        r"\b(?:might|may|could|possibly|potentially|appears?)\b[^.!?;\n]{0,35}\bsteep\b"
        r"|\bsteep[- ]looking\b"
        r"|\bslope\s+(?:severity|angle|degree)\b[^.!?;\n]{0,50}"
        r"\b(?:cannot|can't|could not|cannot be)\s+(?:be\s+)?determined\b",
        description,
    )
    uncertain_route_relevance = re.search(
        r"\b(?:whether|if)\b[^.!?;\n]{0,70}\b(?:affects?|matters?\s+for)\b"
        r"[^.!?;\n]{0,40}\b(?:walking\s+)?route\b[^.!?;\n]{0,35}"
        r"\b(?:unclear|uncertain|not\s+clear)\b",
        description,
    )
    describes_relevant_steep_terrain = (
        re.search(r"\bsteep(?:ly)?\b", description)
        and re.search(r"\b(?:hillside|slope|trail|path)\b", description)
        and re.search(r"\b(?:walking\s+route|path|trail)\b", description)
        and not uncertain_steepness
        and not uncertain_route_relevance
    )
    if analysis.barrier_type == "none" and describes_relevant_steep_terrain:
        updates = {"barrier_type": "steep_slope"}
        if analysis.severity == "low":
            updates["severity"] = "medium"
        return analysis.model_copy(update=updates)

    describes_wall = re.search(r"\b(?:stone|brick|rock)\s+wall\b", description)
    uncertain_blockage = re.search(
        r"\b(?:unclear|uncertain|not\s+clear|cannot\s+tell|cannot\s+determine)\b"
        r"[^.!?;\n]{0,100}\b(?:block|obstruct|route|path)\b"
        r"|\b(?:block|obstruct|route|path)\b[^.!?;\n]{0,100}"
        r"\b(?:unclear|uncertain|not\s+clear|cannot\s+tell|cannot\s+determine)\b",
        description,
    )
    explicitly_no_hole = re.search(
        r"\b(?:no|without|not\s+showing|does\s+not\s+show)\s+(?:visible\s+)?"
        r"(?:hole|pothole|cavity)\b",
        description,
    )
    if describes_wall and (
        (analysis.barrier_type == "obstruction" and uncertain_blockage)
        or (analysis.barrier_type == "pothole" and explicitly_no_hole)
    ):
        return analysis.model_copy(update={"barrier_type": "none", "severity": "low"})

    return analysis
