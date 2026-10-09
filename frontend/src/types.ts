export type Preferences = {
  mobility_mode: "wheelchair" | "walker" | "cane" | "none";
  avoid_stairs: boolean;
  prefer_ramps: boolean;
  avoid_steep_slopes: boolean;
  avoid_unpaved: boolean;
  max_slope: number;
  target_duration_minutes: number;
};

export type PreferenceField = keyof Preferences;
export type PreferenceResponse = {
  preferences: Preferences;
  source: "ollama" | "local_fallback";
  fallback_reason: string | null;
  explicit_fields: PreferenceField[];
  suggested_fields: PreferenceField[];
};

export type Barrier = {
  id: number;
  latitude: number;
  longitude: number;
  barrier_type: string;
  severity: "low" | "medium" | "high";
  description: string;
  confidence: number;
  created_at: string;
};

export type BarrierAnalysis = Omit<Barrier, "id" | "latitude" | "longitude" | "created_at">;

export type RouteSummary = {
  distance_meters: number;
  duration_minutes: number;
  score: number;
  stairs: string;
  known_barriers: number;
  accessibility_notes: string[];
  geometry: { type: "LineString"; coordinates: [number, number][] };
};

export type RouteResponse = { routes: RouteSummary[]; recommended_index: number };
export type PickMode = "start" | "destination" | "report";
