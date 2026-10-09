# AccessPath Go demo checklist

## Clean Windows startup

Prerequisites: Python 3.11+, Node.js 20+, Git, Ollama, and an OpenRouteService API key. From the repository root, copy `.env.example` to `.env` and add your own key locally. Do not share or commit `.env`.

In PowerShell, prepare and start the backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second PowerShell window, install Ollama if needed, pull the configured vision model, and start Ollama:

```powershell
ollama pull gemma3:4b
ollama serve
```

In a third PowerShell window, install frontend dependencies and start Next.js:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:3000`. The API docs are at `http://127.0.0.1:8000/docs`. For a later run, start the backend, Ollama, and frontend in separate terminals without repeating the install steps. Routing needs an internet connection and the local `.env` key. If PowerShell blocks `npm.ps1`, use the `npm.cmd` commands shown above.

## Walkthrough

1. Start the backend with `ORS_API_KEY` configured and Ollama running with the configured Gemma vision model.
2. Start the frontend and open `http://localhost:3000`.
3. Use the example wheelchair request. Confirm the structured preference values, including a 25-minute target and a 5% maximum slope.
4. Choose a start and a destination on the map (or use browser location for the start) and request the ORS route.
5. Show the route geometry, deterministic score, known report count and explicit unknown stairs/slope/surface values.
6. Open Touch Grass mode and start the walk to show the minimal outdoor screen.
7. Return to the planner, choose a real barrier photo and mark its actual location. Inspect the model's structured finding and confirm it before saving.

Use a real, non-identifying test photo and verify its description before submitting the report. Do not claim that a route is accessible based on the demo score.
