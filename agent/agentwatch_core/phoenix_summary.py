"""Lightweight Phoenix summary tools.

The official ``@arizeai/phoenix-mcp`` server returns full trace payloads —
useful for deep inspection but easy to blow past Gemini's 1M-token context
window when a project has long-running agents. These helpers hit the
Phoenix REST API directly and return only the metadata the agent needs to
*decide what to inspect next*: trace IDs, latency, status code, span count.

Use these as the agent's first-pass tools; reach for the full MCP toolset
only when the user wants to dive into a specific trace or dataset.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from agentwatch_core.pricing import current_model, pricing_for, pricing_note


def _phoenix_base_url() -> str:
    """Phoenix Cloud base URL (workspace root, no /v1/traces suffix)."""
    raw = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    return raw.replace("/v1/traces", "").rstrip("/")


def _phoenix_headers() -> Dict[str, str]:
    api_key = os.environ.get("PHOENIX_API_KEY", "").strip()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_phoenix_base_url(),
        headers=_phoenix_headers(),
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# Tool functions (each one will become an ADK FunctionTool)
# ---------------------------------------------------------------------------


def list_projects_summary() -> Dict[str, Any]:
    """List every Phoenix project in this workspace with no trace bodies.

    Returns a compact list of {id, name, description} — safe to call any time.
    """
    with _client() as c:
        resp = c.get("/v1/projects")
        resp.raise_for_status()
        data = resp.json().get("data", [])
    return {
        "projects": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "description": p.get("description"),
            }
            for p in data
        ],
        "count": len(data),
    }


def get_project_trace_summary(
    project_name: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """Return a lightweight summary of the most recent traces in a project.

    For each trace returns only: trace_id, root span name, start/end time,
    latency_ms, status, span_count. Does NOT return any span bodies/prompts.

    Use this to scan for outliers (slow traces, error traces) and then call
    ``inspect_trace`` (or the Phoenix MCP ``get-trace`` tool) on the
    specific trace_ids you want to investigate.

    Args:
        project_name: Phoenix project name (e.g. 'agentwatch').
        limit: Max traces to summarize. Default 10, hard cap 50.
    """
    limit = max(1, min(int(limit), 50))
    project = quote(project_name, safe="")

    # Use the /v1/spans endpoint with project filter + pagination,
    # then group by trace_id ourselves.
    with _client() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": limit * 20})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found.", "traces": []}
        resp.raise_for_status()
        payload = resp.json()

    spans = payload.get("data", [])

    traces: Dict[str, Dict[str, Any]] = {}
    for span in spans:
        # OTLP-shaped span: id, trace_id, parent_id, name, start_time,
        # end_time, status_code, etc.
        tid = span.get("context", {}).get("trace_id") or span.get("trace_id")
        if not tid:
            continue
        bucket = traces.setdefault(
            tid,
            {
                "trace_id": tid,
                "span_count": 0,
                "root_span_name": None,
                "start_time": None,
                "end_time": None,
                "status_codes": set(),
                "tool_calls": [],
                "llm_calls": 0,
            },
        )
        bucket["span_count"] += 1
        bucket["status_codes"].add(span.get("status_code", "UNSET"))
        st = span.get("start_time")
        et = span.get("end_time")
        if st and (bucket["start_time"] is None or st < bucket["start_time"]):
            bucket["start_time"] = st
        if et and (bucket["end_time"] is None or et > bucket["end_time"]):
            bucket["end_time"] = et
        if span.get("parent_id") in (None, "") and bucket["root_span_name"] is None:
            bucket["root_span_name"] = span.get("name")
        # Phoenix REST API exposes span kind at top level as "span_kind"
        # (values: "LLM", "TOOL", "CHAIN", "AGENT", "RETRIEVER", "EMBEDDING")
        kind = span.get("span_kind", "")
        attrs = span.get("attributes") or {}
        if kind == "LLM":
            bucket["llm_calls"] += 1
        elif kind == "TOOL":
            tool_name = (
                attrs.get("tool.name")
                or attrs.get("gen_ai.tool.name")
                or span.get("name", "").replace("execute_tool ", "")
            )
            if tool_name:
                bucket["tool_calls"].append(tool_name)

    # Compute latency and convert sets → lists for JSON
    summaries: List[Dict[str, Any]] = []
    for t in traces.values():
        latency_ms: Optional[float] = None
        if t["start_time"] and t["end_time"]:
            try:
                from datetime import datetime

                s = datetime.fromisoformat(t["start_time"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(t["end_time"].replace("Z", "+00:00"))
                latency_ms = round((e - s).total_seconds() * 1000, 1)
            except Exception:
                pass
        summaries.append(
            {
                "trace_id": t["trace_id"],
                "root_span": t["root_span_name"],
                "span_count": t["span_count"],
                "llm_calls": t["llm_calls"],
                "tool_calls": t["tool_calls"],
                "latency_ms": latency_ms,
                "status_codes": sorted(t["status_codes"]),
                "start_time": t["start_time"],
            }
        )

    # Newest first, then trim
    summaries.sort(key=lambda x: x["start_time"] or "", reverse=True)
    summaries = summaries[:limit]

    # Aggregate stats
    latencies = [s["latency_ms"] for s in summaries if s["latency_ms"] is not None]
    stats = {
        "trace_count": len(summaries),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
        if latencies
        else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "any_errors": any("ERROR" in s["status_codes"] for s in summaries),
    }

    return {"project": project_name, "stats": stats, "traces": summaries}


def inspect_trace(trace_id: str, project_name: str = "agentwatch") -> Dict[str, Any]:
    """Fetch one trace's spans in detail.

    Use this only AFTER ``get_project_trace_summary`` or ``get_failure_traces``
    has narrowed you down to a specific trace_id worth inspecting. Returns
    trimmed span data: name, kind, latency, status, attribute keys — but
    truncates large input/output payloads to keep the context manageable.

    Args:
        trace_id: The trace_id to inspect (from a prior summary call).
        project_name: Project the trace belongs to. Defaults to 'agentwatch'.
    """
    # Phoenix's REST API does not support a trace_id query param on /v1/spans
    # (returns 422), and filter_condition does not filter by trace_id. So we
    # page through the project's spans and filter client-side.
    project = quote(project_name, safe="")
    with _client() as c:
        resp = c.get(
            f"/v1/projects/{project}/spans", params={"limit": 500}
        )
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found.", "spans": []}
        resp.raise_for_status()
        all_spans = resp.json().get("data", [])

    spans = [
        s
        for s in all_spans
        if (s.get("context", {}).get("trace_id") or s.get("trace_id")) == trace_id
    ]
    if not spans:
        return {
            "trace_id": trace_id,
            "error": (
                f"No spans found for trace_id={trace_id} in project "
                f"'{project_name}'. It may be older than the 500 most recent "
                "spans — narrow your summary query or check the project name."
            ),
            "spans": [],
        }

    def _short(val: Any, max_chars: int = 500) -> Any:
        if isinstance(val, str) and len(val) > max_chars:
            return val[:max_chars] + f"... [truncated {len(val) - max_chars} chars]"
        return val

    trimmed = []
    for s in spans:
        attrs = s.get("attributes") or {}
        # Phoenix REST API surfaces span kind at top-level "span_kind"
        kind = s.get("span_kind", "")
        # Compute latency for this span
        span_latency_ms: Optional[float] = None
        if s.get("start_time") and s.get("end_time"):
            try:
                from datetime import datetime
                st = datetime.fromisoformat(s["start_time"].replace("Z", "+00:00"))
                et = datetime.fromisoformat(s["end_time"].replace("Z", "+00:00"))
                span_latency_ms = round((et - st).total_seconds() * 1000, 1)
            except Exception:
                pass

        # The real error lives in status_message (top level) and in the
        # span's events array, NOT in attributes["exception.message"].
        exception_msg = s.get("status_message") or ""
        exception_type = None
        for ev in s.get("events", []) or []:
            if ev.get("name") == "exception":
                ev_attrs = ev.get("attributes", {}) or {}
                exception_msg = ev_attrs.get("exception.message") or exception_msg
                exception_type = ev_attrs.get("exception.type")
                break

        trimmed.append(
            {
                "span_id": s.get("context", {}).get("span_id") or s.get("id"),
                "parent_id": s.get("parent_id"),
                "name": s.get("name"),
                "kind": kind,
                "status_code": s.get("status_code"),
                "latency_ms": span_latency_ms,
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "tool_name": attrs.get("tool.name") or attrs.get("gen_ai.tool.name"),
                "llm_model": attrs.get("llm.model_name") or attrs.get("gen_ai.response.model"),
                "input_preview": _short(attrs.get("input.value", "")),
                "output_preview": _short(attrs.get("output.value", "")),
                "exception_type": exception_type,
                "exception": _short(exception_msg),
            }
        )
    return {"trace_id": trace_id, "span_count": len(trimmed), "spans": trimmed}


def get_failure_traces(
    project_name: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """Return only ERROR/failed traces for a project — useful for root-cause analysis.

    Filters to traces that contain at least one span with status ERROR or
    EXCEPTION. Returns the same lightweight format as ``get_project_trace_summary``
    so you can then call ``inspect_trace`` on the interesting ones.

    Args:
        project_name: Phoenix project name.
        limit: Max failure traces to return (default 10, cap 50).
    """
    result = get_project_trace_summary(project_name, limit=50)
    if "error" in result:
        return result

    failures = [
        t for t in result.get("traces", [])
        if any(
            code in ("ERROR", "ERROR_STATUS_CODE", "STATUS_CODE_ERROR")
            for code in t.get("status_codes", [])
        )
    ]
    failures = failures[:max(1, min(int(limit), 50))]

    latencies = [t["latency_ms"] for t in failures if t["latency_ms"] is not None]
    return {
        "project": project_name,
        "failure_count": len(failures),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "traces": failures,
    }


def get_token_usage_stats(
    project_name: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Aggregate token usage across recent LLM spans for a project.

    Reads prompt_tokens and completion_tokens from LLM span attributes to
    surface cost/budget insights. Returns per-trace totals plus an aggregate.

    Args:
        project_name: Phoenix project name.
        limit: Number of recent traces to analyze (default 20, cap 50).
    """
    limit = max(1, min(int(limit), 50))
    from urllib.parse import quote as _quote
    project = _quote(project_name, safe="")

    with _client() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": limit * 20})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found."}
        resp.raise_for_status()
        spans = resp.json().get("data", [])

    # Only LLM spans carry token counts
    trace_tokens: Dict[str, Dict[str, int]] = {}
    for s in spans:
        if s.get("span_kind") != "LLM":
            continue
        tid = s.get("context", {}).get("trace_id") or s.get("trace_id")
        if not tid:
            continue
        attrs = s.get("attributes") or {}
        prompt = int(attrs.get("llm.token_count.prompt", 0) or 0)
        completion = int(attrs.get("llm.token_count.completion", 0) or 0)
        total = int(attrs.get("llm.token_count.total", 0) or 0) or (prompt + completion)
        bucket = trace_tokens.setdefault(tid, {"prompt": 0, "completion": 0, "total": 0, "llm_calls": 0})
        bucket["prompt"] += prompt
        bucket["completion"] += completion
        bucket["total"] += total
        bucket["llm_calls"] += 1

    rows = [{"trace_id": tid, **vals} for tid, vals in trace_tokens.items()]
    rows = rows[:limit]

    total_prompt = sum(r["prompt"] for r in rows)
    total_completion = sum(r["completion"] for r in rows)
    total_all = sum(r["total"] for r in rows)

    # Cost estimation — priced for the configured Gemini model (see pricing.py).
    _pin, _pout = pricing_for()
    _price_in = _pin / 1_000_000
    _price_out = _pout / 1_000_000
    # Per-trace cost so the UI bars use the same pricing as the aggregate.
    for r in rows:
        r["cost_usd"] = round(
            r["prompt"] * _price_in + r["completion"] * _price_out, 6
        )
    estimated_cost_usd = round(
        total_prompt * _price_in + total_completion * _price_out, 6
    )
    cost_per_trace = round(
        estimated_cost_usd / len(rows), 6
    ) if rows else 0.0

    return {
        "project": project_name,
        "traces_analyzed": len(rows),
        "aggregate": {
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_all,
            "avg_tokens_per_trace": round(total_all / len(rows), 0) if rows else 0,
            "estimated_cost_usd": estimated_cost_usd,
            "avg_cost_per_trace_usd": cost_per_trace,
            "model": current_model(),
            "pricing_note": pricing_note(),
        },
        "per_trace": rows,
    }
