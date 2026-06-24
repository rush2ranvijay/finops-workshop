# FinOps for Agentic Coding — Workshop

> **Your agentic bill is a variance you _engineer_, not a price you _negotiate_. The lever is the loop.**

A ~1-hour session for **AI Champions, platform leads, and admins** on controlling GitHub Copilot
agentic-coding cost. Not developer basics — this is about **cost control and the operating model**:
designing reusable, cost-effective agent workflows and governing the AI-credit budget around them.

Everything here is **self-contained and near-zero-build**: a minimalist HTML slide deck you present
from a browser, and one live demo that meters **your own** Copilot logs. No SaaS, no signup.

---

## TL;DR — what to do

| You want to… | Do this |
|---|---|
| **Present the deck** | Open `docs/index.html` in a browser (← / → to navigate, `F` to present fullscreen). |
| **Run the cost meter** | `cd demos/demo3-meter && python3 build_db.py && python3 export_dashboard.py && open dashboard.html` |
| **Show the meter as a Copilot canvas** | One-time: `apm experimental enable canvas`. Then `apm install danielmeppiel/finops-workshop --target copilot --trust-canvas-extensions`, relaunch Copilot, and open the **FinOps cost dashboard** canvas. |
| **Facilitate the room** | Read `docs/facilitator-guide.md`. |
| **Understand the narrative** | Read `deck/deck-spec.md` (the why) and `deck/deck-mockups.md` (slide-by-slide). |

---

## Repository map

| Path | What it is |
|---|---|
| `docs/` | The **built, presentable HTML deck** (`index.html` + `slides/`) — minimalist, 1280×720, offline. Plus `facilitator-guide.md`. |
| `deck/deck-spec.md` | Deck **source of truth**: the spine, the 3 acts, idea-slides + 3 demos, the exact metered hook numbers, and number discipline. |
| `deck/deck-mockups.md` | One minimalist ASCII mockup per slide — the design intent behind each built slide. |
| `demos/demo3-meter/` | **Demo 3 — MEASURE.** A stdlib-only meter that turns the Copilot logs you already produce into tokens → AI Units → credits → USD, with sessions-by-cost and skills-by-usage rankings. |
| `.apm/extensions/finops-dashboard/` | The Copilot **canvas** (source of truth) wrapping the Demo 3 dashboard. Shipped via `apm` — `apm install … --target copilot` deploys it into `.github/extensions/` (gitignored as a build artifact). Renders the local snapshot; data stays local. |

---

## Install the cost dashboard as a Copilot canvas (apm)

The dashboard ships as an [`apm`](https://github.com/microsoft/apm) package, so anyone can install it
into their own Copilot app — no manual file wiring.

```bash
# one-time: turn on apm's experimental canvas support
apm experimental enable canvas

# install this package's canvas into the current project (it's executable Node, hence --trust-canvas-extensions)
apm install danielmeppiel/finops-workshop --target copilot --trust-canvas-extensions
```

`apm` deploys the canvas to `.github/extensions/finops-dashboard/` (a generated, gitignored artifact).
Relaunch the Copilot app (extensions are discovered at session start) and open the **FinOps cost
dashboard** canvas. With no local snapshot yet it shows a friendly empty-state telling you to run
`build_db.py` + `export_dashboard.py` — it never shows fabricated cost data.

---



```
Hook(1) → Reframe(2) → Why-not-dev(3) +DEMO1 → Lifecycle loop(4)
        → Pools(5) → Tooling(6) +DEMO2 → Meter +DEMO3 → Playbook(7) → Exercise
```

**The three demos**
1. **DESIGN** — `genesis` designs a cost-aware agentic workflow: explore once at frontier cost, then codify the cheap, repeatable loop.
2. **GOVERN** — `apm` (Agent Package Manager) + a central catalog: a governed, reusable workflow pinned by manifest + lockfile and gated by policy.
3. **MEASURE** — the meter in `demos/demo3-meter/` reads **your own** logs. *“It’s already in your logs. No new SaaS.”*

**Tools shown:** GitHub Copilot App · GitHub Agentic Workflows · Agent Package Manager (`apm`).

---

## Number discipline (be honest)

Lead with the **metered**, replayable story, not a headline multiplier:

| Same high-value task | Cost | vs. right-sized |
|---|---|---|
| Right-sized loop (explore once → cheap loop) | **$4.81** | 1× |
| Same model, **bad loop** (re-explores every run) | **$33.79** | **7×** |
| **Premium default** (Opus everywhere) — and it still **failed** | **$41.01** | **8.5×** |

“18×” is only an **order-of-magnitude envelope** for naive fan-out — never present it as a single metered fact.
The dashboard mirrors this discipline: session cost is **measured**; per-skill/tool cost is an explicit **estimate** (see below).

---

## Privacy — your telemetry stays local

The meter reads your local `~/.copilot/logs` and `~/.copilot/session-state`. Generated artifacts
(`finops.db`, `dashboard_data.json`, `dashboard.html`, `EVIDENCE.md`) contain **your real session
costs and usage** and are **gitignored** — they are never committed or pushed. Regenerate them locally
with `build_db.py` + `export_dashboard.py`. The canvas extension renders only that local snapshot.

---

## Credits

Built on the *Agentic SDLC Bill* corpus (handbook ch. 7), a minimalist base slide system,
`genesis` (workflow design), `apm` (Agent Package Manager), and an agentic-SDLC workshop track.
