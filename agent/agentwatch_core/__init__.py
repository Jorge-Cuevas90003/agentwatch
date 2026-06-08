"""AgentWatch — conversational diagnostic agent for AI agents.

The root_agent is imported lazily to avoid starting the Phoenix MCP
subprocess on every import. Use:

    from agentwatch_core.agent import root_agent
"""

__all__ = ["root_agent"]


def __getattr__(name):
    if name == "root_agent":
        from .agent import root_agent
        return root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
