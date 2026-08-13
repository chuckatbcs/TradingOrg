# Docker quick start (TradingOrg)

Web UI and autonomous firm scheduler run in the **web** service on port **8000**.

## Prerequisites

- Docker Desktop (Windows)
- Project root `.env` (not committed) with API keys and `TRADINGAGENTS_*` / `FIRM_*` settings
- **LM Studio on the host** (recommended for local LLM):
  1. Install and open [LM Studio](https://lmstudio.ai/)
  2. Download and **load a chat model** (not embedding-only)
  3. Start the **Local Server** (default `http://127.0.0.1:1234`)
  4. Set in `.env`: `TRADINGAGENTS_LLM_BACKEND_URL=http://host.docker.internal:1234/v1`

- **OpenRouter (cloud)** — no `host.docker.internal`; set in `.env`:
  - `TRADINGAGENTS_LLM_PROVIDER=openrouter`
  - `OPENROUTER_API_KEY=...` (from [openrouter.ai/keys](https://openrouter.ai/keys))
  - Model IDs ending in `:free` (see [MODELS.md](MODELS.md#openrouter-cloud-free-tier))

- **Hybrid local + OpenRouter** — recommended when running all analysts on the free tier:
  - `TRADINGAGENTS_LLM_PROVIDER=hybrid`
  - `TRADINGAGENTS_QUICK_LLM_PROVIDER=openai_compatible`
  - `TRADINGAGENTS_QUICK_LLM_BACKEND_URL=http://host.docker.internal:1234/v1`
  - `TRADINGAGENTS_QUICK_THINK_LLM=qwen/qwen3-4b-2507`
  - `TRADINGAGENTS_DEEP_LLM_PROVIDER=openrouter`
  - `TRADINGAGENTS_DEEP_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free`
  - `OPENROUTER_API_KEY=...`

The web UI checks `/api/llm-health` on load. If the dot stays red or models are empty, analyses will fail once agents call the LLM.

**Model choice (8 GB GPU):** see [MODELS.md](MODELS.md) for recommended LM Studio models, context length, and presets. Local default: `qwen/qwen3-4b-2507` (non-thinking instruct). Hybrid default: quick/high-call agents use that local model; deep synthesis uses OpenRouter.

Verify from the host:

```powershell
curl http://localhost:1234/v1/models
```

Verify from inside the container:

```powershell
docker exec tradingorg-web-1 python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:1234/v1/models').read()[:200])"
```

Verify the hybrid routes through the web service:

```powershell
curl "http://localhost:8000/api/llm-health?provider=hybrid&quick_provider=openai_compatible&quick_backend_url=http://host.docker.internal:1234/v1&deep_provider=openrouter"
```

## Commands

```powershell
docker compose build
docker compose up -d web          # start web + firm scheduler
docker compose down               # stop
```

Or use:

```powershell
.\scripts\docker-start.ps1
.\scripts\docker-stop.ps1
```

## URLs

- App: http://localhost:8000
- Health: http://localhost:8000/api/health
- Firm config: http://localhost:8000/api/firm/config

CLI-only runs (no web):

```powershell
docker compose run --rm tradingagents
```
