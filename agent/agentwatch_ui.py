"""AgentWatch — Streamlit demo UI.

Run with:
    uv run streamlit run agent/agentwatch_ui.py
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path
from typing import Iterator

# Bootstrap: ensure agent/ dir is on sys.path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import streamlit as st

st.set_page_config(
    page_title="AgentWatch",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Lazy imports ─────────────────────────────────────────────────────────────
from agentwatch_core.phoenix_summary import (
    get_failure_traces,
    get_project_trace_summary,
    get_token_usage_stats,
    inspect_trace,
    list_projects_summary,
)
from agentwatch_core.phoenix_evals import (
    EVAL_PROMPTS,
    compare_time_windows,
    create_failure_dataset,
    run_llm_evals,
)

# ── Phoenix UI base URL (for deep-links) ────────────────────────────────────
_PHOENIX_UI = (
    os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "")
    .replace("/v1/traces", "")
    .rstrip("/")
)


# ── Async runner with persistent session ────────────────────────────────────

async def _ensure_agent_session():
    """Create agent runner + session once; reuse across chat turns."""
    if "aw_runner" not in st.session_state:
        # Import here so MCP subprocess starts only when the user opens Chat tab
        from agentwatch_core.agent import root_agent
        from google.adk.runners import InMemoryRunner

        app_name = "agentwatch_ui"
        user_id = "streamlit_user"
        session_id = secrets.token_hex(8)

        runner = InMemoryRunner(agent=root_agent, app_name=app_name)
        await runner.session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        st.session_state["aw_runner"] = runner
        st.session_state["aw_session_id"] = session_id
        st.session_state["aw_user_id"] = user_id
        st.session_state["aw_app_name"] = app_name

    return (
        st.session_state["aw_runner"],
        st.session_state["aw_session_id"],
        st.session_state["aw_user_id"],
        st.session_state["aw_app_name"],
    )


def _run_agent(user_text: str) -> Iterator[str]:
    """Yield text/tool-call chunks from AgentWatch, reusing the same session."""
    from google.genai import types

    async def _stream():
        runner, session_id, user_id, app_name = await _ensure_agent_session()
        chunks = []
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=user_text)]
            ),
        ):
            content = getattr(event, "content", None)
            if not content or not getattr(content, "parts", None):
                continue
            for part in content.parts:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(text)
                fc = getattr(part, "function_call", None)
                if fc:
                    args = dict(fc.args) if fc.args else {}
                    chunks.append(f"\n\n`→ {fc.name}({args})`\n\n")
                fr = getattr(part, "function_response", None)
                if fr:
                    preview = str(fr.response)[:120] if fr.response else ""
                    chunks.append(f"`← {fr.name}: {preview}…`\n\n")
        return chunks

    return iter(asyncio.run(_stream()))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _render_span_detail(detail: dict, errors_only: bool = False):
    """Render a span-level trace inspection result inline."""
    if detail.get("error"):
        st.error(detail["error"])
        return

    spans = detail.get("spans", [])
    if errors_only:
        spans = [s for s in spans if s.get("status_code") == "ERROR" or s.get("exception")]

    for span in spans:
        kind = span.get("kind", "")
        icon = {"LLM": "🤖", "TOOL": "🔧", "CHAIN": "⛓️", "AGENT": "🕵️"}.get(kind, "●")
        lat_ms = span.get("latency_ms")
        lat_str = f"{lat_ms/1000:.2f}s" if lat_ms else "?"
        status = span.get("status_code", "?")
        status_icon = "🔴" if status == "ERROR" else "🟢"

        with st.expander(
            f"{icon} **{span.get('name', '?')}** — {kind} — {lat_str} — {status_icon} {status}"
        ):
            meta_cols = st.columns(3)
            if span.get("tool_name"):
                meta_cols[0].caption(f"tool: `{span['tool_name']}`")
            if span.get("llm_model"):
                meta_cols[1].caption(f"model: `{span['llm_model']}`")
            if span.get("exception_type"):
                meta_cols[2].caption(f"exc: `{span['exception_type']}`")

            if span.get("exception"):
                st.error(f"**Exception:** {span['exception']}")

            if span.get("input_preview"):
                with st.expander("Input preview"):
                    st.code(span["input_preview"], language="text")
            if span.get("output_preview"):
                with st.expander("Output preview"):
                    st.code(span["output_preview"], language="text")


def _latency_badge(ms: float | None) -> str:
    if ms is None:
        return "⬜ —"
    s = ms / 1000
    if ms < 10_000:
        return f"🟢 {s:.1f}s"
    if ms < 30_000:
        return f"🟡 {s:.1f}s"
    return f"🔴 {s:.1f}s"


def _status_icon(status_codes: list) -> str:
    return "🔴" if "ERROR" in status_codes else "🟢"


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔬 AgentWatch")
    st.caption("AI Reliability Engineer — Google ADK + Arize Phoenix")
    st.divider()

    st.subheader("📂 Project")
    if st.button("↻ Refresh", use_container_width=True):
        st.cache_data.clear()

    @st.cache_data(ttl=60)
    def _get_projects():
        return list_projects_summary()

    proj_data = _get_projects()
    project_names = [p["name"] for p in proj_data.get("projects", [])]
    selected_project = st.selectbox(
        "Select project", project_names,
        index=0 if project_names else None,
        label_visibility="collapsed",
    )

    st.divider()
    if _PHOENIX_UI:
        st.markdown(f"[🔗 Open Phoenix UI]({_PHOENIX_UI})")
    st.caption(f"Model: {os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')}")

    if "aw_session_id" in st.session_state:
        st.caption(f"Session: `{st.session_state['aw_session_id']}`")
        if st.button("🔄 Reset chat session"):
            for k in ["aw_runner", "aw_session_id", "aw_user_id", "aw_app_name", "messages"]:
                st.session_state.pop(k, None)
            st.rerun()


# ── Main tabs ─────────────────────────────────────────────────────────────────
tab_overview, tab_failures, tab_evals, tab_trend, tab_tokens, tab_chat = st.tabs([
    "📊 Overview",
    "🚨 Failures",
    "🧪 Evals",
    "📈 Trend",
    "💰 Tokens & Cost",
    "💬 Chat",
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Overview
# ════════════════════════════════════════════════════════════════════════════
with tab_overview:
    st.header(f"Trace Overview — {selected_project or '(no project)'}")
    if not selected_project:
        st.warning("No projects found. Check your Phoenix API key.")
    else:
        col_l, col_r = st.columns([3, 1])
        with col_r:
            limit_ov = st.slider("Traces", 5, 50, 10, key="ov_limit")
        with col_l:
            load_ov = st.button("Load traces", key="btn_ov")

        if load_ov:
            with st.spinner("Fetching…"):
                data = get_project_trace_summary(selected_project, limit=limit_ov)

            s = data.get("stats", {})
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Traces", s.get("trace_count", 0))
            avg_ms = s.get("avg_latency_ms")
            c2.metric("Avg Latency", f"{avg_ms/1000:.1f}s" if avg_ms else "—")
            max_ms = s.get("max_latency_ms")
            c3.metric("Max Latency", f"{max_ms/1000:.1f}s" if max_ms else "—")
            c4.metric("Errors", "🔴 Yes" if s.get("any_errors") else "🟢 None")
            st.divider()

            for t in data.get("traces", []):
                icon = _status_icon(t.get("status_codes", []))
                lat = _latency_badge(t.get("latency_ms"))
                tools = ", ".join(t.get("tool_calls") or []) or "—"
                with st.expander(
                    f"{icon} `{t['trace_id'][:16]}…` {lat} | "
                    f"{t.get('llm_calls', 0)} LLM · {len(t.get('tool_calls') or [])} tools | "
                    f"{t.get('root_span', '?')}"
                ):
                    col_a, col_b, col_c = st.columns(3)
                    col_a.caption(f"Start: {(t.get('start_time') or '')[:19]}")
                    col_b.caption(f"Spans: {t.get('span_count', 0)}")
                    col_c.caption(f"Tools: {tools}")

                    if st.button("🔍 Inspect", key=f"insp_{t['trace_id']}"):
                        with st.spinner("Fetching spans…"):
                            detail = inspect_trace(t["trace_id"], selected_project)
                        _render_span_detail(detail)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Failures
# ════════════════════════════════════════════════════════════════════════════
with tab_failures:
    st.header(f"Error Traces — {selected_project or '(no project)'}")
    if not selected_project:
        st.warning("Select a project.")
    else:
        col_l, col_r = st.columns([3, 1])
        with col_r:
            limit_fail = st.slider("Max failures", 5, 50, 20, key="fail_limit")
        with col_l:
            load_fail = st.button("Load failures", key="btn_fail")

        if load_fail:
            with st.spinner("Fetching error traces…"):
                fdata = get_failure_traces(selected_project, limit=limit_fail)

            count = fdata.get("failure_count", 0)
            if count == 0:
                st.success("🎉 No error traces found!")
            else:
                c1, c2 = st.columns(2)
                c1.metric("Failed traces", count, delta=f"-{count}", delta_color="inverse")
                avg_fl = fdata.get("avg_latency_ms")
                c2.metric("Avg failure latency", f"{avg_fl/1000:.1f}s" if avg_fl else "—")

                st.divider()

                for t in fdata.get("traces", []):
                    lat = _latency_badge(t.get("latency_ms"))
                    tools = ", ".join(t.get("tool_calls") or []) or "—"
                    with st.expander(
                        f"🔴 `{t['trace_id'][:16]}…` {lat} | "
                        f"{t.get('llm_calls', 0)} LLM | {t.get('root_span', '?')}"
                    ):
                        st.caption(f"Tools: {tools}")

                        if st.button("Inspect failure", key=f"fi_{t['trace_id']}"):
                            with st.spinner("Fetching spans…"):
                                detail = inspect_trace(t["trace_id"], selected_project)
                            _render_span_detail(detail, errors_only=False)

                st.divider()
                st.subheader("Self-improvement loop")
                ds_name = st.text_input(
                    "Dataset name",
                    value=f"failures-{selected_project}",
                    key="ds_name",
                )
                if st.button("📦 Create Phoenix dataset from failures", key="btn_ds"):
                    with st.spinner("Creating dataset…"):
                        ds = create_failure_dataset(
                            selected_project,
                            dataset_name=ds_name,
                            limit=limit_fail,
                        )
                    if ds.get("status") == "created":
                        st.success(
                            f"✅ Dataset **{ds['dataset_name']}** created — "
                            f"{ds.get('error_spans_added', 0)} error spans added."
                        )
                        st.info(ds.get("next_steps", ""))
                        if ds.get("phoenix_ui_url"):
                            st.markdown(f"[Open in Phoenix UI]({ds['phoenix_ui_url']})")
                    else:
                        st.error(str(ds))


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Evals (LLM-as-judge)
# ════════════════════════════════════════════════════════════════════════════
with tab_evals:
    st.header(f"LLM-as-Judge Evals — {selected_project or '(no project)'}")
    st.caption(
        "Gemini evaluates your agent's spans and posts results as Phoenix annotations. "
        "Results appear in the Phoenix UI under each span."
    )

    if not selected_project:
        st.warning("Select a project.")
    else:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            eval_type = st.selectbox(
                "Eval type",
                list(EVAL_PROMPTS.keys()),
                format_func=lambda x: {
                    "hallucination": "🔍 Hallucination",
                    "relevance": "🎯 Relevance",
                    "qa_correctness": "✅ QA Correctness",
                    "conciseness": "✂️ Conciseness",
                }.get(x, x),
                key="eval_type",
            )
        with col_b:
            eval_limit = st.slider("Spans to evaluate", 1, 10, 5, key="eval_limit",
                                   help="Each span = 1 Gemini call.")
        with col_c:
            st.write("")
            st.write("")
            run_eval = st.button("▶ Run evals", key="btn_evals", use_container_width=True)

        if run_eval:
            with st.spinner(f"Running {eval_type} evals on {eval_limit} spans…"):
                result = run_llm_evals(selected_project, eval_type=eval_type, limit=eval_limit)

            if "error" in result:
                st.error(result["error"])
            else:
                ann = result.get("annotation_status", {})
                c1, c2, c3, c4 = st.columns(4)
                avg = result.get("avg_score")
                c1.metric("Avg score", f"{avg:.2f}" if avg is not None else "—",
                          help="1.0 = perfect, 0.0 = worst")
                c2.metric("Spans evaluated", result.get("evaluated_count", 0))
                label_dist = result.get("label_distribution", {})
                best_label = max(label_dist, key=label_dist.get) if label_dist else "—"
                c3.metric("Dominant label", best_label)
                c4.metric(
                    "Annotations posted",
                    ann.get("posted", 0),
                    help="Posted to Phoenix; visible in span detail view.",
                )

                st.divider()

                # Label distribution bar
                if label_dist:
                    total_labeled = sum(label_dist.values())
                    st.subheader("Label distribution")
                    for label, cnt in sorted(label_dist.items(), key=lambda x: -x[1]):
                        pct = cnt / total_labeled
                        st.progress(pct, text=f"{label}: {cnt} spans ({pct:.0%})")

                st.divider()
                st.subheader("Per-span results")
                for r in result.get("per_span_results", []):
                    score = r.get("score", 0)
                    color = "🟢" if score >= 0.7 else ("🟡" if score >= 0.4 else "🔴")
                    with st.expander(
                        f"{color} `{r.get('span_id', '')[:16]}…` — "
                        f"**{r.get('label', '?')}** (score={score:.2f}) | "
                        f"{r.get('span_name', '')}"
                    ):
                        st.write(f"**Explanation:** {r.get('explanation', '—')}")
                        st.caption(f"trace_id: `{r.get('trace_id', '')[:32]}`")
                        if r.get("status") == "eval_error":
                            st.error(f"Eval error: {r.get('explanation')}")

                if ann.get("posted", 0) > 0 and _PHOENIX_UI:
                    st.info(
                        f"✅ {ann['posted']} annotations posted to Phoenix. "
                        f"[View in Phoenix UI]({_PHOENIX_UI})"
                    )


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — Trend
# ════════════════════════════════════════════════════════════════════════════
with tab_trend:
    st.header(f"Performance Trend — {selected_project or '(no project)'}")
    st.caption("Compares the last N hours against the N hours before that.")

    if not selected_project:
        st.warning("Select a project.")
    else:
        col_l, col_r = st.columns([3, 1])
        with col_r:
            window_h = st.selectbox(
                "Window", [6, 12, 24, 48, 72, 168],
                index=2, format_func=lambda x: f"{x}h", key="trend_window",
            )
        with col_l:
            run_trend = st.button("📈 Compare windows", key="btn_trend")

        if run_trend:
            with st.spinner(f"Comparing last {window_h}h vs prior {window_h}h…"):
                trend = compare_time_windows(selected_project, window_hours=window_h)

            if "error" in trend:
                st.error(trend["error"])
            else:
                verdict = trend.get("verdict", "—")
                if "IMPROVED" in verdict:
                    st.success(f"## {verdict}")
                elif "DEGRADED" in verdict:
                    st.error(f"## {verdict}")
                else:
                    st.info(f"## {verdict}")

                wa = trend.get("window_a_recent", {})
                wb = trend.get("window_b_baseline", {})
                delta = trend.get("delta", {})

                # Metrics table
                metrics = [
                    ("Traces", wa.get("count"), wb.get("count"), None),
                    ("Error rate", wa.get("error_rate"), wb.get("error_rate"),
                     delta.get("error_rate")),
                    ("Avg latency", wa.get("avg_latency_ms"), wb.get("avg_latency_ms"),
                     delta.get("avg_latency_ms")),
                    ("p95 latency", wa.get("p95_latency_ms"), wb.get("p95_latency_ms"),
                     delta.get("p95_latency_ms")),
                    ("Avg LLM calls", wa.get("avg_llm_calls"), wb.get("avg_llm_calls"), None),
                    ("Avg tool calls", wa.get("avg_tool_calls"), wb.get("avg_tool_calls"), None),
                ]

                st.subheader("Metrics comparison")
                header = st.columns([2, 1, 1, 1])
                header[0].markdown("**Metric**")
                header[1].markdown(f"**Recent ({window_h}h)**")
                header[2].markdown(f"**Baseline ({window_h}h before)**")
                header[3].markdown("**Delta**")
                st.divider()

                for name, recent, baseline, d in metrics:
                    cols = st.columns([2, 1, 1, 1])
                    cols[0].write(name)
                    cols[1].write(f"{recent}" if recent is not None else "—")
                    cols[2].write(f"{baseline}" if baseline is not None else "—")
                    if d is not None:
                        # For lower-is-better: negative delta = good
                        arrow = "🟢 ↓" if d < 0 else ("🔴 ↑" if d > 0 else "➡️")
                        cols[3].write(f"{arrow} {d:+.3g}")
                    else:
                        cols[3].write("—")

                st.caption(delta.get("note", ""))


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 — Tokens & Cost
# ════════════════════════════════════════════════════════════════════════════
with tab_tokens:
    st.header(f"Token Usage & Cost — {selected_project or '(no project)'}")
    st.caption("Gemini 2.5 Flash pricing: $0.075/1M input · $0.30/1M output")

    if not selected_project:
        st.warning("Select a project.")
    else:
        col_l, col_r = st.columns([3, 1])
        with col_r:
            tok_limit = st.slider("Traces", 5, 50, 20, key="tok_limit")
        with col_l:
            run_tok = st.button("💰 Analyze cost", key="btn_tok")

        if run_tok:
            with st.spinner("Aggregating token counts…"):
                tdata = get_token_usage_stats(selected_project, limit=tok_limit)

            if "error" in tdata:
                st.error(tdata["error"])
            else:
                agg = tdata.get("aggregate", {})
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Traces analyzed", tdata.get("traces_analyzed", 0))
                c2.metric("Total tokens", f"{agg.get('total_tokens', 0):,}")
                cost = agg.get("estimated_cost_usd", 0)
                c3.metric("Est. cost", f"${cost:.4f}")
                avg_c = agg.get("avg_cost_per_trace_usd", 0)
                c4.metric("Cost/trace", f"${avg_c:.4f}")

                st.caption(agg.get("pricing_note", ""))
                st.divider()

                rows = sorted(
                    tdata.get("per_trace", []),
                    key=lambda x: x.get("total", 0),
                    reverse=True,
                )
                st.subheader("Top traces by token usage")
                for r in rows[:10]:
                    trace_cost = (
                        r.get("prompt", 0) * 0.075 / 1_000_000
                        + r.get("completion", 0) * 0.30 / 1_000_000
                    )
                    st.markdown(
                        f"`{r['trace_id'][:16]}…` — **{r.get('total', 0):,}** tokens "
                        f"({r.get('prompt', 0):,} in / {r.get('completion', 0):,} out) "
                        f"| {r.get('llm_calls', 0)} LLM calls "
                        f"| ~${trace_cost:.5f}"
                    )


# ════════════════════════════════════════════════════════════════════════════
# TAB 6 — Chat
# ════════════════════════════════════════════════════════════════════════════
with tab_chat:
    st.header("💬 Chat with AgentWatch")
    st.caption(
        "AgentWatch maintains a **persistent session** — it remembers context across turns. "
        "Ask it to diagnose failures, run evals, compare trends, or build a full improvement plan."
    )

    # Quick prompts
    st.subheader("Quick prompts")
    qc = st.columns(4)
    quick = [
        ("🔍 Failures", f"Look at '{selected_project}' failures. What's the root cause and what's the fix?"),
        ("🧪 Run evals", f"Run hallucination evals on '{selected_project}'. What's the avg score?"),
        ("📈 Trend", f"Is '{selected_project}' getting better or worse? Compare last 24h vs prior 24h."),
        ("🔧 Improve", f"Run the full self-improvement loop on '{selected_project}': diagnose, eval, create dataset, propose fix."),
    ]
    selected_quick = None
    for i, (label, prompt) in enumerate(quick):
        if qc[i].button(label, use_container_width=True, key=f"qp_{i}"):
            selected_quick = prompt

    st.divider()

    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Ask AgentWatch anything…") or selected_quick

    if user_input:
        st.session_state["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            placeholder = st.empty()
            full = ""
            with st.spinner("AgentWatch is analyzing…"):
                for chunk in _run_agent(user_input):
                    full += chunk
                    placeholder.markdown(full + "▌")
            placeholder.markdown(full)

        st.session_state["messages"].append({"role": "assistant", "content": full})
