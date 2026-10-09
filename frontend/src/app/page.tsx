"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Barrier, BarrierAnalysis, PickMode, PreferenceField, Preferences, RouteSummary } from "@/types";

const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <div className="map-loading">Loading the map…</div>,
});

const EXAMPLE = "I want a 25-minute wheelchair-friendly walk. Avoid stairs and steep slopes.";
function prettyDistance(meters: number) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
}

export default function Home() {
  const [requestText, setRequestText] = useState(EXAMPLE);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [explicitFields, setExplicitFields] = useState<PreferenceField[]>([]);
  const [suggestedFields, setSuggestedFields] = useState<PreferenceField[]>([]);
  const [preferenceSource, setPreferenceSource] = useState("");
  const [preferenceFallbackReason, setPreferenceFallbackReason] = useState("");
  const [start, setStart] = useState<[number, number] | null>(null);
  const [destination, setDestination] = useState<[number, number] | null>(null);
  const [reportPoint, setReportPoint] = useState<[number, number] | null>(null);
  const [pickMode, setPickMode] = useState<PickMode>("start");
  const [route, setRoute] = useState<RouteSummary | null>(null);
  const [reports, setReports] = useState<Barrier[]>([]);
  const [reportsLoading, setReportsLoading] = useState(true);
  const [reportsError, setReportsError] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [isWalkOpen, setIsWalkOpen] = useState(false);
  const [walkStarted, setWalkStarted] = useState(false);
  const [image, setImage] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<BarrierAnalysis | null>(null);
  const touchGrassTriggerRef = useRef<HTMLButtonElement>(null);
  const walkActionRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    api.reports().then((loadedReports) => {
      setReports(loadedReports);
      setReportsError("");
    }).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : "The report service did not return a response.";
      setReportsError(`Community reports could not be loaded: ${detail}`);
    }).finally(() => setReportsLoading(false));
  }, []);

  useEffect(() => {
    if (!isWalkOpen) return;
    walkActionRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsWalkOpen(false);
        window.requestAnimationFrame(() => touchGrassTriggerRef.current?.focus());
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isWalkOpen]);

  const choosePoint = (mode: PickMode, point: [number, number]) => {
    if (mode === "start") { setStart(point); setPickMode("destination"); setRoute(null); }
    else if (mode === "destination") { setDestination(point); setPickMode("start"); setRoute(null); }
    else setReportPoint(point);
  };

  const locateMe = () => {
    setMessage("");
    if (!navigator.geolocation) { setMessage("Location isn't available in this browser. Click the map to choose a start point."); return; }
    setBusy("location");
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => { setStart([coords.longitude, coords.latitude]); setPickMode("destination"); setRoute(null); setMessage(""); setBusy(""); },
      () => { setMessage("Couldn't get your location. You can choose a start point on the map instead."); setBusy(""); },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  };

  const updatePreference = <K extends keyof Preferences,>(key: K, value: Preferences[K]) => {
    setPreferences((current) => current ? { ...current, [key]: value } : current);
    setRoute(null);
  };

  const preferenceOrigin = (field: PreferenceField) =>
    explicitFields.includes(field) ? "From your request" : "Suggested default";

  const extract = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy("preferences"); setMessage(""); setPreferences(null); setPreferenceFallbackReason(""); setRoute(null);
    try {
      const result = await api.preferences(requestText);
      setPreferences(result.preferences);
      setExplicitFields(result.explicit_fields);
      setSuggestedFields(result.suggested_fields);
      setPreferenceSource(result.source); setPreferenceFallbackReason(result.fallback_reason ?? "");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Couldn't interpret the request."); }
    finally { setBusy(""); }
  };

  const plan = async () => {
    if (!start || !destination || !preferences) return;
    setBusy("route"); setMessage(""); setRoute(null);
    try {
      const result = await api.routes(start, destination, preferences);
      const chosen = result.routes[result.recommended_index];
      if (!chosen) throw new Error("No route was returned. Try choosing nearby points.");
      setRoute(chosen);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Couldn't generate a route."); }
    finally { setBusy(""); }
  };

  const analyze = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!image) { setMessage("Choose an image first."); return; }
    if (!("image/jpeg image/png image/webp").split(" ").includes(image.type)) { setMessage("Choose a JPEG, PNG or WebP image."); return; }
    if (image.size > 8 * 1024 * 1024) { setMessage("Image must be 8 MB or smaller."); return; }
    setBusy("analysis"); setMessage(""); setAnalysis(null);
    try { setAnalysis(await api.analyze(image)); }
    catch (error) {
      const detail = error instanceof Error ? error.message : "The local model did not return a result.";
      setMessage(`Barrier image analysis failed: ${detail}`);
    }
    finally { setBusy(""); }
  };

  const saveReport = async () => {
    if (!analysis || !reportPoint) { setMessage("Choose the barrier location on the map before saving."); return; }
    setBusy("report"); setMessage("");
    try {
      const saved = await api.createReport(analysis, reportPoint);
      setReports((current) => [saved, ...current]); setReportsError(""); setAnalysis(null); setImage(null); setReportPoint(null);
      const input = document.getElementById("barrier-image") as HTMLInputElement | null;
      if (input) input.value = "";
      setMessage("Report saved. Thanks for making the next walk easier.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Couldn't save the report."); }
    finally { setBusy(""); }
  };

  const mapStart = start ? `${start[1].toFixed(5)}, ${start[0].toFixed(5)}` : "Choose on map";
  const mapDestination = destination ? `${destination[1].toFixed(5)}, ${destination[0].toFixed(5)}` : "Choose on map";
  const editPoint = pickMode === "start" ? start : pickMode === "destination" ? destination : reportPoint;
  const closeWalk = () => {
    setIsWalkOpen(false);
    window.requestAnimationFrame(() => touchGrassTriggerRef.current?.focus());
  };

  return (
    <main>
      <header className="topbar"><a className="brand" href="#top" aria-label="AccessPath Go home"><span className="brand-mark">↗</span> accesspath<span>go</span></a><a className="quiet-link" href="#how-it-works">A little planning, then outside <span aria-hidden="true">↘</span></a></header>

      <section className="hero" id="top">
        <div className="hero-copy"><p className="eyebrow"><span className="status-dot" /> MADE FOR THE WAY OUT</p>
          <h1>Go farther.<br /><em>Look at your phone less.</em></h1>
          <p className="hero-subtitle">Plan an outdoor route around the accessibility needs that matter to you.</p>
        </div>
        <div className="hero-art" aria-hidden="true"><div className="sun" /><svg viewBox="0 0 500 280" role="presentation"><path d="M-10 244 C90 180 114 244 194 193 S330 222 510 86" fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray="7 10"/><path d="M0 270 L94 200 155 243 248 153 304 209 387 106 500 164" fill="none" stroke="currentColor" strokeWidth="2" opacity=".36"/><circle cx="194" cy="193" r="7" fill="currentColor"/><circle cx="387" cy="106" r="7" fill="currentColor"/></svg><span>the best part is out there</span></div>
      </section>

      <div className="page-content">
        <section className="step-section" aria-labelledby="request-title">
          <div className="section-heading"><span className="step-number">01</span><div><p className="eyebrow">TELL US WHAT WORKS FOR YOU</p><h2 id="request-title">Your walk, your needs.</h2></div></div>
          <form className="request-form" onSubmit={extract}>
            <label className="sr-only" htmlFor="walk-request">Describe the walk you want</label>
            <textarea id="walk-request" value={requestText} onChange={(event) => setRequestText(event.target.value)} rows={3} maxLength={1000} placeholder="Describe the walk you have in mind…" />
            <div className="form-bottom"><span className="privacy-note"><span aria-hidden="true">◉</span> Your request stays on this device when local AI is available.</span><button className="button button-dark" type="submit" disabled={busy === "preferences"}>{busy === "preferences" ? "Reading your request…" : "Set my preferences"}<span aria-hidden="true">↗</span></button></div>
          </form>
          {preferences && <section className="preferences-panel" aria-labelledby="preferences-review-title">
            <div className="panel-title"><span aria-hidden="true">✓</span><div>
              <strong id="preferences-review-title">Here’s what we understood</strong>
              <small>{preferenceSource === "ollama" ? "Parsed with local Gemma" : "Handled by the local fallback parser"}</small>
            </div></div>
            <h3>Review and adjust before finding your route</h3>
            <p className="preference-hint">Suggestions are clearly marked and can be changed any time before routing.</p>
            <div className="preference-controls">
              <div className="preference-control">
                <div className="preference-control-copy"><label htmlFor="preference-mobility">Mobility mode</label><small>{preferenceOrigin("mobility_mode")}</small></div>
                <select id="preference-mobility" value={preferences.mobility_mode} onChange={(event) => updatePreference("mobility_mode", event.target.value as Preferences["mobility_mode"])}>
                  <option value="wheelchair">Wheelchair</option><option value="walker">Walker</option><option value="cane">Cane</option><option value="none">None</option>
                </select>
              </div>
              {([
                ["avoid_stairs", "Avoid stairs"],
                ["prefer_ramps", "Prefer ramps"],
                ["avoid_steep_slopes", "Avoid steep slopes"],
                ["avoid_unpaved", "Avoid unpaved surfaces"],
              ] as const).map(([field, label]) => <label className="preference-check" key={field}>
                <input type="checkbox" checked={preferences[field]} onChange={(event) => updatePreference(field, event.target.checked)} />
                <span className="preference-check-copy"><strong>{label}</strong><small>{preferenceOrigin(field)}</small></span>
              </label>)}
              <div className="preference-control">
                <div className="preference-control-copy"><label htmlFor="preference-slope">Maximum slope</label><small>{preferenceOrigin("max_slope")}</small></div>
                <div className="number-with-unit"><input id="preference-slope" type="number" min="0" max="30" step="0.5" value={preferences.max_slope} onChange={(event) => { const value = Number(event.target.value); if (event.target.value !== "" && value >= 0 && value <= 30) updatePreference("max_slope", value); }} /><span>%</span></div>
              </div>
              <div className="preference-control">
                <div className="preference-control-copy"><label htmlFor="preference-duration">Target duration</label><small>{preferenceOrigin("target_duration_minutes")}</small></div>
                <div className="number-with-unit"><input id="preference-duration" type="number" min="5" max="240" step="1" value={preferences.target_duration_minutes} onChange={(event) => { const value = Number(event.target.value); if (event.target.value !== "" && Number.isInteger(value) && value >= 5 && value <= 240) updatePreference("target_duration_minutes", value); }} /><span>minutes</span></div>
              </div>
            </div>
            {preferences.mobility_mode === "wheelchair" && !preferences.avoid_unpaved && <p className="unpaved-suggestion">For wheelchair comfort, you may prefer paved surfaces. This option stays off unless you choose it.</p>}
            {suggestedFields.length > 0 && <p className="preference-hint">Suggested options reflect common defaults, not requirements. Change any of them to suit this walk.</p>}
            {preferenceFallbackReason && <p className="fallback-note" role="status">{preferenceFallbackReason}</p>}
          </section>}
        </section>

        <section className="step-section map-section" id="how-it-works" aria-labelledby="route-title">
          <div className="section-heading"><span className="step-number">02</span><div><p className="eyebrow">PICK YOUR WAY</p><h2 id="route-title">Choose two points on the map.</h2></div></div>
          <div className="map-tools"><div className="point-fields"><button type="button" className={`point-field ${pickMode === "start" ? "selected" : ""}`} onClick={() => setPickMode("start")}><span className="point-dot start-dot" /> <span><small>START</small><strong>{mapStart}</strong></span></button><button type="button" className={`point-field ${pickMode === "destination" ? "selected" : ""}`} onClick={() => setPickMode("destination")}><span className="point-dot end-dot" /> <span><small>DESTINATION</small><strong>{mapDestination}</strong></span></button></div><button type="button" className="button button-outline locate-button" onClick={locateMe} disabled={busy === "location"}><span aria-hidden="true">⊙</span> {busy === "location" ? "Finding you…" : "Use my location"}</button></div>
          <div className="map-frame"><RouteMap mode={pickMode} start={start} destination={destination} reportPoint={reportPoint} route={route} reports={reports} onPick={choosePoint} /></div>
          <details className="coordinate-entry"><summary>Enter a point using your keyboard</summary><form key={pickMode} onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const latText = String(data.get("latitude") ?? "").trim(); const lonText = String(data.get("longitude") ?? "").trim(); const lat = Number(latText); const lon = Number(lonText); if (!latText || !lonText || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) { setMessage("Enter a valid latitude and longitude."); return; } setMessage(""); choosePoint(pickMode, [lon, lat]); }}><p>Latitude and longitude are sent to the routing service as longitude, latitude.</p><label>Latitude<input name="latitude" type="number" min="-90" max="90" step="any" required defaultValue={editPoint?.[1] ?? ""} /></label><label>Longitude<input name="longitude" type="number" min="-180" max="180" step="any" required defaultValue={editPoint?.[0] ?? ""} /></label><button className="button button-outline" type="submit">Set {pickMode} point</button></form></details>
          <div className="map-caption"><span><b className="legend-dot start-dot" /> Start <b className="legend-dot end-dot" /> Destination <b className="legend-dot barrier-dot" /> Community report</span><span>Click the map to set the selected point</span></div>
          <div className="route-actions"><button className="button button-dark" onClick={plan} disabled={!preferences || !start || !destination || busy === "route"}>{busy === "route" ? "Finding a walking route…" : "Find my route"}<span aria-hidden="true">↗</span></button><span className="route-prereq">{!preferences ? "Set your preferences first" : !start || !destination ? "Choose a start and destination" : "Walking route by OpenRouteService"}</span></div>
          {route && preferences && <div className="route-result" aria-live="polite"><div className="result-heading"><span className="result-icon">↗</span><div><p className="eyebrow">ROUTE OPTION · LOWEST SCORE</p><h3>Your route is ready to review.</h3></div></div><div className="route-metrics"><div><small>DISTANCE</small><strong>{prettyDistance(route.distance_meters)}</strong></div><div><small>EST. DURATION</small><strong>{Math.round(route.duration_minutes)} min</strong></div><div><small>ACCESSIBILITY SCORE</small><strong>{route.score.toFixed(2)} <span className="score-hint">lower is better</span></strong></div><div><small>KNOWN BARRIERS NEAR ROUTE</small><strong>{route.known_barriers}</strong></div></div><p className="data-limit"><span aria-hidden="true">ⓘ</span> Stairs, slope and surface are unknown from available data. A low score does not guarantee an accessible route.</p><button ref={touchGrassTriggerRef} className="text-button" onClick={() => { setWalkStarted(false); setIsWalkOpen(true); }}>Open Touch Grass mode <span aria-hidden="true">→</span></button></div>}
        </section>

        <section className="step-section report-section" aria-labelledby="report-title">
          <div className="section-heading"><span className="step-number">03</span><div><p className="eyebrow">LEAVE THE PATH A LITTLE BETTER</p><h2 id="report-title">Spot a barrier? Mark it for others.</h2></div></div>
          <div className="report-layout"><div className="report-intro"><p>Share a photo of a step, blocked ramp, rough path or other obstacle. Local Gemma will describe only what it can see. You decide whether to share it.</p><button type="button" className={`point-field report-location ${pickMode === "report" ? "selected" : ""}`} onClick={() => setPickMode("report")}><span className="point-dot report-dot" /><span><small>REPORT LOCATION</small><strong>{reportPoint ? `${reportPoint[1].toFixed(5)}, ${reportPoint[0].toFixed(5)}` : "Choose on map"}</strong></span><span aria-hidden="true">⌖</span></button><small className="location-help">Select this, then click where the barrier is on the map.</small></div>
            <div className="upload-panel"><form onSubmit={analyze}><label className="upload-label" htmlFor="barrier-image"><span className="upload-symbol" aria-hidden="true">＋</span><span><strong>{image ? image.name : "Choose a photo of the barrier"}</strong><small>JPG, PNG or WebP · up to 8 MB · analyzed locally</small></span><input id="barrier-image" type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { setImage(event.target.files?.[0] ?? null); setAnalysis(null); }} /></label><button type="submit" className="button button-outline" disabled={busy === "analysis" || !image}>{busy === "analysis" ? "Checking the image…" : "Analyze photo"}</button></form>
              {analysis && <div className="analysis-result" aria-live="polite"><p className="eyebrow">PLEASE CHECK BEFORE SHARING</p><strong>{analysis.barrier_type.replaceAll("_", " ")} <span className={`severity severity-${analysis.severity}`}>{analysis.severity} severity</span></strong><p>{analysis.description}</p><small>Model confidence: {Math.round(analysis.confidence * 100)}% · Confirm that this description matches the photo.</small><button className="button button-dark" onClick={saveReport} disabled={busy === "report" || !reportPoint}>{busy === "report" ? "Saving…" : "Confirm & share report"}<span aria-hidden="true">↗</span></button>{!reportPoint && <small className="location-help">Choose the report location on the map before sharing.</small>}</div>}
            </div></div>
          <p className="report-count" role="status">{reportsLoading ? "Loading community reports…" : reportsError || `${reports.length} community ${reports.length === 1 ? "report" : "reports"} currently visible on the map.`}</p>
        </section>

        {message && <p className="notice" role="status">{message}<button type="button" aria-label="Dismiss message" onClick={() => setMessage("")}>×</button></p>}
        <footer><span className="brand"><span className="brand-mark">↗</span> accesspath<span>go</span></span><span>Thoughtful routes. More time outside.</span><small>Route information is based on available data and may be incomplete.</small></footer>
      </div>

      {isWalkOpen && route && <div className="walk-overlay" role="dialog" aria-modal="true" aria-labelledby="walk-title"><button className="walk-close" onClick={closeWalk} aria-label="Close walk view">×</button><div className="walk-content"><p className="eyebrow">ACCESSPATH GO · TOUCH GRASS MODE</p><div className="walk-sun" aria-hidden="true">✳</div><h2 id="walk-title">{walkStarted ? "The outdoors is yours now." : "Your route is ready."}</h2><p className="walk-subtitle">{walkStarted ? "Your screen can wait. Take in the path at your own pace." : "A quick look, then let the screen rest."}</p><p className="walk-route-summary">{mapStart} <span aria-hidden="true">→</span> {mapDestination}</p><div className="walk-stats"><div><small>DISTANCE</small><strong>{prettyDistance(route.distance_meters)}</strong></div><div><small>EST. TIME</small><strong>{Math.round(route.duration_minutes)} min</strong></div><div><small>KNOWN BARRIERS</small><strong>{route.known_barriers}</strong></div></div>{!walkStarted && <p className="walk-note">Stairs, slope and surface are unknown. Check the route conditions that matter to you before you set out.</p>}<button ref={walkActionRef} className="button walk-button" onClick={() => walkStarted ? closeWalk() : setWalkStarted(true)}>{walkStarted ? "Finish walk" : "Start walk"}<span aria-hidden="true">→</span></button><p className="phone-away">{walkStarted ? "Take a breath. Put your phone away when you’re ready." : "Once you start, the screen gets out of your way."}</p></div></div>}
    </main>
  );
}
