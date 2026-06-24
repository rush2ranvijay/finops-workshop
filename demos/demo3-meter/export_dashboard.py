#!/usr/bin/env python3
"""Export dashboard_data.json and static dashboard.html from finops.db."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

WORK = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORK, "finops.db")
JSON_PATH = os.path.join(WORK, "dashboard_data.json")
HTML_PATH = os.path.join(WORK, "dashboard.html")
COPILOT_HOME = os.path.expanduser(os.environ.get("COPILOT_HOME", "~/.copilot"))
SESSION_STORE_DB = os.path.join(COPILOT_HOME, "session-store.db")
DATA_DB = os.path.join(COPILOT_HOME, "data.db")


def rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _clean_title(val, limit=64):
    """Collapse whitespace and clip an over-long session summary to one tidy line.

    Some sessions store the entire first user message as their `summary` (a raw
    multi-line prompt rather than a curated title). Displaying that verbatim
    blows up table rows and can surface unrelated content in a live demo, so we
    flatten to a single line and clip to `limit` chars with an ellipsis.
    """
    s = " ".join(str(val).split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "\u2026"


def load_titles():
    """Best-available human title per session id (LOCAL telemetry, never committed).

    Precedence: session-store.db `sessions.summary` (curated title) >
    data.db `sessions.title` (first-message title) > short hash (added later in JS).
    """
    titles = {}
    # Lower precedence first so higher precedence overwrites.
    for path, table, col in ((DATA_DB, "sessions", "title"), (SESSION_STORE_DB, "sessions", "summary")):
        if not os.path.exists(path):
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            for sid, val in conn.execute(f"SELECT id, {col} FROM {table}"):
                if val and str(val).strip():
                    titles[sid] = _clean_title(val)
            conn.close()
        except sqlite3.Error:
            continue
    return titles


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    meta = {r["key"]: json.loads(r["value"]) for r in rows(conn, "SELECT key, value FROM etl_metadata")}
    top_sessions = rows(
        conn,
        """
        SELECT session_id, SUBSTR(start_time, 1, 10) AS start_date, models, total_tokens, aiu, usd, credits
        FROM sessions
        WHERE usd > 0
        ORDER BY usd DESC
        LIMIT 25
        """,
    )
    per_model = rows(
        conn,
        """
        SELECT model,
               SUM(responses) AS requests,
               SUM(input_tokens) AS input_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens,
               SUM(cache_write_tokens) AS cache_write_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(total_tokens) AS total_tokens,
               SUM(usd) AS usd,
               SUM(credits) AS credits
        FROM session_models
        GROUP BY model
        ORDER BY usd DESC, output_tokens DESC
        """,
    )
    top_tools = rows(
        conn,
        """
        SELECT st.tool_name AS tool_name,
               SUM(st.invocation_count) AS invocation_count,
               COUNT(DISTINCT st.session_id) AS sessions,
               SUM(COALESCE(s.usd, 0)) AS session_usd_touched,
               SUM(COALESCE(s.credits, 0)) AS session_credits_touched
        FROM session_tools st
        LEFT JOIN sessions s ON s.session_id = st.session_id
        WHERE st.tool_name NOT LIKE 'skill:%'
        GROUP BY st.tool_name
        ORDER BY invocation_count DESC
        LIMIT 25
        """,
    )
    top_skills = rows(
        conn,
        """
        WITH skill_counts AS (
            SELECT SUBSTR(tool_name, 7) AS skill_name,
                   SUM(invocation_count) AS invocation_count,
                   COUNT(DISTINCT session_id) AS sessions
            FROM session_tools
            WHERE tool_name LIKE 'skill:%'
            GROUP BY SUBSTR(tool_name, 7)
        ),
        window_costs AS (
            SELECT skill_name,
                   SUM(window_output_tokens) AS window_output_tokens,
                   SUM(window_input_tokens) AS window_input_tokens,
                   SUM(window_cache_read_tokens) AS window_cache_read_tokens,
                   SUM(window_cache_write_tokens) AS window_cache_write_tokens,
                   SUM(window_usd_est) AS window_usd_est,
                   SUM(window_credits_est) AS window_credits_est
            FROM session_skill_windows
            GROUP BY skill_name
        )
        SELECT sc.skill_name,
               sc.invocation_count,
               sc.sessions,
               COALESCE(wc.window_output_tokens, 0) AS window_output_tokens,
               COALESCE(wc.window_input_tokens, 0) AS window_input_tokens,
               COALESCE(wc.window_cache_read_tokens, 0) AS window_cache_read_tokens,
               COALESCE(wc.window_cache_write_tokens, 0) AS window_cache_write_tokens,
               COALESCE(wc.window_usd_est, 0) AS window_usd_est,
               COALESCE(wc.window_credits_est, 0) AS window_credits_est
        FROM skill_counts sc
        LEFT JOIN window_costs wc ON wc.skill_name = sc.skill_name
        ORDER BY window_usd_est DESC, invocation_count DESC
        LIMIT 25
        """,
    )
    summary = rows(
        conn,
        """
        SELECT COUNT(*) AS session_count, SUM(usd) AS usd, SUM(credits) AS credits,
               SUM(total_tokens) AS total_tokens, SUM(tool_call_count) AS tool_call_count
        FROM sessions
        """,
    )[0]

    # ---- Granular facts for client-side filtering (date / model / skill) ----
    # Per (session, model) metered rows, tagged with the session's calendar day.
    facts_session_model = rows(
        conn,
        """
        SELECT sm.session_id AS s, SUBSTR(se.start_time, 1, 10) AS d, sm.model AS m,
               sm.input_tokens AS i, sm.cache_read_tokens AS cr, sm.cache_write_tokens AS cw,
               sm.output_tokens AS o, sm.total_tokens AS t, sm.responses AS rq,
               sm.usd AS usd, sm.credits AS cred, sm.aiu AS aiu
        FROM session_models sm
        JOIN sessions se ON se.session_id = sm.session_id
        WHERE se.start_time IS NOT NULL AND se.start_time <> ''
        """,
    )
    # Per (skill, session, model) windowed rows: output tokens MEASURED, input/cache
    # and usd MODELED (apportioned from the metered model pool by output share).
    facts_skill = rows(
        conn,
        """
        SELECT w.skill_name AS sk, w.session_id AS s, SUBSTR(se.start_time, 1, 10) AS d, w.model AS m,
               SUM(w.window_output_tokens) AS o, SUM(w.window_input_tokens) AS i,
               SUM(w.window_cache_read_tokens) AS cr, SUM(w.window_cache_write_tokens) AS cw,
               SUM(w.window_usd_est) AS usd, SUM(w.window_credits_est) AS cred,
               SUM(w.invocation_count) AS c
        FROM session_skill_windows w
        JOIN sessions se ON se.session_id = w.session_id
        WHERE se.start_time IS NOT NULL AND se.start_time <> ''
        GROUP BY w.skill_name, w.session_id, SUBSTR(se.start_time, 1, 10), w.model
        """,
    )
    # Per (tool, session) rows: usage only (point tools are not windowed, no $).
    facts_tool = rows(
        conn,
        """
        SELECT st.tool_name AS tl, st.session_id AS s, SUBSTR(se.start_time, 1, 10) AS d,
               SUM(st.invocation_count) AS c
        FROM session_tools st
        JOIN sessions se ON se.session_id = st.session_id
        WHERE st.tool_name NOT LIKE 'skill:%'
          AND se.start_time IS NOT NULL AND se.start_time <> ''
        GROUP BY st.tool_name, st.session_id, SUBSTR(se.start_time, 1, 10)
        """,
    )
    models_list = [r["model"] for r in rows(conn, "SELECT DISTINCT model FROM session_models ORDER BY model")]
    skills_list = [r["sk"] for r in rows(conn, "SELECT DISTINCT skill_name AS sk FROM session_skill_windows ORDER BY skill_name")]
    daterange = rows(
        conn,
        "SELECT MIN(SUBSTR(start_time,1,10)) AS lo, MAX(SUBSTR(start_time,1,10)) AS hi FROM sessions WHERE start_time IS NOT NULL AND start_time <> ''",
    )[0]
    titles = load_titles()
    conn.close()

    # Intern session ids -> integer index, with a parallel [id, day, title] table,
    # so the 36-char UUID, the date, and the human title are stored once instead of
    # on every fact row. The title is the curated session summary (local only).
    sessions_tbl = []
    sid_index = {}

    def intern(sid, day):
        idx = sid_index.get(sid)
        if idx is None:
            idx = len(sessions_tbl)
            sid_index[sid] = idx
            sessions_tbl.append([sid, day, titles.get(sid, "")])
        return idx

    for r in facts_session_model:
        r["s"] = intern(r["s"], r.pop("d"))
    for r in facts_skill:
        r["s"] = intern(r["s"], r.pop("d"))
    for r in facts_tool:
        r["s"] = intern(r["s"], r.pop("d"))
    for r in top_sessions:
        r["title"] = titles.get(r.get("session_id"), "")
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method_note": "Session/model dollars are METERED. Skill window_output_tokens are MEASURED (assistant output while the skill is in context, summed per model incl. subagents on other models). Windows open at skill.invoked and close at the next HUMAN turn or session end (never at the next skill.invoked), so they may overlap. window_input/cache/usd/credits are MODELED: metered per-model session totals apportioned by the window's output share, denominator max(metered_output, sum_window_output) so the parts never exceed the metered whole. Tools are ranked by usage.",
        "summary": summary,
        "per_model": per_model,
        "top_sessions": top_sessions,
        "top_skills": top_skills,
        "top_tools": top_tools,
        "date_min": daterange.get("lo"),
        "date_max": daterange.get("hi"),
        "models": models_list,
        "skills": skills_list,
        "sessions_tbl": sessions_tbl,
        "facts_session_model": facts_session_model,
        "facts_skill": facts_skill,
        "facts_tool": facts_tool,
    }
    with open(JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    embedded = json.dumps(data)
    html = f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Demo 3 — Copilot Cost Telemetry</title>
<style>
:root {{ color-scheme: light; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bar:#2563eb; --bg:#f8fafc; --card:#fff; }}
body {{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--ink); }}
main {{ max-width:1180px; margin:0 auto; padding:28px; }}
h1 {{ margin:0 0 6px; font-size:28px; }}
.sub {{ color:var(--muted); margin-bottom:22px; }}
.cards {{ display:grid; grid-template-columns: repeat(5, minmax(130px,1fr)); gap:12px; margin-bottom:22px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
.label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
.value {{ font-weight:750; font-size:22px; margin-top:4px; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; margin:16px 0; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
h2 {{ margin:0 0 12px; font-size:18px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--muted); border-bottom:1px solid var(--line); padding:8px; font-weight:650; }}
td {{ border-bottom:1px solid #f1f5f9; padding:8px; vertical-align:middle; }}
.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
.sid {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
.barcell {{ min-width:140px; }}
.bar {{ height:8px; background:#dbeafe; border-radius:999px; overflow:hidden; }}
.fill {{ height:100%; background:linear-gradient(90deg,var(--bar),#60a5fa); }}
.note {{ color:var(--muted); font-size:12px; margin-top:10px; }}
@media (max-width:900px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} table {{ font-size:12px; }} }}
</style>
</head>
<body><main>
<h1>Demo 3 — MEASURE your own Copilot cost</h1>
<div class=\"sub\" id=\"generated\"></div>
<div class=\"cards\" id=\"cards\"></div>
<section><h2>Per-model totals</h2><div id=\"models\"></div><div class=\"note\"><b>Measured</b> — totals come from <code>session.shutdown.data.modelMetrics</code> / <code>session_models</code>.</div></section>
<section><h2>Top sessions by cost</h2><div id=\"sessions\"></div><div class=\"note\"><b>Measured</b> — session USD/credits come from real per-session model token telemetry (input/output/cache).</div></section>
<section><h2>Skill windows</h2><div id=\"skills\"></div><div class=\"note\"><b>Calls, sessions, and window output tokens are measured</b> from real <code>skill.invoked</code> and <code>assistant.message.outputTokens</code> events. <b>Window USD is an estimate</b>: each skill window runs from invocation to the next user message or next skill invocation, then receives that model's metered session USD by output-token share when measured window output fits the metered model pool. If not, dollars are left unmodeled rather than over-allocated. Per-message input/cache/cost are absent, so true per-skill dollars are not directly meterable.</div></section>
<section><h2>Most-used tools</h2><div id=\"tools\"></div><div class=\"note\">Tools are point operations, not token windows: calls/sessions are measured; <b>Session $ touched</b> is metered session cost where the tool ran, not per-tool attribution.</div></section>
</main>
<script>
const data = {embedded};
const fmtMoney = n => '$' + Number(n||0).toFixed(2);
const fmtCredits = n => Number(n||0).toLocaleString(undefined, {{maximumFractionDigits:1}});
const fmtInt = n => Number(n||0).toLocaleString();
const short = s => s ? s.slice(0,8) : '';
document.getElementById('generated').textContent = `Generated ${{data.generated_at}} from ${{data.summary.session_count}} local sessions`;
const cards = [
  ['Sessions', fmtInt(data.summary.session_count)],
  ['Total USD', fmtMoney(data.summary.usd)],
  ['Credits', fmtCredits(data.summary.credits)],
  ['Tokens', fmtInt(data.summary.total_tokens)],
  ['Tool calls', fmtInt(data.summary.tool_call_count)]
];
document.getElementById('cards').innerHTML = cards.map(([l,v]) => `<div class=card><div class=label>${{l}}</div><div class=value>${{v}}</div></div>`).join('');
function renderSessions() {{
 const max = Math.max(...data.top_sessions.map(r => r.usd||0), 1);
 return `<table><thead><tr><th>Session</th><th>Date</th><th>Model(s)</th><th class=num>Credits</th><th class=num>USD</th><th class=num>Tokens</th><th class=num>AIU</th><th>Cost bar</th></tr></thead><tbody>` +
 data.top_sessions.map(r => `<tr><td title="${{r.session_id}}">${{r.title?r.title:short(r.session_id)}}<div class=sid>${{short(r.session_id)}}</div></td><td>${{r.start_date||'—'}}</td><td>${{r.models||'—'}}</td><td class=num>${{fmtCredits(r.credits)}}</td><td class=num>${{fmtMoney(r.usd)}}</td><td class=num>${{fmtInt(r.total_tokens)}}</td><td class=num>${{r.aiu==null?'—':Number(r.aiu).toFixed(3)}}</td><td class=barcell><div class=bar><div class=fill style="width:${{100*(r.usd||0)/max}}%"></div></div></td></tr>`).join('') + `</tbody></table>`;
}}
function renderModels() {{
 if (!data.per_model || !data.per_model.length) return '<div class=note>No metered model rows recorded.</div>';
 return `<table><thead><tr><th>Model</th><th class=num>Requests</th><th class=num>Input</th><th class=num>Cache read</th><th class=num>Cache write</th><th class=num>Output</th><th class=num>Total tokens</th><th class=num>Credits</th><th class=num>USD</th></tr></thead><tbody>` +
 data.per_model.map(r => `<tr><td>${{r.model}}</td><td class=num>${{fmtInt(r.requests)}}</td><td class=num>${{fmtInt(r.input_tokens)}}</td><td class=num>${{fmtInt(r.cache_read_tokens)}}</td><td class=num>${{fmtInt(r.cache_write_tokens)}}</td><td class=num>${{fmtInt(r.output_tokens)}}</td><td class=num>${{fmtInt(r.total_tokens)}}</td><td class=num>${{fmtCredits(r.credits)}}</td><td class=num>${{fmtMoney(r.usd)}}</td></tr>`).join('') + `</tbody></table>`;
}}
function renderSkills() {{
 if (!data.top_skills || !data.top_skills.length) return '<div class=note>No skill invocations recorded.</div>';
 const max = Math.max(...data.top_skills.map(r => r.window_usd_est||0), 1);
 return `<table><thead><tr><th>Skill</th><th class=num>Calls</th><th class=num>Sessions</th><th class=num>Win input</th><th class=num>Win cache rd</th><th class=num>Win output</th><th class=num>Window USD est.</th><th>Est. cost bar</th></tr></thead><tbody>` +
 data.top_skills.map(r => `<tr><td>${{r.skill_name}}</td><td class=num>${{fmtInt(r.invocation_count)}}</td><td class=num>${{fmtInt(r.sessions)}}</td><td class=num>${{fmtInt(r.window_input_tokens)}}</td><td class=num>${{fmtInt(r.window_cache_read_tokens)}}</td><td class=num>${{fmtInt(r.window_output_tokens)}}</td><td class=num>${{fmtMoney(r.window_usd_est)}}</td><td class=barcell><div class=bar><div class=fill style="width:${{100*(r.window_usd_est||0)/max}}%"></div></div></td></tr>`).join('') + `</tbody></table>`;
}}
function renderTools() {{
 const max = Math.max(...data.top_tools.map(r => r.invocation_count||0), 1);
 return `<table><thead><tr><th>Tool</th><th class=num>Calls</th><th class=num>Sessions</th><th class=num>Session $ touched</th><th>Usage bar</th></tr></thead><tbody>` +
 data.top_tools.map(r => `<tr><td>${{r.tool_name}}</td><td class=num>${{fmtInt(r.invocation_count)}}</td><td class=num>${{fmtInt(r.sessions)}}</td><td class=num>${{fmtMoney(r.session_usd_touched)}}</td><td class=barcell><div class=bar><div class=fill style="width:${{100*(r.invocation_count||0)/max}}%"></div></div></td></tr>`).join('') + `</tbody></table>`;
}}
document.getElementById('models').innerHTML = renderModels();
document.getElementById('sessions').innerHTML = renderSessions();
document.getElementById('skills').innerHTML = renderSkills();
document.getElementById('tools').innerHTML = renderTools();
</script></body></html>
"""
    with open(HTML_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {JSON_PATH} and {HTML_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
