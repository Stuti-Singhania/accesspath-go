import base64
import json
import re

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.accessibility import BarrierAnalysis, WalkPreferences


class LocalAIUnavailable(Exception):
    """A local model could not provide a usable, structured response."""


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


def _duration_minutes(text: str) -> int | None:
    match = _DURATION.search(text)
    if not match:
        return None
    value = float(match.group("value"))
    unit = match.group("unit").lower()
    minutes = round(value * 60) if unit.startswith(("h",)) else round(value)
    return max(5, min(minutes, 240))


def normalize_preferences(preferences: WalkPreferences, text: str) -> WalkPreferences:
    """Combine validated model fields with stable mobility defaults and explicit user constraints."""
    values = preferences.model_dump()
    wheelchair = preferences.mobility_mode == "wheelchair" or bool(_WHEELCHAIR.search(text))
    if wheelchair:
        values.update(
            mobility_mode="wheelchair",
            avoid_stairs=True,
            prefer_ramps=True,
            avoid_steep_slopes=True,
            avoid_unpaved=True,
            max_slope=5,
        )
    elif preferences.mobility_mode == "none":
        if _WALKER.search(text):
            values["mobility_mode"] = "walker"
        elif _CANE.search(text):
            values["mobility_mode"] = "cane"

    if _AVOID_STAIRS.search(text):
        values["avoid_stairs"] = True
    if _AVOID_SLOPE.search(text):
        values["avoid_steep_slopes"] = True
    if _PREFER_RAMPS.search(text):
        values["prefer_ramps"] = True
    if _AVOID_UNPAVED.search(text):
        values["avoid_unpaved"] = True

    explicit_duration = _duration_minutes(text)
    if explicit_duration is not None:
        values["target_duration_minutes"] = explicit_duration
    return WalkPreferences.model_validate(values)


def conservative_preferences(text: str) -> WalkPreferences:
    """Safe local fallback used only when local model inference cannot be used."""
    base = WalkPreferences()
    return normalize_preferences(base, text)


async def _ollama(settings: Settings, prompt: str, image: bytes | None = None) -> dict:
    content: dict = {"model": settings.ollama_model, "prompt": prompt, "stream": False, "format": "json"}
    if image is not None:
        content["images"] = [base64.b64encode(image).decode("ascii")]
    try:
        async with httpx.AsyncClient(timeout=180) as client:
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
        "Inspect only visible evidence in this outdoor image. Do not infer hidden barriers. "
        "Return ONLY JSON with barrier_type (stairs|blocked_ramp|pothole|broken_sidewalk|"
        "steep_slope|narrow_path|mud|obstruction|inaccessible_entrance|none), severity "
        "(low|medium|high), concise description, confidence from 0 to 1. If uncertain or no "
        "barrier is visible, use none with low confidence."
    )
    try:
        return BarrierAnalysis.model_validate(await _ollama(settings, prompt, image))
    except ValidationError as exc:
        raise LocalAIUnavailable("Gemma returned an invalid barrier result. No report was created.") from exc
