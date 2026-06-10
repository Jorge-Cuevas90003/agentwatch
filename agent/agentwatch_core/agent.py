"""AgentWatch root agent.

A Google ADK agent that uses the Arize Phoenix MCP server as its sole tool
provider. Every diagnostic action — fetch traces, list projects, create
datasets, run evaluations — happens through Phoenix MCP.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from instrumentation import setup_tracing
from agentwatch_core.prompt import AGENTWATCH_INSTRUCTION
from agentwatch_core.phoenix_summary import (
    get_failure_traces,
    get_project_trace_summary,
    get_token_usage_stats,
    inspect_trace,
    list_projects_summary,
)
from agentwatch_core.phoenix_evals import (
    run_llm_evals,
    compare_time_windows,
    create_failure_dataset,
)
from agentwatch_core.pricing import current_model

# Load env so PHOENIX_API_KEY, PHOENIX_COLLECTOR_ENDPOINT etc. are available
# both for tracing AgentWatch itself and for the Phoenix MCP subprocess.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
setup_tracing()

_model = current_model()

# Phoenix Cloud base URL (without /v1/traces suffix — MCP server takes the
# hostname only).
_phoenix_base_url = (
    os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "")
    .replace("/v1/traces", "")
    .rstrip("/")
)
_phoenix_api_key = os.environ.get("PHOENIX_API_KEY", "")

# The Phoenix MCP server runs as a child `npx` process. The Gemini agent talks
# to it over stdio for the "full power" tools (datasets, experiments, prompts).
# It only loads when Node/npx is actually on PATH — cloud Python runtimes (e.g.
# Render's native Python service) ship without Node, and in that case the agent
# still works using the lean Python tools below. Without this guard a missing
# `npx` would hang the chat on the MCP handshake timeout.
def _build_phoenix_mcp_toolset() -> McpToolset | None:
    if shutil.which("npx") is None:
        return None
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=[
                    "-y",
                    "@arizeai/phoenix-mcp@latest",
                    "--baseUrl",
                    _phoenix_base_url,
                    "--apiKey",
                    _phoenix_api_key,
                ],
                env={
                    "PHOENIX_API_KEY": _phoenix_api_key,
                    "PHOENIX_BASE_URL": _phoenix_base_url,
                },
            ),
            # npx + first MCP handshake easily takes 15-30s — bump the default
            # 5s timeout so the agent doesn't give up before tools register.
            timeout=60.0,
        ),
    )


_tools = [
    # Lean Python tools — use these FIRST for browsing/triage. No Node needed.
    FunctionTool(func=list_projects_summary),
    FunctionTool(func=get_project_trace_summary),
    FunctionTool(func=get_failure_traces),
    FunctionTool(func=get_token_usage_stats),
    FunctionTool(func=inspect_trace),
    # Eval + self-improvement tools.
    FunctionTool(func=run_llm_evals),
    FunctionTool(func=compare_time_windows),
    FunctionTool(func=create_failure_dataset),
]

# Phoenix MCP — full power for datasets, experiments, prompts, etc. (if available).
_phoenix_toolset = _build_phoenix_mcp_toolset()
if _phoenix_toolset is not None:
    _tools.append(_phoenix_toolset)

root_agent = Agent(
    model=_model,
    name="agentwatch",
    instruction=AGENTWATCH_INSTRUCTION,
    tools=_tools,
)
