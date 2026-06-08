"""Run AgentWatch — one diagnostic turn against your Phoenix workspace.

Usage:
    uv run python agent/agentwatch_main.py "Why is my shopping agent slow?"
    uv run python agent/agentwatch_main.py  # uses default prompt
"""

from __future__ import annotations

import asyncio
import io
import secrets
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so we can print box-drawing chars + emojis.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google.adk.runners import InMemoryRunner
from google.genai import types

from instrumentation import setup_tracing
from agentwatch_core.agent import root_agent


async def run_turn(user_text: str) -> None:
    setup_tracing()
    app_name = "agentwatch_session"
    user_id = "local_user"
    session_id = secrets.token_hex(8)

    runner = InMemoryRunner(agent=root_agent, app_name=app_name)
    await runner.session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

    print(f"\n🔍 AgentWatch — analyzing: {user_text!r}\n" + "─" * 60)

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=user_text)]),
    ):
        # Stream model text + tool activity to the console.
        content = getattr(event, "content", None)
        if not content or not getattr(content, "parts", None):
            continue
        for part in content.parts:
            text = getattr(part, "text", None)
            if text:
                print(text, end="", flush=True)
            fc = getattr(part, "function_call", None)
            if fc:
                print(f"\n  → calling tool: {fc.name}({dict(fc.args) if fc.args else ''})")
            fr = getattr(part, "function_response", None)
            if fr:
                preview = str(fr.response)[:200] if fr.response else ""
                print(f"  ← {fr.name} returned: {preview}{'...' if len(str(fr.response)) > 200 else ''}")

    print("\n" + "─" * 60 + "\n✅ Done")


def main() -> None:
    default_msg = (
        "List the projects in my Phoenix workspace, then pull the last 5 traces "
        "from the busiest project. Tell me what you see — any failure patterns, "
        "latency spikes, or obvious issues."
    )
    msg = sys.argv[1] if len(sys.argv) > 1 else default_msg
    asyncio.run(run_turn(msg))


if __name__ == "__main__":
    main()
