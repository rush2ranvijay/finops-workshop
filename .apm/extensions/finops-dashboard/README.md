# finops-dashboard — Copilot canvas

Wraps the **Demo 3** cost meter as a canvas you can open in the GitHub Copilot app:
top sessions by cost, most-used skills, most-used tools. **Calls are measured; per-skill/tool
cost is an explicit estimate** (proportional split of each session's measured cost).

This folder is the **source of truth**. It ships via [`apm`](https://github.com/microsoft/apm);
`apm install … --target copilot` deploys it verbatim into `.github/extensions/finops-dashboard/`.

## Install it (apm)

```bash
apm experimental enable canvas
apm install danielmeppiel/finops-workshop --target copilot --trust-canvas-extensions
```

Relaunch the Copilot app, then open the **FinOps cost dashboard** canvas.

## Data (stays on your machine, gitignored)

Generate the local snapshot:
```bash
cd demos/demo3-meter
python3 build_db.py && python3 export_dashboard.py
```
The extension reads, in order: `$FINOPS_DASHBOARD_DATA` → a `dashboard_data.json` next to
`extension.mjs` → `demos/demo3-meter/dashboard_data.json`. With **no snapshot** it renders a
friendly empty-state with next steps — never fabricated cost data. The `refresh` action re-reads
the snapshot from disk.

## Privacy
Renders only your **local** snapshot. No network calls. `dashboard_data.json` is gitignored and never pushed.
