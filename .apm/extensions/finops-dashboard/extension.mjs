// Extension: finops-dashboard
// Renders a local, FILTERABLE FinOps telemetry dashboard (cost-by-model,
// sessions-by-cost, skills-by-windowed-cost, tools-by-usage) from a bundled
// dashboard_data.json snapshot produced by demos/demo3-meter/export_dashboard.py.
// Filters (from/to date, model, skill) recompute every table client-side from
// granular fact arrays; they can also be driven via URL params for shareable
// views. Session/model $ are METERED; skill window output tokens are MEASURED;
// windowed skill $ is MODELED. Tools are point ops: usage only, no $.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";

const HERE = dirname(fileURLToPath(import.meta.url));
// Resolve the local, gitignored telemetry snapshot. Precedence:
//   1. $FINOPS_DASHBOARD_DATA  2. ./dashboard_data.json (copied next to the extension)
//   3. the demo's generated output under demos/demo3-meter/
const DATA_CANDIDATES = [
    process.env.FINOPS_DASHBOARD_DATA,
    join(HERE, "dashboard_data.json"),
    join(HERE, "..", "..", "..", "demos", "demo3-meter", "dashboard_data.json"),
].filter(Boolean);

function resolveDataPath() {
    for (const p of DATA_CANDIDATES) {
        if (existsSync(p)) return p;
    }
    return DATA_CANDIDATES[DATA_CANDIDATES.length - 1];
}

const servers = new Map(); // instanceId -> { server, url }

async function loadData() {
    const path = resolveDataPath();
    if (!existsSync(path)) {
        throw new Error(
            "No dashboard_data.json found. Generate it: cd demos/demo3-meter && python3 build_db.py && python3 export_dashboard.py"
        );
    }
    const raw = await readFile(path, "utf-8");
    return JSON.parse(raw);
}

// ---- raw session-log reader (per-session events.jsonl) ------------------
// Local OTel-style event stream lives at ~/.copilot/session-state/<sid>/events.jsonl.
// We never serve it verbatim: lines can be ~1MB (a single assistant message), so we
// flatten each event to a compact, truncated record the modal can render quickly.
const STATE_DIR = join(homedir(), ".copilot", "session-state");
const STR_CAP = 1200;      // clip any single string field
const EVENT_CAP = 6000;    // clip a whole event's pretty JSON
const MAX_EVENTS = 6000;   // hard cap on events returned

function truncStrings(o, max) {
    if (typeof o === "string") return o.length > max ? o.slice(0, max) + "… [" + (o.length - max) + " more chars]" : o;
    if (Array.isArray(o)) return o.map((x) => truncStrings(x, max));
    if (o && typeof o === "object") { const r = {}; for (const k of Object.keys(o)) r[k] = truncStrings(o[k], max); return r; }
    return o;
}

function oneLine(s, n) { return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n); }

function summarize(type, d) {
    d = d || {};
    if (type === "skill.invoked") return [d.name, d.source ? "via " + d.source : "", d.trigger || ""].filter(Boolean).join("  ·  ");
    if (type === "tool.execution_start" || type === "tool.execution_complete") return [d.toolName, d.model].filter(Boolean).join("  ·  ");
    if (type === "assistant.message") return [d.model, d.outputTokens != null ? d.outputTokens + " out" : "", oneLine(d.content, 90)].filter(Boolean).join("  ·  ");
    if (type === "user.message") return [d.source ? "source=" + d.source : "human", oneLine(d.content || d.transformedContent, 110)].filter(Boolean).join("  ·  ");
    if (type === "subagent.started" || type === "subagent.completed") return d.agentDisplayName || d.agentName || "";
    if (type === "session.compaction_start") return ["sys " + (d.systemTokens || 0), "conv " + (d.conversationTokens || 0)].join("  ·  ");
    const k = ["name", "toolName", "agentName", "model", "source", "message"].find((x) => d[x] != null);
    return k ? oneLine(d[k], 110) : "";
}

async function buildLogs(sid) {
    const file = join(STATE_DIR, sid, "events.jsonl");
    if (!existsSync(file)) return { error: "no_events", message: "No events.jsonl for this session (live or already pruned)." };
    const raw = await readFile(file, "utf-8");
    const lines = raw.split("\n");
    const events = [];
    let total = 0;
    const typeCounts = {};
    for (const ln of lines) {
        if (!ln.trim()) continue;
        total++;
        let o;
        try { o = JSON.parse(ln); } catch { continue; }
        const type = o.type || o.event || "?";
        typeCounts[type] = (typeCounts[type] || 0) + 1;
        if (events.length >= MAX_EVENTS) continue;
        const ts = typeof o.timestamp === "string" ? o.timestamp : "";
        const time = ts.length >= 19 ? ts.slice(11, 19) : "";
        let text = JSON.stringify(truncStrings(o, STR_CAP), null, 2);
        if (text.length > EVENT_CAP) text = text.slice(0, EVENT_CAP) + "\n… [event truncated]";
        events.push({ time, date: ts.slice(0, 10), type, summary: summarize(type, o.data), text });
    }
    return { session: sid, total, returned: events.length, truncated: total > events.length, typeCounts, events };
}

function renderHtml(data) {
    const json = JSON.stringify(data);
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>FinOps — Copilot cost telemetry</title>
<style>
  :root { --bar: var(--true-color-blue, #2563eb); }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--background-color-default, #0d1117);
    color: var(--text-color-default, #e6edf3);
    font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif);
    font-size: var(--text-body-medium, 14px);
    line-height: var(--leading-body-medium, 20px);
  }
  main { max-width: 1100px; margin: 0 auto; padding: 22px 26px 48px; }
  h1 { font-size: var(--text-title-large, 24px); font-weight: var(--font-weight-semibold, 600); margin: 0 0 4px; }
  .sub { color: var(--text-color-muted, #8b949e); font-size: 12.5px; margin-bottom: 12px; }
  .filters { display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; background: var(--background-color-muted,#161b22); border:1px solid var(--border-color-default,#30363d); border-radius:12px; padding:12px 14px; margin-bottom:16px; }
  .filters label { display:flex; flex-direction:column; gap:4px; font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--text-color-muted,#8b949e); }
  .filters input, .filters select { background: var(--background-color-default,#0d1117); color: var(--text-color-default,#e6edf3); border:1px solid var(--border-color-default,#30363d); border-radius:8px; padding:6px 9px; font-size:13px; font-family:inherit; min-width:140px; }
  .filters button { background: var(--background-color-default,#0d1117); color: var(--text-color-default,#e6edf3); border:1px solid var(--border-color-default,#30363d); border-radius:8px; padding:7px 13px; font-size:12.5px; cursor:pointer; }
  .filters button:hover { border-color: var(--bar); }
  .frange { margin-left:auto; font-size:11.5px; color:var(--text-color-muted,#8b949e); text-transform:none; letter-spacing:0; align-self:center; }
  .legend { display:flex; gap:14px; align-items:center; font-size:12px; color: var(--text-color-muted,#8b949e); margin: 0 0 16px; flex-wrap:wrap; }
  .pill { display:inline-flex; align-items:center; gap:6px; padding:3px 9px; border:1px solid var(--border-color-default,#30363d); border-radius:999px; }
  .pill .d { width:8px; height:8px; border-radius:2px; }
  .meas { background: var(--true-color-green-muted, rgba(35,134,54,.18)); }
  .est  { background: var(--true-color-yellow-muted, rgba(187,128,9,.18)); }
  .cards { display:grid; grid-template-columns: repeat(6, minmax(110px,1fr)); gap:10px; margin-bottom:20px; }
  .card { background: var(--background-color-muted, #161b22); border:1px solid var(--border-color-default,#30363d); border-radius:12px; padding:13px 14px; }
  .card.accent { border-color: color-mix(in srgb, var(--bar) 55%, var(--border-color-default,#30363d)); }
  .label { color: var(--text-color-muted,#8b949e); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
  .value { font-weight:700; font-size:20px; margin-top:5px; font-variant-numeric: tabular-nums; }
  .value small { font-size:11px; font-weight:500; color:var(--text-color-muted,#8b949e); }
  section { background: var(--background-color-muted, #161b22); border:1px solid var(--border-color-default,#30363d); border-radius:12px; padding:14px 16px; margin:14px 0; }
  h2 { font-size:15px; margin:0 0 4px; display:flex; align-items:center; gap:9px; }
  .tag { font-size:10px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; padding:2px 7px; border-radius:6px; }
  .tag.m { background: var(--true-color-green-muted, rgba(35,134,54,.22)); color: var(--true-color-green, #3fb950); }
  .tag.e { background: var(--true-color-yellow-muted, rgba(187,128,9,.20)); color: var(--true-color-yellow, #d29922); }
  table { width:100%; border-collapse:collapse; font-size:12.5px; margin-top:8px; }
  th { text-align:left; color:var(--text-color-muted,#8b949e); border-bottom:1px solid var(--border-color-default,#30363d); padding:7px 8px; font-weight:600; }
  td { border-bottom:1px solid var(--border-color-muted,#21262d); padding:7px 8px; vertical-align:middle; }
  tr:last-child td { border-bottom:none; }
  .num { text-align:right; font-variant-numeric: tabular-nums; }
  .sid { font-family: var(--font-mono, ui-monospace, Menlo, monospace); font-size:11.5px; color: var(--text-color-muted,#8b949e); }
  .name { font-weight:600; }
  .models { font-size:11px; color:var(--text-color-muted,#8b949e); }
  .dt { white-space:nowrap; }
  .barcell { width:130px; }
  .bar { height:7px; background: var(--border-color-muted,#21262d); border-radius:999px; overflow:hidden; }
  .fill { height:100%; background: linear-gradient(90deg, var(--bar), color-mix(in srgb, var(--bar) 55%, transparent)); }
  .fill.prem { background: linear-gradient(90deg, #cf6a4c, color-mix(in srgb, #cf6a4c 55%, transparent)); }
  .note { color:var(--text-color-muted,#8b949e); font-size:11.5px; margin-top:9px; }
  .note code { font-family: var(--font-mono, ui-monospace, Menlo, monospace); font-size:11px; }
  .empty { color:var(--text-color-muted,#8b949e); font-size:12.5px; padding:10px 2px; }
  .srow { cursor:pointer; }
  .srow:hover { background: color-mix(in srgb, var(--bar) 12%, transparent); }
  .ov { position:fixed; inset:0; background:rgba(1,4,9,.66); display:none; align-items:flex-start; justify-content:center; z-index:50; overflow:auto; padding:34px 16px; }
  .ov.open { display:flex; }
  .modal { background: var(--background-color-default,#0d1117); border:1px solid var(--border-color-default,#30363d); border-radius:14px; max-width:920px; width:100%; padding:18px 20px 22px; box-shadow:0 16px 50px rgba(1,4,9,.6); }
  .modal h3 { margin:0 0 2px; font-size:17px; }
  .modal .msub { color:var(--text-color-muted,#8b949e); font-size:11.5px; margin-bottom:12px; }
  .modal h4 { margin:16px 0 4px; font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--text-color-muted,#8b949e); }
  .mclose { float:right; cursor:pointer; border:1px solid var(--border-color-default,#30363d); background:var(--background-color-muted,#161b22); color:var(--text-color-default,#e6edf3); border-radius:8px; padding:4px 10px; font-size:12px; }
  .mcards { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:6px 0 4px; }
  .pager { display:flex; align-items:center; justify-content:flex-end; gap:10px; margin-top:10px; font-size:12px; color:var(--text-color-muted,#8b949e); }
  .pager button { background:var(--background-color-muted,#161b22); color:var(--text-color-default,#e6edf3); border:1px solid var(--border-color-default,#30363d); border-radius:7px; padding:4px 11px; font-size:12px; cursor:pointer; }
  .pager button:hover:not(:disabled) { border-color:var(--bar); }
  .pager button:disabled { opacity:.4; cursor:default; }
  .logbtn { float:right; margin-right:8px; cursor:pointer; border:1px solid var(--bar); background:transparent; color:var(--text-color-default,#e6edf3); border-radius:8px; padding:4px 10px; font-size:12px; }
  .logbtn:hover { background:color-mix(in srgb, var(--bar) 18%, transparent); }
  .logtools { display:flex; gap:10px; align-items:center; margin:4px 0 10px; font-size:12px; color:var(--text-color-muted,#8b949e); flex-wrap:wrap; }
  .logtools select { background:var(--background-color-default,#0d1117); color:var(--text-color-default,#e6edf3); border:1px solid var(--border-color-default,#30363d); border-radius:7px; padding:4px 8px; font-size:12px; }
  .logview { max-height:62vh; overflow:auto; border:1px solid var(--border-color-default,#30363d); border-radius:10px; }
  .ev { border-bottom:1px solid var(--border-color-muted,#21262d); }
  .ev:last-child { border-bottom:none; }
  .ev > summary { list-style:none; cursor:pointer; padding:7px 12px; display:grid; grid-template-columns:74px 188px 1fr; gap:10px; align-items:baseline; font-size:12px; }
  .ev > summary::-webkit-details-marker { display:none; }
  .ev:hover > summary { background:color-mix(in srgb, var(--bar) 9%, transparent); }
  .ev .etime { color:var(--text-color-muted,#8b949e); font-variant-numeric:tabular-nums; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .ev .etype { color:var(--bar); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; }
  .ev .esum { color:var(--text-color-default,#e6edf3); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ev pre { margin:0; padding:10px 12px 14px; color:var(--text-color-default,#e6edf3); background:rgba(128,128,128,.10); font-size:11.5px; line-height:1.5; overflow:auto; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap; word-break:break-word; }
  @media (max-width: 900px) { .cards { grid-template-columns: repeat(3,1fr); } .mcards { grid-template-columns:repeat(2,1fr); } .ev > summary { grid-template-columns:64px 1fr; } .ev .etype { grid-column:2; } }
</style>
</head>
<body>
<main>
  <h1>MEASURE your own Copilot cost</h1>
  <div class="sub" id="generated"></div>
  <div class="filters">
    <label>From date<input type="date" id="f-from" /></label>
    <label>To date<input type="date" id="f-to" /></label>
    <label>Model<select id="f-model"></select></label>
    <label>Skill (loop)<select id="f-skill"></select></label>
    <button id="f-reset" type="button">Reset</button>
    <span class="frange" id="frange"></span>
  </div>
  <div class="legend">
    <span class="pill"><span class="d meas"></span> <b>Metered</b> — per-session &amp; per-model $ from real token telemetry</span>
    <span class="pill"><span class="d meas"></span> <b>Measured</b> — skill/tool usage &amp; window output tokens from events</span>
    <span class="pill"><span class="d est"></span> <b>Modeled</b> — windowed skill $ apportioned from metered session cost</span>
  </div>
  <div class="cards" id="cards"></div>
  <section>
    <h2>Cost by model <span class="tag m">metered</span></h2>
    <div id="models"></div>
    <div class="note">Per-model tokens and $ for the selected window. The <b>model sets the unit rate</b> (premium tiers cost multiples of right-sized ones); tokens are the volume — rate &times; volume is the bill. Use this to run the <b>tier-models</b> play.</div>
  </section>
  <section>
    <h2>Top sessions by cost <span class="tag m">metered</span></h2>
    <div id="sessions"></div>
    <div class="note">Session USD/credits come from each session's real per-model token counts (input / output / cache). The biggest sessions are where a better loop pays back fastest.</div>
  </section>
  <section>
    <h2>Most-used skills <span class="tag m">usage + window tokens measured</span> <span class="tag e">window $ modeled</span></h2>
    <div id="skills"></div>
    <div class="note" id="skills-note"></div>
  </section>
  <section>
    <h2>Most-used tools <span class="tag m">usage measured</span></h2>
    <div id="tools"></div>
    <div class="note">Calls and sessions are measured from <code>tool</code> events. Point tools are not windowed, so no per-tool $ is shown. Filtered by date (and by the selected skill's sessions); the model filter does not apply to tools.</div>
  </section>
</main>
<div class="ov" id="ov"><div class="modal" id="modal"></div></div>
<script>
  const data = ${json};
  const ST = data.sessions_tbl||[], FSM = data.facts_session_model||[], FSK = data.facts_skill||[], FT = data.facts_tool||[];
  const fmtMoney = n => '$' + Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const fmtCredits = n => Number(n||0).toLocaleString(undefined,{maximumFractionDigits:0});
  const fmtInt = n => Number(n||0).toLocaleString();
  const fmtPct = n => (Number(n||0)*100).toFixed(0)+'%';
  const fmtTok = n => { n=Number(n||0); return n>=1e9?(n/1e9).toFixed(1)+'B':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n); };
  const esc = s => String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const dayOf = i => { const r=ST[i]; return r?r[1]:''; };
  const sidOf = i => { const r=ST[i]; return r?r[0]:''; };
  const titleOf = i => { const r=ST[i]; return (r&&r[2])?r[2]:''; };
  const tier = m => { m=m||''; return m.indexOf('opus')>=0?'premium':(m.indexOf('sonnet')>=0?'mid':'economy'); };

  const state = { from: data.date_min||'', to: data.date_max||'', model:'', skill:'' };
  try { const q=new URLSearchParams(location.search); ['from','to','model','skill'].forEach(k=>{ if(q.has(k)) state[k]=q.get(k); }); } catch(e){}
  const inRange = i => { const d=dayOf(i); return (!state.from || d>=state.from) && (!state.to || d<=state.to); };

  const PAGE_SIZE = 10;
  const page = { sessions:0, skills:0, tools:0 };
  let LAST = null;
  function pageSlice(kind, arr){ const p=page[kind]; return arr.slice(p*PAGE_SIZE, p*PAGE_SIZE+PAGE_SIZE); }
  function pager(kind, total){
    const pages = Math.max(1, Math.ceil(total/PAGE_SIZE));
    if(page[kind] > pages-1) page[kind]=pages-1;
    if(total<=PAGE_SIZE) return '';
    const p=page[kind];
    return '<div class="pager" data-kind="'+kind+'"><button data-act="prev"'+(p<=0?' disabled':'')+'>‹ Prev</button>'+
      '<span>Page '+(p+1)+' / '+pages+' · '+fmtInt(total)+' total</span>'+
      '<button data-act="next"'+(p>=pages-1?' disabled':'')+'>Next ›</button></div>';
  }

  function compute(){
    const model=state.model, skill=state.skill;
    let skillSessions=null;
    if(skill){ skillSessions=new Set(); for(const f of FSK){ if(f.sk===skill && inRange(f.s) && (!model||f.m===model)) skillSessions.add(f.s); } }
    const smRows=[]; for(const r of FSM){ if(inRange(r.s) && (!model||r.m===model) && (!skillSessions||skillSessions.has(r.s))) smRows.push(r); }
    const pm={}; for(const r of smRows){ const a=pm[r.m]||(pm[r.m]={model:r.m,requests:0,i:0,cr:0,cw:0,o:0,t:0,usd:0,cred:0}); a.requests+=r.rq||0;a.i+=r.i||0;a.cr+=r.cr||0;a.cw+=r.cw||0;a.o+=r.o||0;a.t+=r.t||0;a.usd+=r.usd||0;a.cred+=r.cred||0; }
    const per_model=Object.values(pm).sort((x,y)=>y.usd-x.usd);
    const ss={}; for(const r of smRows){ const a=ss[r.s]||(ss[r.s]={s:r.s,usd:0,cred:0,t:0,models:new Set()}); a.usd+=r.usd||0;a.cred+=r.cred||0;a.t+=r.t||0; a.models.add(r.m); }
    const sessions=Object.values(ss).sort((x,y)=>y.usd-x.usd);
    let totUsd=0,totCred=0,totTok=0,premUsd=0; for(const r of smRows){ totUsd+=r.usd||0; totCred+=r.cred||0; totTok+=r.t||0; if(tier(r.m)==='premium') premUsd+=r.usd||0; }
    const skRows=[]; for(const f of FSK){ if(inRange(f.s) && (!model||f.m===model) && (!skill||f.sk===skill)) skRows.push(f); }
    const sk={}; for(const f of skRows){ const a=sk[f.sk]||(sk[f.sk]={skill:f.sk,calls:0,sessions:new Set(),o:0,i:0,cr:0,cw:0,usd:0,cred:0}); a.calls+=f.c||0;a.sessions.add(f.s);a.o+=f.o||0;a.i+=f.i||0;a.cr+=f.cr||0;a.cw+=f.cw||0;a.usd+=f.usd||0;a.cred+=f.cred||0; }
    const skills=Object.values(sk).sort((x,y)=>y.usd-x.usd);
    const skillTotUsd=skills.reduce((s,r)=>s+r.usd,0);
    const tlRows=[]; for(const f of FT){ if(inRange(f.s) && (!skillSessions||skillSessions.has(f.s))) tlRows.push(f); }
    const tl={}; for(const f of tlRows){ const a=tl[f.tl]||(tl[f.tl]={tool:f.tl,calls:0,sessions:new Set()}); a.calls+=f.c||0; a.sessions.add(f.s); }
    const tools=Object.values(tl).sort((x,y)=>y.calls-x.calls);
    let toolCalls=0; for(const f of tlRows) toolCalls+=f.c||0;
    return {per_model,sessions,skills,skillTotUsd,tools,totUsd,totCred,totTok,sessCount:sessions.length,premUsd,toolCalls};
  }

  function bar(v,max,cls){ return '<td class="barcell"><div class="bar"><div class="fill'+(cls?' '+cls:'')+'" style="width:'+(100*(v||0)/(max||1))+'%"></div></div></td>'; }

  function renderCards(c){
    const avg = c.sessCount ? c.totUsd/c.sessCount : 0;
    const premPct = c.totUsd ? c.premUsd/c.totUsd : 0;
    const cards = [
      ['Sessions', fmtInt(c.sessCount), ''],
      ['Metered $', fmtMoney(c.totUsd), ''],
      ['Credits', fmtCredits(c.totCred), ''],
      ['Avg $/session', fmtMoney(avg), 'accent'],
      ['Premium-tier $', fmtPct(premPct)+' <small>of spend</small>', 'accent'],
      ['Tool calls', fmtInt(c.toolCalls), ''],
    ];
    return cards.map(a=>'<div class="card '+(a[2])+'"><div class="label">'+a[0]+'</div><div class="value">'+a[1]+'</div></div>').join('');
  }
  function renderModels(c){
    const m=c.per_model; if(!m.length) return '<div class="empty">No metered model rows for this filter.</div>';
    const max=Math.max(...m.map(r=>r.usd||0),1);
    return '<table><thead><tr><th>Model</th><th>Tier</th><th class="num">Requests</th><th class="num">Input</th><th class="num">Cache rd</th><th class="num">Output</th><th class="num">USD</th><th>Cost</th></tr></thead><tbody>'+
      m.map(r=>'<tr><td class="name">'+esc(r.model)+'</td><td class="models">'+tier(r.model)+'</td><td class="num">'+fmtInt(r.requests)+'</td><td class="num">'+fmtTok(r.i)+'</td><td class="num">'+fmtTok(r.cr)+'</td><td class="num">'+fmtTok(r.o)+'</td><td class="num">'+fmtMoney(r.usd)+'</td>'+bar(r.usd,max,tier(r.model)==='premium'?'prem':'')+'</tr>').join('')+
      '</tbody></table>';
  }
  function renderSessions(c){
    const all=c.sessions; if(!all.length) return '<div class="empty">No sessions for this filter.</div>';
    const s=pageSlice('sessions',all);
    const max=Math.max(...all.map(r=>r.usd||0),1);
    return '<table><thead><tr><th>Session</th><th>Date</th><th>Models</th><th class="num">USD</th><th class="num">Credits</th><th class="num">Tokens</th><th>Cost</th></tr></thead><tbody>'+
      s.map(r=>{ const ti=titleOf(r.s), sid=sidOf(r.s); const label=ti?esc(ti):esc(sid.slice(0,8));
        return '<tr class="srow" data-s="'+r.s+'" title="'+esc(sid)+(ti?' · '+esc(ti):'')+'"><td><div class="name">'+label+'</div><div class="sid">'+esc(sid.slice(0,8))+' · click to inspect</div></td><td class="num dt">'+esc(dayOf(r.s)||'—')+'</td><td class="models">'+esc(Array.from(r.models).sort().join(', '))+'</td><td class="num">'+fmtMoney(r.usd)+'</td><td class="num">'+fmtCredits(r.cred)+'</td><td class="num">'+fmtTok(r.t)+'</td>'+bar(r.usd,max)+'</tr>'; }).join('')+
      '</tbody></table>'+pager('sessions',all.length);
  }
  function renderSkills(c){
    const all=c.skills; if(!all.length){ document.getElementById('skills-note').innerHTML='No skill invocations for this filter.'; return '<div class="empty">No skill invocations for this filter.</div>'; }
    const s=pageSlice('skills',all);
    const max=Math.max(...all.map(r=>r.usd||0),1);
    const top5=all.slice(0,5).reduce((a,r)=>a+r.usd,0);
    const conc = c.skillTotUsd ? top5/c.skillTotUsd : 0;
    document.getElementById('skills-note').innerHTML='<b>Calls, sessions &amp; window output are measured</b> from each <code>skill.invoked</code> until the next skill, the next HUMAN turn, or session end (synthetic skill-context messages no longer truncate the window; a new skill overtakes the prior one, so windows do not overlap). <b>Input / cache / $ (est)</b> apportion the metered session cost by that window\\'s output share (modeled, reconciles to metered). Top 5 skills = <b>'+fmtPct(conc)+'</b> of modeled skill $ — your <b>codify-the-loop</b> candidates.';
    return '<table><thead><tr><th>Skill (loop)</th><th class="num">Calls</th><th class="num">Sessions</th><th class="num">Win input</th><th class="num">Win cache rd</th><th class="num">Win output</th><th class="num">Window $ (est)</th><th>Window cost</th></tr></thead><tbody>'+
      s.map(r=>'<tr><td class="name">'+esc(r.skill)+'</td><td class="num">'+fmtInt(r.calls)+'</td><td class="num">'+fmtInt(r.sessions.size)+'</td><td class="num">'+fmtTok(r.i)+'</td><td class="num">'+fmtTok(r.cr)+'</td><td class="num">'+fmtTok(r.o)+'</td><td class="num">'+fmtMoney(r.usd)+'</td>'+bar(r.usd,max,'')+'</tr>').join('')+
      '</tbody></table>'+pager('skills',all.length);
  }
  function renderTools(c){
    const all=c.tools; if(!all.length) return '<div class="empty">No tool calls for this filter.</div>';
    const s=pageSlice('tools',all);
    const max=Math.max(...all.map(r=>r.calls||0),1);
    return '<table><thead><tr><th>Tool</th><th class="num">Calls</th><th class="num">Sessions</th><th>Usage</th></tr></thead><tbody>'+
      s.map(r=>'<tr><td class="name">'+esc(r.tool)+'</td><td class="num">'+fmtInt(r.calls)+'</td><td class="num">'+fmtInt(r.sessions.size)+'</td>'+bar(r.calls,max,'')+'</tr>').join('')+
      '</tbody></table>'+pager('tools',all.length);
  }

  function openInspector(s){
    const sid=sidOf(s), ti=titleOf(s), day=dayOf(s);
    const sm=FSM.filter(r=>r.s===s).slice().sort((a,b)=>(b.usd||0)-(a.usd||0));
    const fk=FSK.filter(r=>r.s===s).slice().sort((a,b)=>(b.usd||0)-(a.usd||0));
    const ft=FT.filter(r=>r.s===s).slice().sort((a,b)=>(b.c||0)-(a.c||0));
    let tUsd=0,tCred=0,tTok=0,tReq=0; for(const r of sm){ tUsd+=r.usd||0; tCred+=r.cred||0; tTok+=r.t||0; tReq+=r.rq||0; }
    let h='<button class="mclose" id="mclose">Close ✕</button>';
    h+='<button class="logbtn" id="mlogs">⌗ Logs</button>';
    h+='<h3>'+(ti?esc(ti):esc(sid.slice(0,8)))+'</h3>';
    h+='<div class="msub">'+esc(sid)+' · '+esc(day||'—')+'</div>';
    h+='<div class="mcards">'+
      '<div class="card"><div class="label">Metered $</div><div class="value">'+fmtMoney(tUsd)+'</div></div>'+
      '<div class="card"><div class="label">Credits</div><div class="value">'+fmtCredits(tCred)+'</div></div>'+
      '<div class="card"><div class="label">Tokens</div><div class="value">'+fmtTok(tTok)+'</div></div>'+
      '<div class="card"><div class="label">Requests</div><div class="value">'+fmtInt(tReq)+'</div></div>'+
      '</div>';
    h+='<h4>Per-model (metered)</h4>';
    if(sm.length){
      h+='<table><thead><tr><th>Model</th><th class="num">Req</th><th class="num">Input</th><th class="num">Cache rd</th><th class="num">Cache wr</th><th class="num">Output</th><th class="num">USD</th><th class="num">Credits</th></tr></thead><tbody>';
      for(const r of sm){ h+='<tr><td class="name">'+esc(r.m)+'</td><td class="num">'+fmtInt(r.rq)+'</td><td class="num">'+fmtTok(r.i)+'</td><td class="num">'+fmtTok(r.cr)+'</td><td class="num">'+fmtTok(r.cw)+'</td><td class="num">'+fmtTok(r.o)+'</td><td class="num">'+fmtMoney(r.usd)+'</td><td class="num">'+fmtCredits(r.cred)+'</td></tr>'; }
      h+='</tbody></table>';
    } else { h+='<div class="empty">No metered model rows.</div>'; }
    h+='<h4>Skill windows <span class="tag m">output measured</span> <span class="tag e">input/cache/$ modeled</span></h4>';
    if(fk.length){
      h+='<table><thead><tr><th>Skill</th><th>Model</th><th class="num">Calls</th><th class="num">Output</th><th class="num">Input</th><th class="num">Cache rd</th><th class="num">$ est</th></tr></thead><tbody>';
      for(const r of fk){ h+='<tr><td class="name">'+esc(r.sk)+'</td><td class="models">'+esc(r.m)+'</td><td class="num">'+fmtInt(r.c)+'</td><td class="num">'+fmtTok(r.o)+'</td><td class="num">'+fmtTok(r.i)+'</td><td class="num">'+fmtTok(r.cr)+'</td><td class="num">'+fmtMoney(r.usd)+'</td></tr>'; }
      h+='</tbody></table>';
    } else { h+='<div class="empty">No skill windows in this session.</div>'; }
    h+='<h4>Tools</h4>';
    if(ft.length){
      h+='<table><thead><tr><th>Tool</th><th class="num">Calls</th></tr></thead><tbody>';
      for(const r of ft.slice(0,40)){ h+='<tr><td class="name">'+esc(r.tl)+'</td><td class="num">'+fmtInt(r.c)+'</td></tr>'; }
      h+='</tbody></table>';
    } else { h+='<div class="empty">No tool calls in this session.</div>'; }
    document.getElementById('modal').innerHTML=h;
    document.getElementById('ov').classList.add('open');
    document.getElementById('mclose').addEventListener('click',closeInspector);
    document.getElementById('mlogs').addEventListener('click',()=>renderLogs(s));
  }
  function closeInspector(){ document.getElementById('ov').classList.remove('open'); }

  async function renderLogs(s){
    const sid=sidOf(s), ti=titleOf(s);
    const modal=document.getElementById('modal');
    modal.innerHTML='<button class="mclose" id="mclose">Close ✕</button><button class="logbtn" id="mback">‹ Back</button>'+
      '<h3>'+(ti?esc(ti):esc(sid.slice(0,8)))+'</h3><div class="msub">'+esc(sid)+' · raw session event log</div>'+
      '<div class="empty" id="logbody">Loading events…</div>';
    document.getElementById('mclose').addEventListener('click',closeInspector);
    document.getElementById('mback').addEventListener('click',()=>openInspector(s));
    let data;
    try { const r=await fetch('/api/logs?session='+encodeURIComponent(sid)); data=await r.json(); }
    catch(e){ document.getElementById('logbody').textContent='Failed to load logs: '+e; return; }
    if(!data || data.error){ document.getElementById('logbody').textContent=(data&&data.message)||'No logs available for this session.'; return; }
    const body=document.getElementById('logbody'); body.className='';
    const types=Object.keys(data.typeCounts||{}).sort();
    const opts='<option value="">all types</option>'+types.map(t=>'<option value="'+esc(t)+'">'+esc(t)+' ('+fmtInt(data.typeCounts[t])+')</option>').join('');
    body.innerHTML='<div class="logtools"><label>Filter <select id="logtype">'+opts+'</select></label>'+
      '<span>'+(data.truncated?'showing first '+fmtInt(data.returned)+' of '+fmtInt(data.total):fmtInt(data.total))+' events · strings clipped for display</span>'+
      '</div><div class="logview" id="logview"></div>';
    const view=document.getElementById('logview');
    function paintLog(filter){
      const evs=filter?data.events.filter(e=>e.type===filter):data.events;
      view.innerHTML=evs.length?evs.map(e=>'<details class="ev"><summary><span class="etime">'+esc(e.time||'')+'</span><span class="etype">'+esc(e.type)+'</span><span class="esum">'+esc(e.summary||'')+'</span></summary><pre>'+esc(e.text)+'</pre></details>').join(''):'<div class="empty">No events of this type.</div>';
    }
    paintLog('');
    document.getElementById('logtype').addEventListener('change',ev=>paintLog(ev.target.value));
  }

  function paint(kind){
    const el=document.getElementById(kind);
    el.innerHTML = kind==='sessions'?renderSessions(LAST):kind==='skills'?renderSkills(LAST):renderTools(LAST);
    const pg=el.querySelector('.pager');
    if(pg){ pg.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
      const tot = kind==='sessions'?LAST.sessions.length:kind==='skills'?LAST.skills.length:LAST.tools.length;
      const pages=Math.max(1,Math.ceil(tot/PAGE_SIZE));
      page[kind]= b.getAttribute('data-act')==='prev'?Math.max(0,page[kind]-1):Math.min(pages-1,page[kind]+1);
      paint(kind);
    })); }
    if(kind==='sessions'){ el.querySelectorAll('.srow').forEach(elm=>elm.addEventListener('click',()=>openInspector(parseInt(elm.getAttribute('data-s'),10)))); }
  }

  function renderAll(){
    LAST=compute();
    page.sessions=0; page.skills=0; page.tools=0;
    document.getElementById('cards').innerHTML=renderCards(LAST);
    document.getElementById('models').innerHTML=renderModels(LAST);
    paint('sessions'); paint('skills'); paint('tools');
    const flt=[]; if(state.model) flt.push('model='+state.model); if(state.skill) flt.push('skill='+state.skill);
    document.getElementById('frange').textContent=(state.from||'…')+' → '+(state.to||'…')+(flt.length?'  ·  '+flt.join('  ·  '):'')+'  ·  '+fmtInt(LAST.sessCount)+' sessions';
  }

  function initControls(){
    document.getElementById('generated').textContent='Generated '+(data.generated_at||'')+' · '+fmtInt((data.summary&&data.summary.session_count)||ST.length)+' local sessions · data '+(data.date_min||'')+' → '+(data.date_max||'');
    const ff=document.getElementById('f-from'), ft=document.getElementById('f-to');
    ff.min=ft.min=data.date_min||''; ff.max=ft.max=data.date_max||''; ff.value=state.from; ft.value=state.to;
    const mo=document.getElementById('f-model'); mo.innerHTML='<option value="">All models</option>'+(data.models||[]).map(m=>'<option value="'+esc(m)+'">'+esc(m)+' ('+tier(m)+')</option>').join('');
    const sk=document.getElementById('f-skill'); sk.innerHTML='<option value="">All skills</option>'+(data.skills||[]).map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join('');
    mo.value=state.model; sk.value=state.skill;
    ff.addEventListener('change',e=>{ state.from=e.target.value; renderAll(); });
    ft.addEventListener('change',e=>{ state.to=e.target.value; renderAll(); });
    mo.addEventListener('change',e=>{ state.model=e.target.value; renderAll(); });
    sk.addEventListener('change',e=>{ state.skill=e.target.value; renderAll(); });
    document.getElementById('f-reset').addEventListener('click',()=>{ state.from=data.date_min||''; state.to=data.date_max||''; state.model=''; state.skill=''; ff.value=state.from; ft.value=state.to; mo.value=''; sk.value=''; renderAll(); });
    document.getElementById('ov').addEventListener('click',e=>{ if(e.target.id==='ov') closeInspector(); });
    document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeInspector(); });
  }
  initControls();
  renderAll();
</script>
</body>
</html>`;
}

function emptyStateHtml() {
    return `<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>FinOps — Copilot cost telemetry</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; background: var(--background-color-default,#0d1117); color: var(--text-color-default,#e6edf3);
    font-family: var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif); }
  .wrap { max-width:680px; margin:0 auto; padding:64px 28px; }
  h1 { font-size: var(--text-title-large,24px); font-weight:600; margin:0 0 6px; }
  .sub { color: var(--text-color-muted,#8b949e); font-size:13.5px; margin:0 0 22px; line-height:1.55; }
  .card { background: var(--background-color-muted,#161b22); border:1px solid var(--border-color-default,#30363d);
    border-radius:12px; padding:18px 20px; margin:0 0 14px; }
  .card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color: var(--text-color-muted,#8b949e); margin:0 0 10px; }
  pre { margin:0; background: rgba(128,128,128,.10); border-radius:8px; padding:12px 14px; overflow:auto;
    font-family: var(--font-mono,ui-monospace,SFMono-Regular,Menlo,monospace); font-size:12.5px; line-height:1.7;
    color: var(--text-color-default,#e6edf3); }
  .ok { color: var(--true-color-green,#2ea043); }
  .muted { color: var(--text-color-muted,#8b949e); }
</style></head>
<body><div class="wrap">
  <h1>FinOps cost dashboard</h1>
  <p class="sub">The canvas is installed and running. There's no local telemetry snapshot to show yet —
  this dashboard renders <span class="muted">your own</span> Copilot session costs, which stay entirely on this machine.</p>
  <div class="card">
    <h2>Generate your snapshot</h2>
    <pre>cd demos/demo3-meter
python3 build_db.py        <span class="muted"># parse your local ~/.copilot logs</span>
python3 export_dashboard.py <span class="muted"># write dashboard_data.json</span></pre>
  </div>
  <div class="card">
    <h2>Then</h2>
    <pre>Reopen this canvas (or run the <span class="ok">refresh</span> action).</pre>
  </div>
  <p class="sub muted">No data leaves your machine. The snapshot is gitignored by design.</p>
</div></body></html>`;
}

async function startServer(instanceId) {
    let cached = null;
    const server = createServer(async (req, res) => {
        try {
            const u = new URL(req.url, "http://127.0.0.1");
            if (u.pathname === "/api/logs") {
                const sid = u.searchParams.get("session") || "";
                if (!/^[0-9a-fA-F-]{8,40}$/.test(sid)) {
                    res.statusCode = 400;
                    res.setHeader("Content-Type", "application/json");
                    res.end(JSON.stringify({ error: "bad_session", message: "Invalid session id." }));
                    return;
                }
                const payload = await buildLogs(sid);
                res.statusCode = payload && payload.error ? 404 : 200;
                res.setHeader("Content-Type", "application/json; charset=utf-8");
                res.end(JSON.stringify(payload));
                return;
            }
            let data;
            try {
                data = await loadData();
            } catch {
                // No local snapshot yet — serve a friendly empty-state, never a 500.
                res.setHeader("Content-Type", "text/html; charset=utf-8");
                res.end(emptyStateHtml());
                return;
            }
            if (!cached) cached = renderHtml(data);
            res.setHeader("Content-Type", "text/html; charset=utf-8");
            res.end(cached);
        } catch (err) {
            res.statusCode = 500;
            res.setHeader("Content-Type", "text/plain; charset=utf-8");
            res.end("Failed to render dashboard: " + (err && err.message ? err.message : String(err)));
        }
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    return { server, url: `http://127.0.0.1:${port}/` };
}

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "finops-dashboard",
            displayName: "FinOps cost dashboard",
            description: "Local Copilot cost telemetry: top sessions by metered cost, plus most-used skills and tools (ranked by measured usage).",
            actions: [
                {
                    name: "refresh",
                    description: "Reload the dashboard from the latest dashboard_data.json snapshot on disk.",
                    handler: async (ctx) => {
                        const entry = servers.get(ctx.instanceId);
                        if (!entry) throw new CanvasError("not_open", "Canvas instance is not open.");
                        const data = await loadData();
                        return {
                            ok: true,
                            sessions: data.summary.session_count,
                            usd: data.summary.usd,
                            top_skill: (data.top_skills && data.top_skills[0]) ? data.top_skills[0].skill_name : null,
                        };
                    },
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx.instanceId);
                    servers.set(ctx.instanceId, entry);
                }
                return { title: "FinOps cost dashboard", url: entry.url };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});
