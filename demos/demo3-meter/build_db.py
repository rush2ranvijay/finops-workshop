#!/usr/bin/env python3
"""Build finops.db from local Copilot session-state events and process logs."""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from collections import Counter, defaultdict

import finops_core as fc

WORK = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORK, "finops.db")
LOG_DIR = os.path.expanduser(os.environ.get("COPILOT_LOG_DIR", "~/.copilot/logs"))
STATE_DIR = os.path.expanduser(os.environ.get("COPILOT_SESSION_STATE_DIR", "~/.copilot/session-state"))


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS session_skill_windows;
        DROP TABLE IF EXISTS session_models;
        DROP TABLE IF EXISTS session_tools;
        DROP TABLE IF EXISTS sessions;
        DROP TABLE IF EXISTS etl_metadata;

        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            models TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            nano_aiu INTEGER,
            aiu REAL,
            usd REAL NOT NULL DEFAULT 0,
            credits REAL NOT NULL DEFAULT 0,
            premium_requests REAL,
            responses INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            start_time TEXT,
            end_time TEXT,
            cwd TEXT,
            repository TEXT,
            selected_model TEXT,
            events_path TEXT,
            source_logs TEXT,
            has_shutdown INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE session_models (
            session_id TEXT NOT NULL,
            model TEXT NOT NULL,
            responses INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            nano_aiu INTEGER,
            aiu REAL,
            usd REAL NOT NULL DEFAULT 0,
            credits REAL NOT NULL DEFAULT 0,
            premium_requests REAL,
            PRIMARY KEY (session_id, model),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE session_tools (
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            invocation_count INTEGER NOT NULL DEFAULT 0,
            attributed_input_tokens REAL NOT NULL DEFAULT 0,
            attributed_output_tokens REAL NOT NULL DEFAULT 0,
            attributed_cache_read_tokens REAL NOT NULL DEFAULT 0,
            attributed_cache_write_tokens REAL NOT NULL DEFAULT 0,
            attributed_total_tokens REAL NOT NULL DEFAULT 0,
            attributed_usd REAL NOT NULL DEFAULT 0,
            attributed_credits REAL NOT NULL DEFAULT 0,
            attribution_method TEXT NOT NULL,
            PRIMARY KEY (session_id, tool_name),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE session_skill_windows (
            session_id TEXT NOT NULL,
            window_index INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            model TEXT NOT NULL,
            window_start_time TEXT,
            window_end_time TEXT,
            window_output_tokens INTEGER NOT NULL DEFAULT 0,
            window_input_tokens INTEGER NOT NULL DEFAULT 0,
            window_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            window_cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            denominator_output_tokens INTEGER NOT NULL DEFAULT 0,
            model_session_usd REAL NOT NULL DEFAULT 0,
            window_usd_est REAL NOT NULL DEFAULT 0,
            window_credits_est REAL NOT NULL DEFAULT 0,
            invocation_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, window_index, model),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE etl_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def upsert_session(conn: sqlite3.Connection, session: dict) -> None:
    tokens = session.get("tokens") or fc.zero_tokens()
    total_tokens = sum(int(tokens.get(k, 0) or 0) for k in fc.TOKEN_TYPES)
    conn.execute(
        """
        INSERT OR REPLACE INTO sessions (
            session_id, source, models, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
            total_tokens, nano_aiu, aiu, usd, credits, premium_requests, responses, tool_call_count,
            start_time, end_time, cwd, repository, selected_model, events_path, source_logs, has_shutdown
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session["session_id"], session.get("source") or "events_no_cost", session.get("model_names") or session.get("selected_model"),
            tokens.get("input", 0), tokens.get("output", 0), tokens.get("cache_read", 0), tokens.get("cache_write", 0),
            total_tokens, session.get("nano_aiu"), session.get("aiu"), session.get("usd") or 0.0,
            session.get("credits") or 0.0, session.get("premium_requests"), session.get("responses") or 0,
            session.get("tool_call_count") or 0, session.get("start_time"), session.get("end_time"),
            session.get("cwd"), session.get("repository"), session.get("selected_model"), session.get("events_path"),
            json.dumps(session.get("source_logs") or []), 1 if session.get("has_shutdown") else 0,
        ),
    )
    for model in session.get("models") or []:
        mt = model.get("tokens") or fc.zero_tokens()
        conn.execute(
            """
            INSERT OR REPLACE INTO session_models (
                session_id, model, responses, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                total_tokens, nano_aiu, aiu, usd, credits, premium_requests
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["session_id"], model.get("model"), model.get("responses") or 0,
                mt.get("input", 0), mt.get("output", 0), mt.get("cache_read", 0), mt.get("cache_write", 0),
                sum(int(mt.get(k, 0) or 0) for k in fc.TOKEN_TYPES), model.get("nano_aiu"), model.get("aiu"),
                model.get("usd") or 0.0, model.get("credits") or 0.0, model.get("premium_requests"),
            ),
        )
    for name, attr in fc.attribute_to_invocations(session, session.get("invocation_counts") or {}).items():
        conn.execute(
            """
            INSERT OR REPLACE INTO session_tools (
                session_id, tool_name, invocation_count, attributed_input_tokens, attributed_output_tokens,
                attributed_cache_read_tokens, attributed_cache_write_tokens, attributed_total_tokens,
                attributed_usd, attributed_credits, attribution_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["session_id"], name, attr["count"], attr["input"], attr["output"], attr["cache_read"],
                attr["cache_write"], attr["tokens_total"], attr["usd"], attr["credits"],
                "per-session proportional by invocation count; no per-tool token spans in events",
            ),
        )
    for window in session.get("skill_windows") or []:
        conn.execute(
            """
            INSERT OR REPLACE INTO session_skill_windows (
                session_id, window_index, skill_name, model, window_start_time, window_end_time,
                window_output_tokens, window_input_tokens, window_cache_read_tokens, window_cache_write_tokens,
                denominator_output_tokens, model_session_usd, window_usd_est,
                window_credits_est, invocation_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["session_id"],
                window.get("window_index"),
                window.get("skill_name"),
                window.get("model"),
                window.get("window_start_time"),
                window.get("window_end_time"),
                window.get("window_output_tokens") or 0,
                window.get("window_input_tokens") or 0,
                window.get("window_cache_read_tokens") or 0,
                window.get("window_cache_write_tokens") or 0,
                window.get("denominator_output_tokens") or 0,
                window.get("model_session_usd") or 0.0,
                window.get("window_usd_est") or 0.0,
                window.get("window_credits_est") or 0.0,
                window.get("invocation_count") or 0,
            ),
        )


def load_event_sessions() -> dict:
    sessions = {}
    for path in glob.glob(os.path.join(STATE_DIR, "*", "events.jsonl")):
        session_id = os.path.basename(os.path.dirname(path))
        try:
            sessions[session_id] = fc.summarize_event_file(session_id, path)
        except OSError:
            continue
    return sessions


def load_raw_log_sessions() -> dict:
    per_session_records = defaultdict(list)
    source_logs = defaultdict(set)
    parse_errors = []
    for path in glob.glob(os.path.join(LOG_DIR, "*.log")):
        try:
            records = fc.parse_copilot_usage_log(path)
        except Exception as exc:  # keep demo resilient to partial logs
            parse_errors.append({"path": path, "error": str(exc)})
            continue
        for record in records:
            sid = record.get("session_id")
            if not sid:
                continue
            per_session_records[sid].append(record)
            source_logs[sid].add(path)
    return {
        sid: fc.session_from_log_records(sid, records, sorted(source_logs[sid]))
        for sid, records in per_session_records.items()
    }, parse_errors


def main() -> int:
    event_sessions = load_event_sessions()
    raw_sessions, parse_errors = load_raw_log_sessions()
    # Prefer shutdown events when they contain cost-bearing model metrics. Use raw logs for no-shutdown/live sessions.
    merged = dict(event_sessions)
    for sid, raw in raw_sessions.items():
        existing = merged.get(sid)
        if not existing or not existing.get("has_shutdown") or not (existing.get("usd") or 0):
            if existing:
                raw.update({k: existing.get(k) for k in ("events_path", "start_time", "end_time", "cwd", "repository", "selected_model", "tool_call_count", "invocation_counts", "has_shutdown") if existing.get(k) is not None})
                if existing.get("events_path"):
                    raw["skill_windows"] = fc.compute_skill_windows(fc.read_events_file(existing["events_path"]), raw)
            merged[sid] = raw
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    for session in merged.values():
        upsert_session(conn, session)
    meta = {
        "state_dir": STATE_DIR,
        "log_dir": LOG_DIR,
        "event_sessions": len(event_sessions),
        "raw_log_sessions": len(raw_sessions),
        "merged_sessions": len(merged),
        "raw_log_parse_errors": parse_errors,
        "credit_usd": fc.CREDIT_USD,
        "tool_attribution_method": "Tools keep measured invocation counts and distinct sessions; dashboard dollars are metered session cost where the tool ran, not per-tool attribution.",
        "skill_window_method": "Skill windows open at skill.invoked and close at the EARLIEST of the next skill.invoked, the next HUMAN user.message (source absent or 'user'; synthetic skill-context/autopilot messages are ignored), or session end. A new skill overtakes the prior one, so windows do NOT overlap. window_output_tokens are MEASURED assistant.message outputTokens summed per model (including subagent turns on other models that do not themselves emit a skill.invoked). window_input/cache_read/cache_write_tokens, window_usd_est and window_credits_est are MODELED: each model's metered session totals apportioned by the window's output share, with denominator max(metered_output, sum_window_output) as a safety cap.",
    }
    for key, value in meta.items():
        conn.execute("INSERT OR REPLACE INTO etl_metadata (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()
    # concise build summary
    cur = conn.execute("SELECT COUNT(*), SUM(usd), SUM(credits), SUM(tool_call_count) FROM sessions")
    count, usd, credits, tools = cur.fetchone()
    conn.close()
    print(f"built {DB_PATH}: sessions={count} usd={usd:.6f} credits={credits:.2f} tool_calls={int(tools or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
