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

# Vertex AI on cloud platforms: write service account JSON from env var to temp file.
# Must run at import time, before any Google SDK is initialized.
import tempfile as _tempfile
_gac_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
if _gac_json and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    _tmp = _tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_gac_json)
    _tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name

import httpx
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
_MAX_SESSIONS = 50  # cap so the free-tier instance can't OOM on accumulated runners

_env_path = _root.parent / ".env"


def _is_configured() -> bool:
    return bool(os.environ.get("PHOENIX_API_KEY", "").strip())


# ── Setup endpoints ───────────────────────────────────────────────────────────

@app.get("/api/setup/status")
def api_setup_status():
    from agentwatch_core.pricing import current_model, pricing_note
    return {
        "configured": _is_configured(),
        "model": current_model(),
        "pricing_note": pricing_note(),
    }


class SetupRequest(BaseModel):
    phoenix_api_key: str
    phoenix_endpoint: str
    google_api_key: Optional[str] = None


@app.post("/api/setup")
async def api_setup(req: SetupRequest):
    """Save credentials to .env and validate Phoenix connection."""
    # Validate Phoenix key first
    endpoint = req.phoenix_endpoint.rstrip("/").replace("/v1/traces", "")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(
                f"{endpoint}/v1/projects",
                headers={"Authorization": f"Bearer {req.phoenix_api_key}"},
            )
        if r.status_code not in (200, 201):
            raise HTTPException(400, f"Phoenix rejected the key (HTTP {r.status_code}). Check your API key and endpoint.")
    except httpx.RequestError as e:
        raise HTTPException(400, f"Could not reach Phoenix: {e}")

    # Write / update .env (local only — on cloud platforms env vars come from dashboard)
    _is_cloud = bool(os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT"))
    if not _is_cloud:
        lines: list[str] = []
        if _env_path.exists():
            lines = _env_path.read_text(encoding="utf-8").splitlines()

        def _set(key: str, val: str):
            for i, ln in enumerate(lines):
                if ln.startswith(f"{key}=") or ln.startswith(f"# {key}="):
                    lines[i] = f"{key}={val}"
                    return
            lines.append(f"{key}={val}")

        _set("PHOENIX_API_KEY", req.phoenix_api_key)
        _set("PHOENIX_COLLECTOR_ENDPOINT", req.phoenix_endpoint)
        if req.google_api_key:
            _set("GOOGLE_API_KEY", req.google_api_key)

        _env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Reload env in-process (works on both local and cloud)
    os.environ["PHOENIX_API_KEY"] = req.phoenix_api_key
    os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = req.phoenix_endpoint
    if req.google_api_key:
        os.environ["GOOGLE_API_KEY"] = req.google_api_key

    return {"ok": True, "message": "Connected to Phoenix successfully."}


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
        # Bound the in-memory session store. Each session keeps an InMemoryRunner
        # alive; on a 512MB free tier with multiple judges trying the demo these
        # would accumulate until OOM. Evict the oldest once we pass the cap (dicts
        # preserve insertion order, so the first key is the least-recently-created).
        while len(_sessions) >= _MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)), None)
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
