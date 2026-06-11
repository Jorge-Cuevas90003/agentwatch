"""LLM-as-judge evaluation tools and trend analysis for AgentWatch.

Provides three groups of capabilities:
  1. run_llm_evals        — Gemini judges spans for hallucination / relevance /
                             QA correctness / conciseness. Results posted to
                             Phoenix as span annotations (visible in Phoenix UI).
  2. compare_time_windows — Compares recent vs baseline window on error_rate,
                             latency, token usage → regression / improvement verdict.
  3. create_failure_dataset — Harvests ERROR traces into a Phoenix dataset
                               (uses addSpansToDataset GraphQL mutation) ready
                               for experiment runs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from agentwatch_core.pricing import current_model

# ── Eval prompt templates ───────────────────────────────────────────────────
# Use <<INPUT>> / <<OUTPUT>> as placeholders — NOT {input}/{output} — so that
# Python str.format() never gets confused by the JSON braces in the example
# output at the end of each prompt.
EVAL_PROMPTS: Dict[str, str] = {
    "hallucination": (
        "You are an expert AI evaluator detecting hallucinations.\n\n"
        "Given the INPUT and OUTPUT of an AI agent, determine if the OUTPUT "
        "contains claims, facts, or statements NOT grounded in the INPUT.\n\n"
        "INPUT:\n<<INPUT>>\n\nOUTPUT:\n<<OUTPUT>>\n\n"
        'Return ONLY a JSON object (no markdown fences):\n'
        '{"label": "factual" or "hallucinated", '
        '"score": 0.0 to 1.0 where 1.0 means fully factual, '
        '"explanation": "one concise sentence"}'
    ),
    "relevance": (
        "You are an expert AI evaluator assessing response relevance.\n\n"
        "Determine if the AI agent OUTPUT is relevant and responsive to the INPUT.\n\n"
        "INPUT:\n<<INPUT>>\n\nOUTPUT:\n<<OUTPUT>>\n\n"
        'Return ONLY a JSON object (no markdown fences):\n'
        '{"label": "relevant" or "irrelevant", '
        '"score": 0.0 to 1.0 where 1.0 means fully relevant, '
        '"explanation": "one concise sentence"}'
    ),
    "qa_correctness": (
        "You are an expert AI evaluator assessing answer correctness.\n\n"
        "Determine if the AI agent OUTPUT correctly answers the INPUT question.\n\n"
        "INPUT:\n<<INPUT>>\n\nOUTPUT:\n<<OUTPUT>>\n\n"
        'Return ONLY a JSON object (no markdown fences):\n'
        '{"label": "correct" or "incorrect", '
        '"score": 0.0 to 1.0 where 1.0 means fully correct, '
        '"explanation": "one concise sentence"}'
    ),
    "conciseness": (
        "You are an expert AI evaluator assessing response conciseness.\n\n"
        "Determine if the AI agent OUTPUT is appropriately concise "
        "or unnecessarily verbose given the INPUT.\n\n"
        "INPUT:\n<<INPUT>>\n\nOUTPUT:\n<<OUTPUT>>\n\n"
        'Return ONLY a JSON object (no markdown fences):\n'
        '{"label": "concise" or "verbose", '
        '"score": 0.0 to 1.0 where 1.0 means perfectly concise, '
        '"explanation": "one concise sentence"}'
    ),
}


# ── Shared HTTP helpers ─────────────────────────────────────────────────────

def _base_url() -> str:
    raw = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
    return raw.replace("/v1/traces", "").rstrip("/")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ.get('PHOENIX_API_KEY', '').strip()}",
        "Content-Type": "application/json",
    }


def _http() -> httpx.Client:
    return httpx.Client(base_url=_base_url(), headers=_headers(), timeout=30.0)


def _graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    """Execute a Phoenix GraphQL query/mutation."""
    with _http() as c:
        r = c.post("/graphql", json={"query": query, "variables": variables or {}})
        r.raise_for_status()
        return r.json()


# ── Gemini judge ────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> Dict[str, Any]:
    """Synchronous Gemini call for one LLM-as-judge eval. Returns parsed JSON."""
    from google import genai

    client = genai.Client()
    model = current_model()
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config={"temperature": 0.1, "max_output_tokens": 1024},
    )
    text = (resp.text or "").strip()

    # Strip markdown fences if the model wrapped the JSON
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break

    # Try standard parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract label and score with regex even if JSON is truncated
    import re
    label_m = re.search(r'"label"\s*:\s*"([^"]+)"', text)
    score_m = re.search(r'"score"\s*:\s*([\d.]+)', text)
    expl_m = re.search(r'"explanation"\s*:\s*"([^"]*)', text)
    if label_m and score_m:
        return {
            "label": label_m.group(1),
            "score": float(score_m.group(1)),
            "explanation": (expl_m.group(1) + "…") if expl_m else "(truncated)",
        }
    raise ValueError(f"Could not parse eval response: {text[:200]}")


def _gemini_generate(instruction: str, user_text: str) -> str:
    """Free-form Gemini generation (not JSON) — used to produce a candidate
    answer when A/B testing a proposed prompt against production behavior."""
    from google import genai

    client = genai.Client()
    resp = client.models.generate_content(
        model=current_model(),
        contents=f"{instruction}\n\nUser request:\n{user_text}",
        config={"temperature": 0.3, "max_output_tokens": 800},
    )
    return (resp.text or "").strip()


def _extract_text(raw: str, role: str = "user") -> str:
    """Best-effort extraction of the human-readable text from a span's
    input.value / output.value, which may be plain text or JSON in several
    shapes (ADK, OpenAI chat, OpenInference). Falls back to the raw string.

    role: "user" pulls the request from input.value; "assistant" pulls the
    answer from output.value.
    """
    raw = (raw or "").strip()
    if not raw or (raw[0] not in "{["):
        return raw[:2000]
    try:
        obj = json.loads(raw)
    except Exception:
        return raw[:2000]
    if not isinstance(obj, dict):
        return raw[:2000]

    # ADK shape: input {"new_message": {"parts": [{"text": ...}]}}
    #            output {"content": {"parts": [{"text": ...}]}}
    container = obj.get("new_message") or obj.get("content")
    if isinstance(container, dict):
        parts = container.get("parts") or []
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
        if texts:
            return "\n".join(texts)[:2000]

    # Gemini API shape: {"contents": [{"parts": [{"text": ...}]}, ...]}
    contents = obj.get("contents")
    if isinstance(contents, list):
        texts = []
        for turn in contents:
            for p in (turn.get("parts") or []) if isinstance(turn, dict) else []:
                if isinstance(p, dict) and p.get("text"):
                    texts.append(p["text"])
        if texts:
            # For a request, the user's ask is the last text part.
            return (texts[-1] if role == "user" else "\n".join(texts))[:2000]

    # OpenAI chat shape: {"messages": [{"role": "user", "content": ...}]}
    msgs = obj.get("messages")
    if isinstance(msgs, list):
        want = "user" if role == "user" else "assistant"
        picked = [m.get("content", "") for m in msgs
                  if isinstance(m, dict) and m.get("role") == want]
        if picked:
            return str(picked[-1])[:2000]

    # Generic single-field shapes
    for k in ("text", "content", "output", "input", "prompt", "question", "answer"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v[:2000]
    return raw[:2000]


# ── Tool 1: LLM-as-judge evals ──────────────────────────────────────────────

def run_llm_evals(
    project_name: str,
    eval_type: str = "hallucination",
    limit: int = 5,
) -> Dict[str, Any]:
    """Run LLM-as-judge evaluations on recent spans in a project.

    Uses Gemini to evaluate each span's input/output for the chosen quality
    dimension. Results are posted back to Phoenix as span annotations —
    they appear in the Phoenix UI and can drive experiments.

    Args:
        project_name: Phoenix project name.
        eval_type: One of "hallucination", "relevance", "qa_correctness",
            "conciseness". Default "hallucination".
        limit: Number of spans to evaluate. Keep ≤ 10 — each span costs one
            Gemini call. Default 5.

    Returns:
        eval_type, evaluated_count, avg_score, label_distribution,
        annotation_status, per_span_results.
    """
    if eval_type not in EVAL_PROMPTS:
        return {
            "error": (
                f"Unknown eval_type '{eval_type}'. "
                f"Choose from: {sorted(EVAL_PROMPTS.keys())}"
            )
        }

    limit = max(1, min(int(limit), 20))
    project = quote(project_name, safe="")

    with _http() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": 300})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found."}
        resp.raise_for_status()
        spans = resp.json().get("data", [])

    # Keep only spans that have both input.value and output.value
    evaluable = [
        s for s in spans
        if (s.get("attributes") or {}).get("input.value")
        and (s.get("attributes") or {}).get("output.value")
    ][:limit]

    if not evaluable:
        return {
            "error": "No spans with input/output text found to evaluate.",
            "project": project_name,
            "hint": "Ensure your agent writes input.value / output.value attributes.",
        }

    prompt_template = EVAL_PROMPTS[eval_type]
    results: List[Dict[str, Any]] = []
    annotations: List[Dict[str, Any]] = []

    for s in evaluable:
        attrs = s.get("attributes") or {}
        span_id = s.get("context", {}).get("span_id") or ""
        span_name = s.get("name", "")
        trace_id = s.get("context", {}).get("trace_id", "")

        inp = str(attrs.get("input.value", ""))[:1200]
        out = str(attrs.get("output.value", ""))[:1200]

        try:
            # Use simple str.replace — templates use <<INPUT>>/<<OUTPUT>>
            # to avoid collision with JSON braces inside inp/out.
            prompt = (
                prompt_template
                .replace("<<INPUT>>", inp)
                .replace("<<OUTPUT>>", out)
            )
            verdict = _call_gemini(prompt)
            label = str(verdict.get("label", "unknown"))
            score = float(verdict.get("score", 0.5))
            explanation = str(verdict.get("explanation", ""))
            eval_status = "ok"
        except Exception as exc:
            label, score, explanation = "error", 0.0, str(exc)[:120]
            eval_status = "eval_error"

        results.append({
            "span_id": span_id,
            "span_name": span_name,
            "trace_id": trace_id,
            "label": label,
            "score": score,
            "explanation": explanation,
            "status": eval_status,
        })

        if eval_status == "ok":
            annotations.append({
                "span_id": span_id,
                "name": eval_type,
                "annotator_kind": "LLM",
                "result": {
                    "label": label,
                    "score": score,
                    "explanation": explanation,
                },
                "metadata": {
                    "evaluator_model": current_model(),
                },
            })

    # Post annotations to Phoenix
    annotation_status: Dict[str, Any] = {"posted": 0, "status": "skipped"}
    if annotations:
        with _http() as c:
            ar = c.post("/v1/span_annotations", json={"data": annotations})
            annotation_status = {
                "posted": len(annotations) if ar.status_code == 200 else 0,
                "status": "ok" if ar.status_code == 200 else f"http_{ar.status_code}",
            }

    # Aggregate
    good = [r for r in results if r["status"] == "ok"]
    scores = [r["score"] for r in good]
    label_dist: Dict[str, int] = {}
    for r in good:
        label_dist[r["label"]] = label_dist.get(r["label"], 0) + 1

    return {
        "project": project_name,
        "eval_type": eval_type,
        "evaluated_count": len(results),
        "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
        "label_distribution": label_dist,
        "annotation_status": annotation_status,
        "per_span_results": results,
    }


# ── Tool 1b: Prompt A/B experiment (closes the self-improvement loop) ────────

def run_prompt_experiment(
    project_name: str,
    candidate_instruction: str,
    eval_type: str = "qa_correctness",
    limit: int = 3,
) -> Dict[str, Any]:
    """A/B test a candidate prompt against production behavior — in-process.

    This CLOSES the self-improvement loop without needing Phoenix MCP /
    run-experiment (which isn't available on cloud deploys). After you diagnose
    a quality issue and draft a fix, call this to PROVE the fix helps with real
    numbers before recommending it.

    For each recent span that has both input and output:
      1. baseline_score  — LLM-judge of the EXISTING production output.
      2. candidate_output — Gemini run with ``candidate_instruction`` on the
         same extracted user request.
      3. candidate_score — LLM-judge of the candidate output, same rubric.
    Then aggregates avg baseline vs avg candidate, win/loss/tie counts, and a
    verdict (CANDIDATE BETTER / WORSE / TIE).

    Args:
        project_name: Phoenix project to pull recent spans from.
        candidate_instruction: The improved system prompt/instruction to test.
        eval_type: Quality rubric to judge on — one of "hallucination",
            "relevance", "qa_correctness", "conciseness". Default "qa_correctness".
        limit: Spans to test (default 3, hard cap 5 — each span costs 3 Gemini
            calls: one generation + two judgements).
    """
    if eval_type not in EVAL_PROMPTS:
        return {"error": f"Unknown eval_type '{eval_type}'. "
                         f"Choose from: {sorted(EVAL_PROMPTS.keys())}"}
    if not (candidate_instruction or "").strip():
        return {"error": "candidate_instruction is required — pass the improved prompt to test."}

    limit = max(1, min(int(limit), 5))
    project = quote(project_name, safe="")

    with _http() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": 200})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found."}
        resp.raise_for_status()
        spans = resp.json().get("data", [])

    evaluable = [
        s for s in spans
        if (s.get("attributes") or {}).get("input.value")
        and (s.get("attributes") or {}).get("output.value")
    ][:limit]
    if not evaluable:
        return {"error": "No spans with input+output found to experiment on.",
                "project": project_name}

    rubric = EVAL_PROMPTS[eval_type]

    def _judge(question: str, answer: str) -> Dict[str, Any]:
        prompt = rubric.replace("<<INPUT>>", question[:1200]).replace("<<OUTPUT>>", answer[:1200])
        try:
            v = _call_gemini(prompt)
            return {"score": float(v.get("score", 0.5)), "label": str(v.get("label", "unknown"))}
        except Exception as exc:  # noqa: BLE001
            return {"score": None, "label": "eval_error", "error": str(exc)[:120]}

    rows: List[Dict[str, Any]] = []
    for s in evaluable:
        attrs = s.get("attributes") or {}
        question = _extract_text(str(attrs.get("input.value", "")), role="user")
        baseline_out = _extract_text(str(attrs.get("output.value", "")), role="assistant")
        try:
            candidate_out = _gemini_generate(candidate_instruction, question)
            gen_ok = True
        except Exception as exc:  # noqa: BLE001
            candidate_out, gen_ok = f"(generation failed: {str(exc)[:100]})", False

        base = _judge(question, baseline_out)
        cand = _judge(question, candidate_out) if gen_ok else {"score": None, "label": "skipped"}

        delta = (
            round(cand["score"] - base["score"], 3)
            if (base["score"] is not None and cand["score"] is not None) else None
        )
        rows.append({
            "span_id": s.get("context", {}).get("span_id") or s.get("id"),
            "question_preview": question[:160],
            "baseline_score": base["score"], "baseline_label": base["label"],
            "candidate_score": cand["score"], "candidate_label": cand["label"],
            "candidate_output_preview": candidate_out[:240],
            "delta": delta,
        })

    scored = [r for r in rows if r["delta"] is not None]
    base_scores = [r["baseline_score"] for r in scored]
    cand_scores = [r["candidate_score"] for r in scored]
    wins = sum(1 for r in scored if r["delta"] > 0.05)
    losses = sum(1 for r in scored if r["delta"] < -0.05)
    ties = len(scored) - wins - losses

    avg_base = round(sum(base_scores) / len(base_scores), 3) if base_scores else None
    avg_cand = round(sum(cand_scores) / len(cand_scores), 3) if cand_scores else None
    avg_delta = round(avg_cand - avg_base, 3) if (avg_base is not None and avg_cand is not None) else None

    if avg_delta is None:
        verdict = "INCONCLUSIVE — could not score the comparison"
    elif avg_delta > 0.05:
        verdict = "CANDIDATE BETTER 📈"
    elif avg_delta < -0.05:
        verdict = "CANDIDATE WORSE 📉"
    else:
        verdict = "TIE ➡️"

    return {
        "project": project_name,
        "eval_type": eval_type,
        "spans_tested": len(rows),
        "avg_baseline_score": avg_base,
        "avg_candidate_score": avg_cand,
        "avg_delta": avg_delta,
        "wins": wins, "losses": losses, "ties": ties,
        "verdict": verdict,
        "per_span": rows,
        "note": "Baseline = current production output; candidate = your proposed "
                "prompt run on the same requests. Both judged on the same rubric.",
    }


# ── Tool 2: Time-window trend comparison ────────────────────────────────────

def compare_time_windows(
    project_name: str,
    window_hours: int = 24,
) -> Dict[str, Any]:
    """Compare agent performance: most recent N hours vs. the N hours before.

    Computes delta on error_rate, avg_latency_ms, p95_latency_ms,
    avg_llm_calls_per_trace, avg_tool_calls_per_trace.
    Negative delta on latency/errors = improvement. Positive = regression.

    Args:
        project_name: Phoenix project name.
        window_hours: Window size in hours (1–168). Default 24.
    """
    window_hours = max(1, min(int(window_hours), 168))
    project = quote(project_name, safe="")

    with _http() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": 1000})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found."}
        resp.raise_for_status()
        spans = resp.json().get("data", [])

    now = datetime.now(timezone.utc)
    cutoff_a_start = now - timedelta(hours=window_hours)
    cutoff_b_start = cutoff_a_start - timedelta(hours=window_hours)

    def _ts(s: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    # Build per-trace buckets for both windows (root spans only)
    def _empty_trace() -> Dict[str, Any]:
        return {"latency_ms": None, "is_error": False, "llm_calls": 0, "tool_calls": 0}

    traces_a: Dict[str, Dict] = {}
    traces_b: Dict[str, Dict] = {}

    # First pass: root spans → establish trace buckets
    for s in spans:
        if s.get("parent_id") not in (None, ""):
            continue
        st = _ts(s.get("start_time", ""))
        if st is None:
            continue
        tid = s.get("context", {}).get("trace_id", "")
        et = _ts(s.get("end_time", "")) if s.get("end_time") else None
        lat = round((et - st).total_seconds() * 1000, 1) if et else None
        entry = _empty_trace()
        entry["latency_ms"] = lat
        entry["is_error"] = s.get("status_code") == "ERROR"
        if st >= cutoff_a_start:
            traces_a[tid] = entry
        elif st >= cutoff_b_start:
            traces_b[tid] = entry

    # Second pass: count LLM/TOOL child spans per trace
    for s in spans:
        if s.get("parent_id") in (None, ""):
            continue
        tid = s.get("context", {}).get("trace_id", "")
        kind = s.get("span_kind", "")
        for bucket in (traces_a, traces_b):
            if tid in bucket:
                if kind == "LLM":
                    bucket[tid]["llm_calls"] += 1
                elif kind == "TOOL":
                    bucket[tid]["tool_calls"] += 1

    def _stats(traces: Dict[str, Dict]) -> Dict[str, Any]:
        if not traces:
            return {
                "count": 0,
                "error_rate": None,
                "avg_latency_ms": None,
                "p95_latency_ms": None,
                "avg_llm_calls": None,
                "avg_tool_calls": None,
            }
        lats = sorted(t["latency_ms"] for t in traces.values() if t["latency_ms"] is not None)
        n = len(traces)
        errors = sum(1 for t in traces.values() if t["is_error"])
        p95_idx = max(0, int(len(lats) * 0.95) - 1)
        return {
            "count": n,
            "error_rate": round(errors / n, 3),
            "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else None,
            "p95_latency_ms": lats[p95_idx] if lats else None,
            "avg_llm_calls": round(
                sum(t["llm_calls"] for t in traces.values()) / n, 1
            ),
            "avg_tool_calls": round(
                sum(t["tool_calls"] for t in traces.values()) / n, 1
            ),
        }

    sa, sb = _stats(traces_a), _stats(traces_b)

    def _d(a: Optional[float], b: Optional[float]) -> Optional[float]:
        return round(a - b, 3) if a is not None and b is not None else None

    # For lower-is-better metrics: negative delta = improvement
    _lat_pct = (
        round((sa["avg_latency_ms"] - sb["avg_latency_ms"]) / sb["avg_latency_ms"] * 100, 1)
        if sb["avg_latency_ms"] and sa["avg_latency_ms"] is not None
        else None
    )
    delta = {
        "error_rate": _d(sa["error_rate"], sb["error_rate"]),
        "avg_latency_ms": _d(sa["avg_latency_ms"], sb["avg_latency_ms"]),
        "avg_latency_pct": _lat_pct,
        "p95_latency_ms": _d(sa["p95_latency_ms"], sb["p95_latency_ms"]),
        "note": "Negative = improved vs baseline. Positive = regression.",
    }

    # Overall verdict.
    #
    # Latency is judged on RELATIVE change, not an absolute ms threshold: agent
    # latencies here span ~1s to ~2min, so a fixed ±500ms cutoff would flag pure
    # noise as a regression on a 60s trace. 10% is the noise floor. Error rate is
    # judged on absolute percentage-point change (±2pp).
    #
    # Each metric votes independently (−1 improved / +1 regressed / 0 flat). We
    # never let one improving metric mask another that regressed — mixed signals
    # surface as MIXED rather than being rounded to IMPROVED.
    error_delta = delta.get("error_rate")  # absolute pp change, lower is better
    lat_pct = None
    if sb["avg_latency_ms"] and sa["avg_latency_ms"] is not None:
        lat_pct = (sa["avg_latency_ms"] - sb["avg_latency_ms"]) / sb["avg_latency_ms"]

    def _vote(val: Optional[float], eps: float) -> int:
        if val is None:
            return 0
        if val < -eps:
            return -1  # improved (metric went down)
        if val > eps:
            return 1   # regressed (metric went up)
        return 0

    votes = [_vote(error_delta, 0.02), _vote(lat_pct, 0.10)]
    improved = any(v < 0 for v in votes)
    regressed = any(v > 0 for v in votes)

    if improved and regressed:
        verdict = "MIXED ↔️"
    elif improved:
        verdict = "IMPROVED 📈"
    elif regressed:
        verdict = "DEGRADED 📉"
    else:
        verdict = "STABLE ➡️"

    # Can only compare when BOTH windows have traces; otherwise the delta is
    # meaningless (a single populated window was being rounded to STABLE before).
    if sa["count"] == 0 or sb["count"] == 0:
        verdict = "NO DATA — try a larger window_hours"

    return {
        "project": project_name,
        "window_hours": window_hours,
        "window_a_recent": {
            "label": f"Last {window_hours}h",
            "from": cutoff_a_start.isoformat(),
            "to": now.isoformat(),
            **sa,
        },
        "window_b_baseline": {
            "label": f"{window_hours}h before that",
            "from": cutoff_b_start.isoformat(),
            "to": cutoff_a_start.isoformat(),
            **sb,
        },
        "delta": delta,
        "verdict": verdict,
    }


# ── Tool 3: Create failure dataset ──────────────────────────────────────────

def create_failure_dataset(
    project_name: str,
    dataset_name: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """Create a Phoenix dataset from ERROR spans — ready for experiment runs.

    Uses the Phoenix GraphQL addSpansToDataset mutation to add the raw
    failing spans. The dataset then appears in the Phoenix UI and can be
    used with LLM evaluators or experiments to validate prompt improvements.

    This is step 1 of the self-improvement loop:
      create_failure_dataset → inspect failures → draft fix → run experiment.

    Args:
        project_name: Phoenix project name to harvest failures from.
        dataset_name: Name for the new Phoenix dataset. Auto-generated if omitted.
        limit: Max ERROR spans to include (default 10, cap 50).
    """
    if not dataset_name:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dataset_name = f"failures-{project_name}-{date_str}"

    limit = max(1, min(int(limit), 50))
    project = quote(project_name, safe="")

    # Fetch spans and filter to ERROR ones that have input/output
    with _http() as c:
        resp = c.get(f"/v1/projects/{project}/spans", params={"limit": 500})
        if resp.status_code == 404:
            return {"error": f"Project '{project_name}' not found."}
        resp.raise_for_status()
        all_spans = resp.json().get("data", [])

    error_spans = [
        s for s in all_spans
        if s.get("status_code") == "ERROR"
        and (s.get("attributes") or {}).get("input.value")
    ][:limit]

    if not error_spans:
        return {
            "status": "no_failures",
            "message": (
                f"No ERROR spans with input.value found in '{project_name}'. "
                "Run some failing traces first, then try again."
            ),
        }

    # Create the dataset via GraphQL
    create_q = """
    mutation CreateDataset($name: String!, $desc: String!) {
      createDataset(input: {name: $name, description: $desc}) {
        dataset { id name }
      }
    }
    """
    create_resp = _graphql(create_q, {
        "name": dataset_name,
        "desc": (
            f"Auto-created by AgentWatch from {len(error_spans)} ERROR spans "
            f"in project '{project_name}'. "
            f"Created {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."
        ),
    })
    if "errors" in create_resp:
        return {"error": "GraphQL dataset creation failed.", "detail": create_resp["errors"]}

    dataset_id = create_resp["data"]["createDataset"]["dataset"]["id"]
    dataset_name_out = create_resp["data"]["createDataset"]["dataset"]["name"]

    # Add error spans to the dataset (use their base64 GraphQL IDs)
    span_gids = [s["id"] for s in error_spans if s.get("id")]
    add_q = """
    mutation AddSpans($datasetId: ID!, $spanIds: [ID!]!) {
      addSpansToDataset(input: {datasetId: $datasetId, spanIds: $spanIds}) {
        dataset { id name exampleCount }
      }
    }
    """
    add_resp = _graphql(add_q, {"datasetId": dataset_id, "spanIds": span_gids})
    if "errors" in add_resp:
        examples_added = 0
        add_detail = str(add_resp["errors"])[:200]
    else:
        examples_added = (
            add_resp["data"]["addSpansToDataset"]["dataset"].get("exampleCount", 0)
        )
        add_detail = "ok"

    phoenix_ui_url = (
        _base_url().rstrip("/") + f"/datasets/{dataset_id}"
    )

    return {
        "status": "created",
        "dataset_id": dataset_id,
        "dataset_name": dataset_name_out,
        "error_spans_added": len(span_gids),
        "example_count": examples_added,
        "add_status": add_detail,
        "phoenix_ui_url": phoenix_ui_url,
        "next_steps": (
            f"Dataset '{dataset_name_out}' is ready in Phoenix. "
            "Next: use the Phoenix MCP 'list-prompts' tool to find the current "
            "prompt, draft an improved version, then call the MCP 'run-experiment' "
            f"tool with dataset_id='{dataset_id}' to compare old vs new prompt."
        ),
    }
