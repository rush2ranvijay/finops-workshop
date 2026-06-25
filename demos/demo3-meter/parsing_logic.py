#!/usr/bin/env python3
"""Parsing helpers for local Copilot telemetry sources.

This module contains source-specific parsing logic so `finops_core.py` can stay
focused on aggregation/orchestration.
"""
from __future__ import annotations

import bisect
import glob
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from token_logic import canonical_model, extract_request_token_counts, nano_aiu_from_details

SESSION_ANCHOR = re.compile(r"for session ([0-9a-fA-F-]{36})")
MODEL_LINE = re.compile(r'"model"\s*:\s*"([^"]+)"')


def first_non_empty(*values: Any) -> Any:
    """Return the first non-empty candidate value."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def safe_get(dct: Any, *path: str) -> Any:
    """Safely read nested dict keys without exceptions."""
    cur = dct
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def iso_from_millis(ms: Any) -> Optional[str]:
    """Convert epoch milliseconds to an ISO-8601 UTC timestamp."""
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def parse_iso_timestamp(ts: Optional[str]) -> Optional[str]:
    """Normalize optional ISO timestamp fields from event payloads."""
    if not ts:
        return None
    return ts


def find_logs(session_id: str, log_dir: str) -> List[str]:
    """Find process logs that mention the target session id (newest first)."""
    hits = []
    for path in sorted(glob.glob(os.path.join(os.path.expanduser(log_dir), "*.log")), key=os.path.getmtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                if session_id in fh.read():
                    hits.append(path)
        except OSError:
            continue
    return hits


def extract_json_object_after_key(lines: List[str], start_idx: int) -> Tuple[Optional[Dict[str, Any]], int]:
    """Brace-match a pretty-printed JSON object beginning at `start_idx`."""
    buffer: List[str] = []
    depth = 0
    started = False
    end_idx = start_idx
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        for ch in line:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        buffer.append(line)
        end_idx = idx
        if started and depth == 0:
            break

    text = "\n".join(buffer)
    brace = text.find("{")
    if brace < 0:
        return None, start_idx
    try:
        return json.loads(text[brace:]), end_idx
    except json.JSONDecodeError:
        return None, start_idx


def session_for_line_nearest(anchors: List[Tuple[int, str]], line_no: int) -> Optional[str]:
    """Attach a usage block to the closest surrounding session anchor line."""
    if not anchors:
        return None
    positions = [n for n, _sid in anchors]
    idx = bisect.bisect_left(positions, line_no)
    candidates = []
    if idx < len(anchors):
        candidates.append(anchors[idx])
    if idx > 0:
        candidates.append(anchors[idx - 1])
    return min(candidates, key=lambda pair: abs(pair[0] - line_no))[1]


def parse_copilot_usage_log(path: str, target_session: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse process logs and extract `copilot_usage` token blocks."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    anchors: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines):
        match = SESSION_ANCHOR.search(line)
        if match:
            anchors.append((idx, match.group(1)))

    records: List[Dict[str, Any]] = []
    current_model = "unknown"
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        model_match = MODEL_LINE.search(line)
        if model_match:
            current_model = canonical_model(model_match.group(1))

        if '"copilot_usage"' in line:
            obj, end_idx = extract_json_object_after_key(lines, idx)
            if obj and "token_details" in obj:
                nano_aiu, tokens = nano_aiu_from_details(obj["token_details"])
                logged_nano_aiu = int(obj.get("total_nano_aiu", nano_aiu))
                if nano_aiu != logged_nano_aiu:
                    raise ValueError(
                        f"AIU self-check failed in {path}:{idx + 1}: derived {nano_aiu} != logged {logged_nano_aiu}"
                    )
                session_id = session_for_line_nearest(anchors, idx)
                record = {
                    "line": idx + 1,
                    "session_id": session_id,
                    "model": current_model,
                    "tokens": tokens,
                    "nano_aiu": logged_nano_aiu,
                    "source_log": path,
                }
                if target_session is None or session_id == target_session:
                    records.append(record)
                idx = end_idx + 1
                continue
        idx += 1
    return records


def _record_from_chatsession_request(
    req_item: Any,
    line_idx: int,
    path: str,
    session_id: Optional[str],
    model_hint: str,
    target_session: Optional[str],
    session_start_hint: Optional[str],
    developer: str,
) -> Optional[Dict[str, Any]]:
    """Convert one chatSessions request object into a normalized usage record."""
    if not isinstance(req_item, dict):
        return None

    req_session = str(req_item.get("sessionId") or session_id or "")
    if not req_session:
        return None
    if target_session is not None and req_session != target_session:
        return None

    result = req_item.get("result") or {}
    metadata = result.get("metadata") or {} if isinstance(result, dict) else {}

    request_tokens = extract_request_token_counts(req_item, metadata)
    if sum(request_tokens.values()) <= 0:
        return None

    req_model = canonical_model(
        str(
            (req_item.get("agent") or {}).get("model")
            or req_item.get("modelId")
            or metadata.get("model")
            or (req_item.get("metadata") or {}).get("model")
            or model_hint
            or "unknown"
        )
    )
    ts_iso = iso_from_millis(
        first_non_empty(
            req_item.get("timestamp"),
            metadata.get("timestamp"),
            safe_get(req_item, "response", "timestamp"),
        )
    )

    out = {
        "line": line_idx,
        "session_id": req_session,
        "model": req_model,
        "tokens": request_tokens,
        "nano_aiu": None,
        "source_log": path,
        "timestamp": ts_iso,
        "session_start_hint": session_start_hint,
    }
    if developer:
        out["developer"] = developer
    return out


def parse_copilot_chatsessions_jsonl(path: str, target_session: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse VS Code chatSessions snapshots/patches into usage records."""
    records: List[Dict[str, Any]] = []
    session_id: Optional[str] = None
    model_hint = "unknown"
    session_start_hint: Optional[str] = None
    developer = ""

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for idx, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue

            kind = obj.get("kind")
            if kind == 0:
                # Bootstrap snapshot for the whole session.
                v = obj.get("v") or {}
                session_id = str(v.get("sessionId") or "")
                session_start_hint = iso_from_millis(v.get("creationDate"))

                selected_model = safe_get(v, "inputState", "selectedModel") or {}
                if isinstance(selected_model, dict):
                    model_hint = canonical_model(str(safe_get(selected_model, "metadata", "version") or "unknown"))
                    developer = str(safe_get(selected_model, "metadata", "auth", "accountLabel") or developer)

                bootstrap_requests = v.get("requests") or []
                if isinstance(bootstrap_requests, list):
                    for req_item in bootstrap_requests:
                        record = _record_from_chatsession_request(
                            req_item,
                            idx,
                            path,
                            session_id,
                            model_hint,
                            target_session,
                            session_start_hint,
                            developer,
                        )
                        if record:
                            records.append(record)
                continue

            if kind != 2:
                continue

            # Incremental patch records where k=['requests', ...] and v=[...].
            key_path = obj.get("k")
            if not isinstance(key_path, list) or len(key_path) < 1:
                continue
            if key_path[0] != "requests" or not (len(key_path) == 1 or isinstance(key_path[1], int)):
                continue

            payload = obj.get("v")
            if not isinstance(payload, list) or len(payload) == 0:
                continue
            for req_item in payload:
                record = _record_from_chatsession_request(
                    req_item,
                    idx,
                    path,
                    session_id,
                    model_hint,
                    target_session,
                    session_start_hint,
                    developer,
                )
                if record:
                    records.append(record)

    return records


def read_events_file(path: str) -> List[Dict[str, Any]]:
    """Read and decode event JSONL, skipping malformed lines."""
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                events.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    return events
