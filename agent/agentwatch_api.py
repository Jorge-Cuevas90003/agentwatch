"""AgentWatch — FastAPI backend.

Serves the custom HTML frontend and exposes REST + SSE endpoints
for all diagnostic tools. Launch with:

    uv run uvicorn agent.agentwatch_api:app --port 8080 --reload
  or
    uv run python agent/agentwatch_api.py
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

# Bootstrap path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentwatch_core.phoenix_summary import (
    get_failure_traces,
    get_project_trace_summary,
    get_token_usage_stats,
    inspect_trace,
    list_projects_summary,
)
from agentwatch_core.phoenix_evals import (
    compare_time_windows,
    create_failure_dataset,
    run_llm_evals,
)

app = FastAPI(title="AgentWatch", docs_url=None, redoc_url=None)

# ── In-memory chat sessions ──────────────────────────────────────────────────
_sessions: dict = {}


# ── Static files ─────────────────────────────────────────────────────────────
_static = _root / "static"

@app.get("/", response_class=HTMLResponse)
def index():
    return (_static / "index.html").read_text(encoding="utf-8")


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/projects")
def api_projects():
    return list_projects_summary()


@app.get("/api/projects/{project_name}/summary")
def api_summary(project_name: str, limit: int = Query(10, ge=1, le=50)):
    return get_project_trace_summary(project_name, limit=limit)


@app.get("/api/projects/{project_name}/failures")
def api_failures(project_name: str, limit: int = Query(20, ge=1, le=50)):
    return get_failure_traces(project_name, limit=limit)


@app.get("/api/projects/{project_name}/tokens")
def api_tokens(project_name: str, limit: int = Query(20, ge=1, le=50)):
    return get_token_usage_stats(project_name, limit=limit)


@app.get("/api/projects/{project_name}/trend")
def api_trend(project_name: str, window_hours: int = Query(24, ge=1, le=168)):
    return compare_time_windows(project_name, window_hours=window_hours)


@app.get("/api/trace/{trace_id}")
def api_trace(trace_id: str, project: str = "agentwatch"):
    return inspect_trace(trace_id, project_name=project)


class EvalRequest(BaseModel):
    eval_type: str = "hallucination"
    limit: int = 5


@app.post("/api/projects/{project_name}/evals")
def api_evals(project_name: str, req: EvalRequest):
    return run_llm_evals(project_name, eval_type=req.eval_type, limit=req.limit)


class DatasetRequest(BaseModel):
    dataset_name: Optional[str] = None
    limit: int = 10


@app.post("/api/projects/{project_name}/dataset")
def api_dataset(project_name: str, req: DatasetRequest):
    return create_failure_dataset(
        project_name, dataset_name=req.dataset_name, limit=req.limit
    )


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """SSE streaming chat endpoint — reuses the same ADK session."""
    from agentwatch_core.agent import root_agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    session_id = req.session_id or secrets.token_hex(8)

    if session_id not in _sessions:
        app_name = "agentwatch_web"
        runner = InMemoryRunner(agent=root_agent, app_name=app_name)
        await runner.session_service.create_session(
            app_name=app_name, user_id="web_user", session_id=session_id
        )
        _sessions[session_id] = {"runner": runner, "app_name": app_name}

    runner = _sessions[session_id]["runner"]

    async def generate():
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        try:
            async for event in runner.run_async(
                user_id="web_user",
                session_id=session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=req.message)]
                ),
            ):
                content = getattr(event, "content", None)
                if not content or not getattr(content, "parts", None):
                    continue
                for part in content.parts:
                    text = getattr(part, "text", None)
                    if text:
                        yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"
                    fc = getattr(part, "function_call", None)
                    if fc:
                        yield f"data: {json.dumps({'type': 'tool_call', 'name': fc.name, 'args': dict(fc.args) if fc.args else {}})}\n\n"
                    fr = getattr(part, "function_response", None)
                    if fr:
                        preview = str(fr.response)[:300] if fr.response else ""
                        yield f"data: {json.dumps({'type': 'tool_result', 'name': fr.name, 'preview': preview})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:200]})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agentwatch_api:app", host="0.0.0.0", port=8080, reload=False)
