# AgentWatch

Production monitoring dashboard for AI agents. Connects to **Arize Phoenix** to show you what your agents are doing, where they fail, what they cost, and why — all from a single web UI with a built-in AI assistant for automatic diagnosis.

![AgentWatch UI](https://img.shields.io/badge/stack-Google%20ADK%20%2B%20Phoenix%20%2B%20FastAPI-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Python](https://img.shields.io/badge/python-3.10--3.12-blue)

## What it does

| Tab | What you get |
|---|---|
| **Traces** | Recent runs and failures for any Phoenix project. Click any trace to expand spans. |
| **Analysis → Evals** | LLM-as-judge evaluation (hallucination, relevance, QA, conciseness). Results posted back to Phoenix automatically. |
| **Analysis → Trend** | Compare the last N hours vs the N hours before — get an IMPROVED / DEGRADED / STABLE verdict. |
| **Analysis → Cost** | Token usage and estimated USD cost per trace (Gemini 2.5 Flash pricing). |
| **Chat** | Ask in plain English or Spanish: *"Why is my agent failing?"* — the built-in Gemini agent pulls real traces from Phoenix and gives you a root-cause diagnosis. |

The **`i` button** in the top bar explains how to use the app.

## Prerequisites

- Python 3.10–3.12
- [uv](https://docs.astral.sh/uv/)
- Arize Phoenix account — API key at [app.phoenix.arize.com](https://app.phoenix.arize.com)
- Google API key (Gemini) **or** GCP project with Vertex AI

## Quickstart

```bash
git clone https://github.com/your-username/agentwatch
cd agentwatch
cp .env.example .env
# Edit .env — fill in PHOENIX_API_KEY, PHOENIX_COLLECTOR_ENDPOINT, and GOOGLE_API_KEY
uv sync
uv run uvicorn agent.agentwatch_api:app --port 8080 --reload
```

Open **http://localhost:8080**.

## Configuration (`.env`)

```env
# Google Gemini — pick one:
GOOGLE_API_KEY=your_key           # simplest
# or Vertex AI:
# GOOGLE_GENAI_USE_VERTEXAI=1
# GOOGLE_CLOUD_PROJECT=your-project-id
# GOOGLE_CLOUD_LOCATION=us-central1

# Arize Phoenix Cloud
PHOENIX_API_KEY=px_live_...
PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com/s/your-space

# Optional
GEMINI_MODEL=gemini-2.5-flash
PHOENIX_PROJECT_NAME=agentwatch
```

## Architecture

```
browser  ──►  FastAPI (agent/agentwatch_api.py)
                 ├── /api/projects          Phoenix REST
                 ├── /api/.../summary       Phoenix REST  
                 ├── /api/.../failures      Phoenix REST
                 ├── /api/.../tokens        Phoenix REST
                 ├── /api/.../trend         Phoenix REST
                 ├── /api/.../evals         Gemini LLM-as-judge
                 ├── /api/.../dataset       Phoenix GraphQL
                 └── /api/chat  (SSE)       Google ADK agent
                                               └── Phoenix MCP (npx)
```

The frontend (`agent/static/index.html`) is a single HTML file — Three.js galaxy background, Anime.js animations, Inter font. No build step.

## Language

The UI auto-detects your browser language and switches between **English** and **Spanish**.

## License

Apache-2.0 — see [LICENSE](LICENSE).
