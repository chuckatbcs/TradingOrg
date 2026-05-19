# Hermes Control Plane for TradingOrg

This repository can be run as a Hermes-controlled research and post-game review engine.

## Design

Hermes is the home-PC operator. TradingOrg is the research engine. The trading app or broker executor must remain a separate deterministic, risk-gated system.

```text
Hermes workspace / Hermes WebUI
  -> runs TradingOrg commands and scripts
  -> chooses local/free-cloud/premium model route
  -> saves signal JSON and markdown reports
  -> runs post-game review
  -> appends lessons to memory/lessons.jsonl

TradingOrg
  -> produces research and signal JSON only
  -> never places broker orders

Trading app
  -> consumes signals
  -> applies strategy and risk controls
  -> paper trades first
  -> broker execution later, gated
```

## Non-negotiable safety rules

- TradingOrg output is research only.
- Generated signals must default to `paper_only: true`.
- Broker keys do not belong in this repo.
- Hermes may run scripts, but must not directly place broker orders.
- No agent should push directly to `main`.
- Live trading requires a separate deterministic risk engine, kill switch, audit log, and manual approval path.

## Local model over Tailscale

On the home PC, run Ollama and expose it only on your private Tailscale network.

```bash
ollama serve
ollama pull qwen3:latest
```

On the laptop or inside the container, test:

```bash
curl http://home-pc-tailnet-name:11434/api/tags
curl http://home-pc-tailnet-name:11434/v1/models
```

`.env` example:

```env
LLM_PROVIDER=ollama
LLM_BACKEND_URL=http://home-pc-tailnet-name:11434/v1
OLLAMA_BASE_URL=http://home-pc-tailnet-name:11434/v1
QUICK_THINK_LLM=qwen3:latest
DEEP_THINK_LLM=qwen3:latest
MODEL_ROUTING_MODE=cost_optimized
```

## Free/cheap cloud fallback

OpenRouter can be used as a fallback when the local model is unavailable, slow, or too weak.

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=...
QUICK_THINK_LLM=your/free-or-cheap-model
DEEP_THINK_LLM=your/free-or-cheap-model
```

## Run one ticker

Installed console script:

```bash
tradingagents-analyze-run \
  --ticker NVDA \
  --date 2026-05-19 \
  --provider ollama \
  --backend-url http://home-pc-tailnet-name:11434/v1 \
  --quick-model qwen3:latest \
  --deep-model qwen3:latest \
  --analysts market,news,fundamentals \
  --depth 1 \
  --checkpoint \
  --output-json reports/signals/NVDA/2026-05-19/signal.json
```

Hermes-safe wrapper:

```bash
scripts/hermes/run_single_ticker.sh NVDA 2026-05-19
```

## Run a daily watchlist

```bash
WATCHLIST="SPY,NVDA,AAPL,MSFT" scripts/hermes/run_daily_watchlist.sh
```

## Run post-game review

```bash
scripts/hermes/run_postgame_review.sh NVDA 2026-05-19
```

This creates:

```text
reports/postgame/NVDA/2026-05-19/review.json
memory/lessons.jsonl
```

## Learning loop

The intended loop is:

```text
analyze -> signal -> observe result -> post-game review -> lesson -> next-run context
```

This is not model fine-tuning. It is reflection and memory injection. That is safer, cheaper, and easier to audit.
