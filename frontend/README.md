# Frontend — Ops-analyst UI

Vite + React + TypeScript. Three screens against the backend's `GET /cases`,
`GET /cases/{id}`, `POST /cases/{id}/resolve`, and `POST /transactions`:

- **Case Queue** (`src/pages/CaseQueue.tsx`) — every case, all three states, with severity and
  transaction context.
- **Case Detail** (`src/pages/CaseDetail.tsx`) — the four-step pipeline story for one case, plus
  the resolve action.
- **Simulate Transaction** (`src/pages/SimulateTransaction.tsx`) — submit a transaction against a
  seeded demo mandate and watch it move through the live pipeline.

See the [root README](../README.md) for the full project overview.

## Setup

```bash
npm install
cp .env.example .env   # VITE_API_BASE_URL / VITE_API_BEARER_TOKEN -- must match the backend's
                        # API_BEARER_TOKEN (backend/.env)
npm run dev
```

The backend must be running first (see `../backend/README.md`) and its CORS config
(`backend/app/main.py`) must include this dev server's origin — it already covers
`http://localhost:5173` and `http://127.0.0.1:5173` by default.

## Other commands

```bash
npm run build     # tsc -b && vite build
npm run lint       # oxlint
npm run preview    # preview a production build
```
