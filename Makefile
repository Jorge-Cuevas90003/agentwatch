.PHONY: setup dev help

help:
	@echo "AgentWatch"
	@echo ""
	@echo "  make setup   Install dependencies"
	@echo "  make dev     Start the web server (http://localhost:8080)"

setup:
	uv sync
	@test -f .env || (cp .env.example .env && echo "Created .env — add your API keys then run: make dev")

dev:
	cd agent && uv run uvicorn agentwatch_api:app --port 8080 --reload
