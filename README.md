# AccessPath Go

An accessibility-aware outdoor route companion for people with mobility challenges, including wheelchair users and people who use walkers or canes. Describe the walk you want, choose its start and destination, compare a walking route using available community reports, then put your phone away. You can also submit a photo of an outdoor barrier for local AI analysis and confirm it before sharing.

AccessPath Go is an early MVP. Route distance and duration come from OpenRouteService (ORS). Known barrier reports are matched to the route and scored with fixed rules. Stairs, slope and surface are explicitly **unknown** because the current routing response does not establish those properties. A route score is a comparison aid, not an accessibility or safety guarantee.

## Core features

- Natural-language request parsing with a local Gemma model, Pydantic validation and deterministic wheelchair defaults.
- OpenRouteService pedestrian alternatives, deterministically ranked by duration fit and nearby confirmed community reports.
- Leaflet map with route line, selected start/destination, and saved barrier report markers.
- Local multimodal Gemma analysis of a user-selected photo. A person reviews the finding and explicitly confirms before a report is stored.
- Minimal Touch Grass view with the selected route endpoints, distance, estimated time and known report count.

## Why Touch Grass?

The app is designed to make screen time the shortest part of going out: describe needs, choose two points, glance at a concise route summary and start the walk. The walk view removes the planning interface and encourages the user to put the phone away. There is no feed, account, tracking, or in-walk screen interaction.

## Architecture

```text
frontend/                         Next.js, React, TypeScript, Tailwind, Leaflet
  src/app/                        Accessible planning flow and Touch Grass view
  src/components/RouteMap.tsx     Client-only Leaflet map
backend/                          FastAPI, Pydantic, SQLModel, SQLite
  app/api/                         Preferences, routing, image analysis, reports
  app/models/                      SQLite report model
  app/schemas/                     Strict request/response validation
  app/services/                    Ollama, ORS, deterministic scoring
  tests/                           Mocked-service unit/API tests
data/                              Local SQLite file, created at startup
demo/                              Quick walkthrough
```

The browser sends preference text, coordinates and uploaded images to the local FastAPI service. That service sends inference requests to the configured local Ollama endpoint, asks ORS for pedestrian route alternatives, scores only returned duration and confirmed nearby reports, and stores user-confirmed reports in SQLite. Route decisions never come from an LLM.

## Local AI and open innovation

Gemma through Ollama turns a natural-language request into a strict `WalkPreferences` object. If Ollama is down for that endpoint, conservative keyword rules give the user a visibly labeled fallback. Barrier photos require a successful Ollama vision response; if the model is unavailable or returns invalid data, the endpoint reports an error rather than inventing a result. Pydantic validates both kinds of model output. Uploaded photos are not saved by the application.

Open-weight local inference lets people inspect and run the model without sending sensitive location context or outdoor photos to a hosted AI provider. OpenStreetMap-derived routing and community barrier reports let people inspect and improve the underlying map information. Routing still requires a network connection and an ORS key; this application does not claim to work offline.

## Technology

- Frontend: Next.js, React, TypeScript, Tailwind CSS, Leaflet and React Leaflet
- Backend: Python, FastAPI, Pydantic, SQLModel and SQLite
- AI: Gemma through the local Ollama HTTP API (text and multimodal image input)
- Routing and map data: HeiGIT OpenRouteService and OpenStreetMap

## Requirements

- Python 3.11 or later
- Node.js 20 or later and npm
- An [OpenRouteService API key](https://openrouteservice.org/dev/#/signup)
- [Ollama](https://ollama.com/) and a vision-capable Gemma model (for example `gemma3:4b`) for local AI features

## Backend setup

From the repository root, copy `.env.example` to `.env` and set your ORS key. The backend loads that root file when started from `backend/`; it also accepts a `backend/.env` for a backend-only setup. Keep both local files out of Git.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API starts at http://127.0.0.1:8000 and interactive docs are at http://127.0.0.1:8000/docs. SQLite tables and the `data/` directory are created automatically on startup. `ORS_API_KEY` is read by the backend only and is never returned by `/health`.

## Ollama setup

Install and start Ollama, then pull a multimodal Gemma model:

```sh
ollama pull gemma3:4b
```

Set `OLLAMA_MODEL` in root `.env` (or `backend/.env`) to the exact model tag shown by `ollama list`. The default Ollama endpoint is `http://localhost:11434`. The health response reports only whether a model endpoint is configured; it does not claim that Ollama is running or that inference succeeded.

## Frontend setup

In another terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open http://localhost:3000. Copy `frontend/.env.example` to `frontend/.env.local` to change `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`). Leaflet CSS is imported in the root layout and the map component is client-only to avoid server-side rendering browser map APIs.

The map supports clicks, browser geolocation, and keyboard-accessible latitude/longitude entry. Coordinates use `[longitude, latitude]` when sent to the backend. Browser geolocation needs permission; manual map/coordinate entry remains available if permission is denied.

## Demo flow

1. Keep the example request or write a mobility request. The preferences panel shows the validated fields and whether Gemma or the fallback parsed them.
2. Use **Use my location** or click the map to set a start. Select **Destination** and click the map again.
3. Choose **Find my route**. The recommended ORS alternative is drawn on the map; known community barriers are marked and included in its deterministic score.
4. Review the distance, ORS duration, low-is-better score, report count and explicit unknowns before opening Touch Grass mode.
5. In the barrier report section, choose an image, set its location on the map, analyze it locally, inspect the structured result and select **Confirm & share report** to save it.

The OpenStreetMap base layer and ORS require network access. Geolocation requires browser permission and a secure context (localhost is allowed). You can select both route points manually instead.

## API

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/health` | Basic health plus `ollama_configured`; no secrets or connectivity claims |
| POST | `/api/preferences` | Local Gemma preference extraction, validated with Pydantic, safe fallback when unavailable |
| POST | `/api/routes` | ORS walking alternatives, deterministic score and GeoJSON line geometry |
| POST | `/api/analyze-barrier` | Local multimodal Gemma; validated classification or clear 503 error |
| POST | `/api/reports` | Validate and persist a user-confirmed report |
| GET | `/api/reports` | List reports; optional `min_lon`, `min_lat`, `max_lon`, `max_lat` filter |

Route coordinates are `[longitude, latitude]`. Report coordinates are named latitude/longitude. Barrier penalties only use reports within approximately 30 metres of the returned route geometry. Duration deviation uses a fixed coefficient and the requested target. Lower scores rank ahead of higher scores; the score is not a probability or a measure of safety.

## Tests and build

From `backend/`:

```sh
pytest -q
```

The suite mocks both Ollama and ORS and needs no external keys or running services. From `frontend/`:

```sh
npm.cmd run build
```

Run these from their respective folders in PowerShell:

```powershell
# backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests

# frontend
npm.cmd run build
```

## Privacy and stored data

Preference text and images go from the browser to the configured local FastAPI/Ollama service; images are analyzed in memory and are not retained by this app. Start/destination coordinates are sent to the backend for ORS route requests. User-confirmed reports persist in local SQLite with latitude, longitude, barrier category, severity, description, confidence and UTC creation time. The application has no login, ad tracking, continuous location tracking or social feed. Protect the local database because reports can reveal places someone visits.

## Contributing and license

Focused contributions are welcome: open an issue for a bug or accessibility concern, then submit a small pull request with tests for backend behavior. Keep route claims evidence-based and do not add real keys, personal photos or private location data to commits. Run the backend test/lint commands and frontend build before submitting.

There is currently no `LICENSE` file in this workspace. A license should be selected and added before the project is presented as reusable open-source software.

## Known limitations

- Route distance/duration need ORS and network access. Missing `ORS_API_KEY`, rejected credentials, rate limits and empty routes produce actionable errors; the app does not fabricate a route.
- The score uses duration deviation and confirmed, nearby community barrier reports. The current version does not claim ORS route geometry proves stairs, slope, curb, ramp or surface accessibility; those remain unknown.
- Community reports are user-submitted and may be stale, misplaced or incorrect. An empty map does not mean a route is barrier-free.
- Preference extraction has a local fallback. Barrier analysis requires Ollama with a vision-capable model and fails clearly when it cannot run.
- Image uploads are size/type checked and analyzed in memory; there is no account system, location tracking, image storage or moderation workflow in this MVP.
- The app does not provide turn-by-turn navigation or guarantee a safe or accessible walk.
