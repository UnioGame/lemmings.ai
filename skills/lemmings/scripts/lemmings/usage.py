"""Local token-usage normalization for host exports; never reads transcripts into prompts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _first(mapping: Mapping[str, Any], *names: str) -> int | float | None:
    for name in names:
        value = _number(mapping.get(name))
        if value is not None:
            return value
    return None


def _coalesce(*values: int | float | None) -> int | float | None:
    return next((value for value in values if value is not None), None)


def _usage_mapping(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], int | float | None]:
    part = record.get("part") if isinstance(record.get("part"), Mapping) else None
    info = record.get("info") if isinstance(record.get("info"), Mapping) else None
    usage = record.get("usage") if isinstance(record.get("usage"), Mapping) else None
    if part and isinstance(part.get("tokens"), Mapping):
        return part["tokens"], _number(part.get("cost"))
    if info and isinstance(info.get("tokens"), Mapping):
        return info["tokens"], _number(info.get("cost"))
    if isinstance(record.get("tokens"), Mapping):
        return record["tokens"], _number(record.get("cost"))
    return usage or record, _coalesce(_number(record.get("cost")), _number(record.get("reportedCost")))


def normalize_usage_record(host: str, record: Mapping[str, Any]) -> dict[str, Any]:
    tokens, cost = _usage_mapping(record)
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), Mapping) else {}
    input_tokens = _first(tokens, "input", "inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
    output_tokens = _first(tokens, "output", "outputTokens", "output_tokens", "completionTokens", "completion_tokens")
    reasoning = _first(tokens, "reasoning", "reasoningTokens", "reasoning_tokens")
    cache_read = _coalesce(_first(cache, "read"), _first(tokens, "cacheReadTokens", "cache_read_tokens", "cacheReadInputTokens"))
    cache_write = _coalesce(_first(cache, "write"), _first(tokens, "cacheWriteTokens", "cache_write_tokens", "cacheWriteInputTokens"))
    total = _first(tokens, "total", "totalTokens", "total_tokens")
    exact = any(value is not None for value in (input_tokens, output_tokens, reasoning, cache_read, cache_write, total))
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "reasoningTokens": reasoning,
        "cacheReadTokens": cache_read,
        "cacheWriteTokens": cache_write,
        "totalTokens": total,
        "reportedCost": cost,
        "currency": "USD" if cost is not None else None,
        "source": f"{host}-export",
        "exact": exact,
    }


def _records(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield item
        return
    if not isinstance(value, Mapping):
        return
    if isinstance(value.get("messages"), list):
        yield from _records(value["messages"])
        return
    if isinstance(value.get("events"), list):
        yield from _records(value["events"])
        return
    yield value


def normalize_usage_export(host: str, value: Any) -> dict[str, Any]:
    if host not in {"codex", "opencode", "kilo"}:
        raise ValueError("usage host must be codex, opencode, or kilo")
    normalized = [normalize_usage_record(host, item) for item in _records(value)]
    exact = [item for item in normalized if item["exact"]]
    if not exact:
        return normalize_usage_record(host, {})
    fields = ("inputTokens", "outputTokens", "reasoningTokens", "cacheReadTokens", "cacheWriteTokens", "totalTokens", "reportedCost")
    result: dict[str, Any] = {field: None for field in fields}
    for field in fields:
        values = [item[field] for item in exact if item[field] is not None]
        result[field] = sum(values) if values else None
    result.update({"currency": "USD" if result["reportedCost"] is not None else None, "source": f"{host}-export", "exact": True})
    return result
