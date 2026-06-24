# Demo 3 — MEASURE your own Copilot cost

A local, **stdlib-only** FinOps meter over GitHub Copilot logs and session-state.
It derives **tokens → AI Units → credits → USD** for every session you've run, then
ranks **sessions by cost** and **skills/tools by usage** — proving the point of the
workshop: *the bill is already in your logs; you don't need a new SaaS to see it.*

> **Privacy:** this reads your **local** `~/.copilot` data and writes everything locally.
> Generated artifacts (`finops.db`, `dashboard_data.json`, `dashboard.html`, `EVIDENCE.md`)
> contain your real session costs and are **gitignored** — they never get committed or pushed.

## Run it (<2 min, no dependencies)

```bash
cd demos/demo3-meter
python3 -m unittest test_finops_core.py -v     # sanity-check the math (synthetic fixtures)
python3 build_db.py                            # build finops.db from ~/.copilot
python3 export_dashboard.py                    # emit dashboard_data.json + dashboard.html
open dashboard.html                            # or wrap it as a Copilot canvas
```

Override the data locations if needed:

```bash
COPILOT_LOG_DIR=~/.copilot/logs \
COPILOT_SESSION_STATE_DIR=~/.copilot/session-state \
python3 build_db.py
```

## Data sources

- **Primary:** `~/.copilot/session-state/<id>/events.jsonl` → `session.shutdown.data.modelMetrics`
  (most reliable; carries per-model token usage, `totalNanoAiu`, premium requests).
- **Fallback:** `~/.copilot/logs/*.log` → `copilot_usage.token_details[]` blocks (for live/no-shutdown sessions).

Real-world log filenames are mixed (`process-*`, `github-app.*`, `session-<uuid>.log`, bare `<uuid>.log`),
so the loader scans `*.log` rather than assuming a single pattern.

## What it computes

Rates (per Mtok, list): Opus 15/75 · Sonnet 3/15 · Haiku 1/5; cache-read ≈ 0.10× input, cache-write ≈ 1.25× input.
`1 AIU = 1e9 nano_aiu`; `1 credit = $0.01` (default, configurable).

## Schema (`finops.db`)

- `sessions(session_id, source, models, *_tokens, nano_aiu, aiu, usd, credits, premium_requests, tool_call_count, start_time, end_time)`
- `session_models(session_id, model, *_tokens, nano_aiu, aiu, usd, credits, premium_requests)` — **measured/metered** per-model totals.
- `session_tools(session_id, tool_name, invocation_count, attributed_*_tokens, attributed_usd, attributed_credits, attribution_method)` — invocation counts for tools and skills; legacy proportional attribution is retained only for reference.
- `session_skill_windows(session_id, window_index, skill_name, model, window_start_time, window_end_time, window_input_tokens, window_cache_read_tokens, window_cache_write_tokens, window_output_tokens, denominator_output_tokens, model_session_usd, window_usd_est, window_credits_est, invocation_count)` — skill windows with the full per-model token breakdown and modeled dollar estimates.
- `etl_metadata(key, value)`

Skills are recorded distinctly from built-in tools (e.g. `skill:<name>` vs `bash`/`view`/`edit`),
so you can rank skill usage separately.

## Dashboard JSON shape (`dashboard_data.json`)

```json
{
  "generated_at": "...",
  "method_note": "Session/model dollars are METERED; skill window_output_tokens are MEASURED (per model, including subagent activity on other models); window input/cache_read/cache_write and window_usd_est are MODELED by apportioning each model's metered session totals by the window's share of that model's output; tools are ranked by usage.",
  "summary": {"session_count": 0, "usd": 0.0, "credits": 0.0, "total_tokens": 0, "tool_call_count": 0},
  "per_model": [{"model": "...", "requests": 0, "input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usd": 0.0, "credits": 0.0}],
  "top_sessions": [{"session_id": "...", "title": "...", "start_date": "YYYY-MM-DD", "models": "...", "total_tokens": 0, "aiu": null, "usd": 0.0, "credits": 0.0}],
  "top_skills": [{"skill_name": "...", "invocation_count": 0, "sessions": 0, "window_input_tokens": 0, "window_cache_read_tokens": 0, "window_cache_write_tokens": 0, "window_output_tokens": 0, "window_usd_est": 0.0, "window_credits_est": 0.0}],
  "top_tools":  [{"tool_name": "...",  "invocation_count": 0, "sessions": 0, "session_usd_touched": 0.0, "session_credits_touched": 0.0}],

  "date_min": "YYYY-MM-DD", "date_max": "YYYY-MM-DD",
  "models": ["..."], "skills": ["..."],
  "sessions_tbl": [["<session_id>", "YYYY-MM-DD", "<human title or empty>"]],
  "facts_session_model": [{"s": 0, "m": "...", "i": 0, "cr": 0, "cw": 0, "o": 0, "t": 0, "rq": 0, "usd": 0.0, "cred": 0.0, "aiu": null}],
  "facts_skill": [{"sk": "...", "s": 0, "m": "...", "o": 0, "i": 0, "cr": 0, "cw": 0, "usd": 0.0, "cred": 0.0, "c": 0}],
  "facts_tool": [{"tl": "...", "s": 0, "c": 0}]
}
```

The `top_*` blocks are the convenience all-time view. The `facts_*` arrays are the
granular, INTERNED rows that power client-side filtering: `s` is an index into
`sessions_tbl` (which holds the session id, its calendar day, and its human title,
stored once), `m` is the model, `sk`/`tl` the skill/tool. `facts_skill` carries the
full per-model window breakdown (`o` output / `i` input / `cr` cache-read / `cw`
cache-write / `usd` / `cred` / `c` invocation count). The canvas filters by date /
model / skill and re-aggregates these facts in the browser — so a clone with no
server can still slice the data, render the per-session inspector (click a row in
Top Sessions), and share filter state via `?from=&to=&model=&skill=` URL params.

For tools, `session_usd_touched` = `SUM(session.usd)` over the distinct sessions where that tool appeared.
It is a **metered** number (real session cost), but it is **not** cost caused by the tool.

For skills, `window_output_tokens` is measured exactly by walking the event timeline:
each `skill.invoked` window starts at the invocation timestamp and ends at the **earliest**
of — the next `skill.invoked`, the next **human** `user.message`, or session end. A newly
invoked skill **overtakes** the prior one (it is typically loaded by it), so windows do
**not** overlap. Synthetic skill-context injections — `user.message` events whose
`data.source` is `skill-<name>`, `instruction-discovery`, `autopilot`, etc. — do **not**
close a window (closing on those is what truncated real windows to zero). All assistant
output inside the window, including subagent activity on **other** models that does not
itself emit a `skill.invoked`, is counted per model. Output is summed per
`assistant.message.outputTokens`, keyed by `assistant.message.data.model` (when a single
model pool exists, untagged messages fall to it; in multi-model sessions untagged output
is bucketed as `unknown` and carries no modeled dollars). The previous implementation
treated synthetic injections as human turns — which truncated any window followed
immediately by a skill-context injection to **zero** output even though the skill clearly
ran. Because windows partition the assistant stream, summed window output never exceeds the
metered session output. The per-model **input/cache/usd/credits** are apportioned with a
denominator of `max(metered_output_pool, Σ window output)` (a safety cap) so the sum of
window shares for a model can never exceed its metered session total. A skill that is
immediately overtaken by another with no assistant turn in between is an empty window by
design — the second skill captured the work.

## Canvas handoff

`dashboard.html` is self-contained (no network deps) and can be wrapped directly as a Copilot canvas.
The shipped canvas (source in `.apm/extensions/finops-dashboard`, installed via `apm install … --target
copilot` which deploys it to `.github/extensions/finops-dashboard`) goes further: it serves the page from a
loopback server and renders an **interactive, filterable** view — from/to date, model, and skill (loop)
filters that recompute cost-by-model, top sessions, windowed skills, and tools entirely client-side from
the `facts_*` arrays. Skills show the full **window input / cache-read / output** breakdown plus modeled
window `$`. Top Sessions show the **human title** (hash as secondary/tooltip) and each row is **clickable**
to open a per-session inspector (per-model metered breakdown, skill windows with per-model tokens + `$`, and
tool counts) rendered entirely from the embedded JSON. It also surfaces operating-model KPIs (avg $/session,
premium-tier $ share, top-5-skill concentration) that map directly to the workshop plays: **tier models**,
**pool the spend**, and **codify the top loops**. Tools are shown by usage only (no per-tool $). Filter state
is shareable via `?from=&to=&model=&skill=` URL params.

## Honest limitation (read this before quoting any number)

- **Metered (trust as fact):** per-session and per-model `$`/credits/tokens, from `session.shutdown` model telemetry.
- **Measured (trust as fact):** skill/tool **invocation counts**, distinct **sessions**, per-model totals, and skill
  per-model `window_output_tokens` from real `skill.invoked`, tool, and `assistant.message.outputTokens` events
  (including subagent output on other models). Because skill windows do not overlap, the summed
  window-output column never exceeds the session output total.
- **Modeled (do not call measured):** skill `window_input_tokens` / `window_cache_read_tokens` /
  `window_cache_write_tokens` / `window_usd_est` / `window_credits_est`. Events do **not** expose per-message input
  tokens, cache tokens, or cost. Each is apportioned from that model's metered session total by the window's share of
  the model's output, using a denominator of `max(metered_output_pool, Σ window output)` (a safety cap).
  Output that is untagged-by-model in a multi-model session is bucketed as
  `unknown` and carries **no** modeled dollars rather than being guessed onto a model.
- **NOT derivable:** truthful **per-skill / per-tool dollars**. Tool events are point operations with no token spans,
  so tools stay ranked by invocation count and distinct sessions, plus `session_usd_touched` as a concentration
  signal only.

The DB still carries legacy `session_tools.attributed_*` columns (count-proportional estimate) for reference;
the dashboard no longer uses them for skill dollars. See `VALIDATION.md` for hand-checks and the attribution audit.
