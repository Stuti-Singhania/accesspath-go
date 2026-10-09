import type { Barrier, BarrierAnalysis, Preferences, RouteResponse, RouteSummary } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

function validatePreferencesResponse(value: unknown): { preferences: Preferences; source: string; fallback_reason: string | null } {
  if (!value || typeof value !== "object") throw new Error("The API returned an invalid preference response.");
  const result = value as Record<string, unknown>;
  const preferences = result.preferences as Record<string, unknown> | undefined;
  const booleans = ["avoid_stairs", "prefer_ramps", "avoid_steep_slopes", "avoid_unpaved"];
  if (!preferences || !["wheelchair", "walker", "cane", "none"].includes(String(preferences.mobility_mode)) ||
      booleans.some((field) => typeof preferences[field] !== "boolean") ||
      typeof preferences.max_slope !== "number" || preferences.max_slope < 0 || preferences.max_slope > 30 ||
      typeof preferences.target_duration_minutes !== "number" || !Number.isInteger(preferences.target_duration_minutes) ||
      preferences.target_duration_minutes < 5 || preferences.target_duration_minutes > 240 ||
      !["ollama", "local_fallback"].includes(String(result.source)) ||
      !(result.fallback_reason === null || typeof result.fallback_reason === "string")) {
    throw new Error("The API returned incomplete preferences. Please try again.");
  }
  return result as unknown as { preferences: Preferences; source: string; fallback_reason: string | null };
}

function validateRouteResponse(value: unknown): RouteResponse {
  if (!value || typeof value !== "object") throw new Error("The API returned an invalid route response.");
  const result = value as Record<string, unknown>;
  if (!Array.isArray(result.routes) || result.routes.length === 0 || typeof result.recommended_index !== "number") {
    throw new Error("No route was returned. Try choosing different points.");
  }
  const routes = result.routes as RouteSummary[];
  const valid = routes.every((route) => Number.isFinite(route.distance_meters) && route.distance_meters >= 0 &&
    Number.isFinite(route.duration_minutes) && route.duration_minutes >= 0 && Number.isFinite(route.score) &&
    Number.isInteger(route.known_barriers) && route.known_barriers >= 0 && typeof route.stairs === "string" &&
    Array.isArray(route.accessibility_notes) && route.accessibility_notes.every((note) => typeof note === "string") &&
    route.geometry?.type === "LineString" &&
    Array.isArray(route.geometry.coordinates) && route.geometry.coordinates.length >= 2 &&
    route.geometry.coordinates.every((point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)));
  if (!valid || result.recommended_index < 0 || result.recommended_index >= routes.length ||
      !Number.isInteger(result.recommended_index)) {
    throw new Error("The API returned incomplete route data. Please try again.");
  }
  return result as unknown as RouteResponse;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new Error("Can't reach AccessPath Go's API. Check that the backend is running.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item) => typeof item?.msg === "string" ? item.msg : "Invalid request").join(" ")
      : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return payload as T;
}

export const api = {
  preferences: async (text: string) => validatePreferencesResponse(await request<unknown>("/api/preferences", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
  })),
  routes: async (start: [number, number], destination: [number, number], preferences: Preferences) => {
    for (const [longitude, latitude] of [start, destination]) {
      if (!Number.isFinite(longitude) || !Number.isFinite(latitude) || Math.abs(longitude) > 180 || Math.abs(latitude) > 90) {
        throw new Error("Choose valid map coordinates for the start and destination.");
      }
    }
    return validateRouteResponse(await request<unknown>("/api/routes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start, destination, preferences }),
    }));
  },
  reports: (bounds?: [number, number, number, number]) => {
    const query = bounds ? `?min_lon=${bounds[0]}&min_lat=${bounds[1]}&max_lon=${bounds[2]}&max_lat=${bounds[3]}` : "";
    return request<Barrier[]>(`/api/reports${query}`);
  },
  analyze: async (file: File) => {
    const form = new FormData(); form.append("image", file);
    return request<BarrierAnalysis>("/api/analyze-barrier", { method: "POST", body: form });
  },
  createReport: (analysis: BarrierAnalysis, point: [number, number]) =>
    request<Barrier>("/api/reports", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...analysis, latitude: point[1], longitude: point[0] }),
    }),
};
