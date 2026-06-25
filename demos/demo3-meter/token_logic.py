#!/usr/bin/env python3
"""Token and pricing calculations shared by FinOps telemetry parsers.

This module centralizes token normalization, aggregation, and AIU/USD math so
higher-level parsers can stay focused on source-specific extraction.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Tuple

RATES_USD_PER_MTOK = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
CREDIT_USD = float(os.environ.get("CREDIT_USD", "0.01"))
NANO_PER_AIU = 1_000_000_000
TOKEN_TYPES = ("input", "cache_read", "cache_write", "output")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def family(model: str) -> str:
    text = (model or "").lower()
    if "opus" in text:
        return "opus"
    if "sonnet" in text:
        return "sonnet"
    if "haiku" in text:
        return "haiku"
    return "opus"


def canonical_model(model: str) -> str:
    model = (model or "unknown").strip().lower()
    if model.startswith("copilot/"):
        model = model.split("/", 1)[1]
    if model.startswith("capi:"):
        model = model.split(":", 2)[1]
    return model.replace(".", "-")


def normalize_token_type(name: str) -> str:
    mapping = {
        "inputTokens": "input",
        "outputTokens": "output",
        "cacheReadTokens": "cache_read",
        "cacheWriteTokens": "cache_write",
        "input": "input",
        "output": "output",
        "cache_read": "cache_read",
        "cache_write": "cache_write",
    }
    return mapping.get(name, name)


def _empty_token_totals() -> Dict[str, int]:
    return {k: 0 for k in TOKEN_TYPES}


def zero_tokens() -> Dict[str, int]:
    return _empty_token_totals()


def _token_count_from_raw_value(raw_value: Any) -> int:
    if isinstance(raw_value, dict):
        return _as_int(_first_non_empty(raw_value.get("tokenCount"), raw_value.get("token_count")), 0)
    return _as_int(raw_value, 0)


def _add_token_if_supported(tokens: Dict[str, int], raw_type: Any, raw_value: Any) -> None:
    token_type = normalize_token_type(str(raw_type))
    if token_type not in tokens:
        return
    tokens[token_type] += _token_count_from_raw_value(raw_value)


def _tokens_from_usage_map(usage: Dict[str, Any]) -> Dict[str, int]:
    tokens = _empty_token_totals()
    for raw_key, raw_value in (usage or {}).items():
        _add_token_if_supported(tokens, raw_key, raw_value)
    return tokens


def _tokens_from_token_details_map(token_details: Dict[str, Any]) -> Dict[str, int]:
    tokens = _empty_token_totals()
    for raw_key, raw_value in (token_details or {}).items():
        _add_token_if_supported(tokens, raw_key, raw_value)
    return tokens


def _nano_aiu_and_tokens_from_token_details_list(details: Iterable[Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
    total_nano_aiu = 0
    by_type = _empty_token_totals()
    for detail in details:
        token_count = _as_int(detail.get("token_count"), 0)
        batch_size = max(1, _as_int(detail.get("batch_size"), 1))
        cost_per_batch = _as_int(detail.get("cost_per_batch"), 0)
        per_token_nano_aiu = cost_per_batch // batch_size

        token_type = normalize_token_type(str(detail.get("token_type", "")))
        if token_type in by_type:
            by_type[token_type] += token_count
        total_nano_aiu += token_count * per_token_nano_aiu
    return total_nano_aiu, by_type


def extract_request_token_counts(req_item: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, int]:
    return {
        "input": _as_int(
            _first_non_empty(
                metadata.get("promptTokens"),
                metadata.get("inputTokens"),
                req_item.get("promptTokens"),
                req_item.get("inputTokens"),
            ),
            0,
        ),
        "output": _as_int(
            _first_non_empty(
                metadata.get("completionTokens"),
                metadata.get("outputTokens"),
                req_item.get("completionTokens"),
                req_item.get("outputTokens"),
            ),
            0,
        ),
        "cache_write": _as_int(
            _first_non_empty(metadata.get("cacheCreationTokens"), req_item.get("cacheCreationTokens")),
            0,
        ),
        "cache_read": _as_int(
            _first_non_empty(metadata.get("cacheReadTokens"), req_item.get("cacheReadTokens")),
            0,
        ),
    }


def usd_from_tokens(model: str, tokens: Dict[str, float]) -> float:
    base_in, base_out = RATES_USD_PER_MTOK[family(model)]
    return (
        base_in
        * (
            tokens.get("input", 0)
            + 0.10 * tokens.get("cache_read", 0)
            + 1.25 * tokens.get("cache_write", 0)
        )
        / 1_000_000
        + base_out * tokens.get("output", 0) / 1_000_000
    )


def nano_aiu_from_details(details: Iterable[Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
    return _nano_aiu_and_tokens_from_token_details_list(details)


def tokens_from_token_details_map(token_details: Dict[str, Any]) -> Dict[str, int]:
    return _tokens_from_token_details_map(token_details)


def tokens_from_usage(usage: Dict[str, Any]) -> Dict[str, int]:
    return _tokens_from_usage_map(usage)


def add_tokens(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in TOKEN_TYPES}
