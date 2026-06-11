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
| **Analysis → Cost** | Token usage and estimated USD cost per trace, priced for the configured Gemini model. |
| **Chat** | Ask in plain English or Spanish: *"Why is my agent failing?"* — the built-in Gemini agent pulls real traces from Phoenix and gives you a root-cause diagnosis. |

The **`i` button** in the top bar explains how to use the app.

## Prerequisites

- Python 3.10–3.12
- [uv](https://docs.astral.sh/uv/)
- Arize Phoenix account — API key at [app.phoenix.arize.com](https://app.phoenix.arize.com)
- Google API key (Gemini) **or** GCP project with Vertex AI

## Live Demo

> **[agentwatch-v4x7.onrender.com](https://agentwatch-v4x7.onrender.com)** — opens directly, no login needed.
> First load may take ~30 s (free tier cold start).

## Deploy your own (free, 5 minutes)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Fork this repo
2. Go to [render.com](https://render.com) → New → Web Service → connect your fork
3. Render auto-detects `render.yaml`
4. Set environment variables in Render dashboard:
   - `PHOENIX_API_KEY` — from [app.phoenix.arize.com](https://app.phoenix.arize.com) → Settings → API Keys
   - `PHOENIX_COLLECTOR_ENDPOINT` — e.g. `https://app.phoenix.arize.com/s/your-space`
   - `GOOGLE_API_KEY` — from [aistudio.google.com](https://aistudio.google.com)
5. Click **Deploy**

Or use the in-app setup screen — open the URL and click **Connect Phoenix Account**.

## Local development

```bash
git clone https://github.com/your-username/agentwatch
cd agentwatch
make setup   # installs deps + creates .env
# edit .env — fill in your API keys
make dev     # starts on http://localhost:8080
```

## Configuration (`.env`)

```env
# Google Gemini — pick one:
GOOGLE_API_KEY=your_key           # simplest
# or Vertex AI:
# GOOGLE_GENAI_USE_VERTEXAI=1
# GOOGLE_CLOUD_PROJECT=your-project-id
# GOOGLE_CLOUD_LOCATION=global     # Gemini 3 models are served from the global endpoint

# Arize Phoenix Cloud
PHOENIX_API_KEY=px_live_...
PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com/s/your-space

# Optional — defaults to gemini-3-flash-preview
GEMINI_MODEL=gemini-3-flash-preview
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

## Known Limitations & Free-Tier Reality

This project was built and deployed on Render's free tier (512 MB RAM, 60 s nginx timeout). If you clone and self-host, be aware of the following:

### Render free tier constraints
- **Cold starts**: The service sleeps after 15 min of inactivity. First request takes 20–30 s.
- **OOM risk on heavy workloads**: The Google ADK `InMemoryRunner` is too memory-heavy for 512 MB. The chat endpoint was rewritten to use the `google.genai` client directly to avoid this. If you upgrade to a paid instance (≥1 GB RAM) you can re-enable the full ADK runner in `agent/agentwatch_core/agent.py`.
- **Evals & A/B timeout at N > 1**: LLM-as-judge calls chain synchronously. On Render free tier, Nginx cuts connections at 60 s. The UI defaults `N=1`; if you have a faster host, raise it.
- **No persistent sessions**: Chat sessions live in memory. A cold start wipes all history.

### Gemini model naming
- If you set `GEMINI_MODEL=gemini-3-flash-preview` and you are **not** on Vertex AI (e.g. using a plain `GOOGLE_API_KEY`), the model ID will be rejected. Use `gemini-2.5-flash` for the Gemini API.
- Vertex AI exposes some models only under region-specific endpoints. The app defaults to the `global` location for Gemini 3 models.

### Phoenix MCP (Node.js tools)
- The full Phoenix MCP toolset (prompt versioning, experiment management) requires `npx` at runtime. Render's Python-only service has no Node.js. The app detects this and falls back to the lean Python tools automatically — but if you want the full MCP capability, run locally or on a Docker-based host that includes Node.

### Demo video
- Not recorded before the submission deadline. The director script (`director.js`, not committed) automates a 3-minute walkthrough for OBS. A/B and Evals run live during recording (not pre-fetched) to show authentic latency to judges.

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
