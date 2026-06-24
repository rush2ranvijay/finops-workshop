# finops-dashboard — Copilot canvas

Wraps the **Demo 3** cost meter as a canvas you can open in the GitHub Copilot app:
top sessions by cost, most-used skills, most-used tools. **Calls are measured; per-skill/tool
cost is an explicit estimate** (proportional split of each session's measured cost).

## Use it

1. Generate the local data snapshot (stays on your machine, gitignored):
   ```bash
   cd ../../../demos/demo3-meter
   python3 build_db.py && python3 export_dashboard.py
   ```
   The extension auto-reads `demos/demo3-meter/dashboard_data.json`. To point elsewhere,
   set `FINOPS_DASHBOARD_DATA=/path/to/dashboard_data.json`, or copy the file next to `extension.mjs`.
2. Load this folder as a Copilot extension (it's discovered under `.github/extensions/`), then open the
   **FinOps cost dashboard** canvas. The `refresh` action re-reads the snapshot from disk.

## Privacy
Renders only your **local** snapshot. No network calls. `dashboard_data.json` is gitignored and never pushed.
