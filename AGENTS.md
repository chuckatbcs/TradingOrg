# Agent Operating Rules

This repository is an AI-assisted trading research framework. Agents may help write code, run research jobs, summarize results, and maintain documentation.

## Hard boundaries

- Do not add live broker execution in this repo without an explicit issue and review plan.
- Do not commit API keys, broker secrets, `.env`, generated reports, or memory logs.
- Do not remove paper-trading defaults or safety warnings.
- Do not make generated LLM text directly executable as broker orders.
- Do not push directly to `main`; use branches and pull requests.
- Do not weaken risk controls, audit logging, or kill-switch behavior.

## Preferred workflow

1. Inspect before editing.
2. Make small, reviewable changes.
3. Prefer deterministic code over LLM judgment where possible.
4. Use JSON schemas for machine-consumed outputs.
5. Save research outputs under `reports/` and lessons under `memory/`; these are ignored by git.
6. Keep Hermes scripts stable and explicit so the home-PC agent runs known commands instead of improvising.

## Architecture rule

```text
AI researches.
TradingOrg produces signals.
A separate app validates strategy and risk.
Broker execution is deterministic and gated.
```

## Model routing rule

Use the cheapest reliable model tier first:

1. deterministic code
2. local Ollama/home-PC model
3. free or cheap cloud model through OpenRouter or similar
4. premium cloud model only when quality or consequence justifies it

Log provider/model details for any machine-consumed signal.
