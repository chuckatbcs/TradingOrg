# LLM model guide

TradingAgents runs a **multi-agent LangGraph pipeline** with many tool calls per run. Model choice matters for both **local** (LM Studio) and **cloud** (OpenRouter, Groq, …) setups.

## Context-window guidance

TradingAgents now applies prompt safeguards, but the LLM server still enforces its own context window. Match the app budget to the model you loaded:

| LM Studio context | Recommended use | App setting |
|-------------------|-----------------|-------------|
| **8K / 8192** | Conservative runs: Market only, Market+News, or **Hybrid budget mode**. Avoid Fundamentals on full pipelines. | `TRADINGAGENTS_CONTEXT_WINDOW=8192` or web **Context window tokens = 8192** |
| **16K / 16384** | Preferred for **Hybrid: Local quick + OpenRouter deep** with all analysts. | `TRADINGAGENTS_CONTEXT_WINDOW=16384` |
| **32K / 32768** | Maximum headroom for full analysts plus longer debate/history, if VRAM allows. | `TRADINGAGENTS_CONTEXT_WINDOW=32768` |

In **LM Studio**, open the loaded model, go to **Developer** settings, set **Context Length** to the same number, then restart or reload the Local Server if LM Studio asks. If LM Studio is still at 8192 and a run uses Fundamentals/full analysts, the server may reject the request with `n_keep >= n_ctx` before the app can recover.

## OpenRouter (cloud, free tier)

OpenRouter is already a first-class provider (`llm_provider: openrouter`). It uses the OpenAI-compatible API at `https://openrouter.ai/api/v1` and reads **`OPENROUTER_API_KEY`** from the environment.

### Feasibility: do free models support tools?

**Yes.** TradingAgents requires function/tool calling for agent loops. On OpenRouter, filter [models with `tools` support](https://openrouter.ai/models?supported_parameters=tools) and append **`:free`** to the model ID. As of mid-2026, **19+ free variants** advertise tool calling, including:

| Model ID | Notes |
|----------|-------|
| `meta-llama/llama-3.3-70b-instruct:free` | **Recommended default** — strong general agent, native `tools` |
| `google/gemma-4-26b-a4b-it:free` | Reliable tool JSON; good for market + news |
| `qwen/qwen3-coder:free` | Code-oriented; good structured output |
| `openai/gpt-oss-20b:free` | Smaller, faster free option |
| `openrouter/free` | Auto-picks a free model that supports your request (including tools) |

Verify current availability on [openrouter.ai/models](https://openrouter.ai/models) — free roster changes.

### Models that FAIL (no tool support)

Do **not** use these for TradingAgents agent runs — they return `404 - No endpoints found that support tool use`:

| Model ID | Why it fails |
|----------|----------------|
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | Reasoning-only route; no `tools` in supported_parameters |
| Other `*:reasoning*` free models | Same — optimized for chain-of-thought, not function calling |

The web UI filters the model dropdown to **tool-capable** `:free` models only. If you pick a custom ID, `/api/analyze` validates tool support before starting a run.

### Rate limits (free `:free` models)

| Account | Daily limit | Per-minute |
|---------|-------------|------------|
| No purchase | **50 requests/day** | **20 RPM** |
| $10+ lifetime credits | **1,000 requests/day** | **20 RPM** |

A full TradingAgents run can consume **dozens** of LLM calls. Start with **Market analyst only** and `max_debate_rounds=1`. Failed requests still count toward the daily quota.

In the hybrid preset, only the **deep** synthesis calls use OpenRouter by default. The high-call analyst/tool-loop work uses local LM Studio, so all-analyst runs are not blocked solely because the deep route is an OpenRouter `:free` model. The UI still warns because final synthesis consumes OpenRouter quota.

### Free-tier operating strategy

Use OpenRouter free as a **single-name, first-pass** workflow:

1. Pick the **OpenRouter Free Budget - Market only** preset.
2. Run **Market analyst only** with debate rounds `1` and risk rounds `1`.
3. Queue fewer tickers manually; avoid bulk queues and all-analyst sweeps.
4. Add one extra analyst only when the Market report needs more context.
5. Avoid selecting all analysts on `:free` models. The web UI warns at 2 analysts and requires an explicit override for 3+ analysts because these runs commonly hit `429`/quota failures.

If you need all four analysts, many tickers, or repeated intraday screening, use **local LM Studio** or add OpenRouter credits. OpenRouter accounts with $10+ lifetime credits currently get a higher free-model daily cap (about **1,000 requests/day**), but the **20 RPM** limit still applies.

### `.env` (recommended — native provider)

```env
TRADINGAGENTS_LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
TRADINGAGENTS_DEEP_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free
TRADINGAGENTS_QUICK_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free
TRADINGAGENTS_TEMPERATURE=0.7
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
```

No `TRADINGAGENTS_LLM_BACKEND_URL` is required — the client defaults to `https://openrouter.ai/api/v1`.

### Alternative: `openai_compatible` relay

If you prefer the generic provider slot:

```env
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=https://openrouter.ai/api/v1
OPENAI_COMPATIBLE_API_KEY=sk-or-...
TRADINGAGENTS_DEEP_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free
TRADINGAGENTS_QUICK_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free
```

Use **`OPENROUTER_API_KEY`** with `openrouter`, or **`OPENAI_COMPATIBLE_API_KEY`** with `openai_compatible` — not both for the same endpoint.

### Docker

Cloud providers do **not** need `host.docker.internal`. Put `OPENROUTER_API_KEY` in `.env`; `docker-compose.yml` loads `env_file: .env` for the `web` service.

**Important:** leave `TRADINGAGENTS_LLM_BACKEND_URL` **unset** when using OpenRouter. If it points at LM Studio (`host.docker.internal:1234`), requests will go to the wrong host even with `TRADINGAGENTS_LLM_PROVIDER=openrouter`.

Health check:

```powershell
curl http://localhost:8000/api/llm-health?provider=openrouter
```

### Web UI presets

The Research form **Preset** dropdown includes:

- **OpenRouter Free Budget - Market only** — safest starting point for the 50/day free quota
- **OpenRouter (free) - Llama 3.3 70B** — best starting point
- **OpenRouter (free) - Gemma 4 26B** — stronger tool calling
- **OpenRouter (free) - Qwen3 Coder** — structured JSON
- **Hybrid budget mode (8K) - Market + News** — local quick route with Fundamentals unchecked by default for 8K LM Studio
- **Hybrid: Local quick + OpenRouter deep** — all analysts local for high-call work; final synthesis on OpenRouter

OpenRouter presets leave the **Local backend URL** field disabled because the native `openrouter` provider already defaults to `https://openrouter.ai/api/v1`.

### Other cloud providers (brief)

| Provider | Free tier | Tool calling | Env var |
|----------|-----------|--------------|---------|
| **Groq** | Yes (no card) | Yes (`tools` on Llama/Mixtral/Gemma) | `GROQ_API_KEY` |
| **Google AI** | Limited free quota | Yes (Gemini function calling) | `GOOGLE_API_KEY` |
| **Together** | Trial credits | Yes on many open models | API key via Together console |

Groq is fast but **~30 RPM / 14k RPD** on free tier — agent runs hit limits quickly. Google Gemini free tier works but quota is model-specific. Together is paid-first with trial credits.

Set `TRADINGAGENTS_LLM_PROVIDER=groq` (or `google`) and the matching API key; no local server required.

---

## Hybrid: local quick + OpenRouter deep

Use this when you want all analysts without spending OpenRouter free quota on every tool loop. TradingAgents already separates model usage:

| Role | Route in hybrid preset | Graph nodes |
|------|------------------------|-------------|
| **Quick / high-call** | LM Studio `openai_compatible` at `http://host.docker.internal:1234/v1`, model `qwen/qwen3-4b-2507` | Market, sentiment, news, fundamentals analysts; bull/bear researchers; trader; risk debators; reflector |
| **Deep / low-call synthesis** | OpenRouter, model `meta-llama/llama-3.3-70b-instruct:free` | Research manager, portfolio manager |

### `.env` template

```env
TRADINGAGENTS_LLM_PROVIDER=hybrid

TRADINGAGENTS_QUICK_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_QUICK_LLM_BACKEND_URL=http://host.docker.internal:1234/v1
TRADINGAGENTS_QUICK_THINK_LLM=qwen/qwen3-4b-2507

TRADINGAGENTS_DEEP_LLM_PROVIDER=openrouter
TRADINGAGENTS_DEEP_THINK_LLM=meta-llama/llama-3.3-70b-instruct:free
OPENROUTER_API_KEY=sk-or-...

TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
```

Leave `TRADINGAGENTS_DEEP_LLM_BACKEND_URL` unset for OpenRouter so the native provider default (`https://openrouter.ai/api/v1`) is used. Keep `TRADINGAGENTS_QUICK_LLM_BACKEND_URL` pointed at LM Studio when running inside Docker.

Health checks:

```powershell
curl "http://localhost:8000/api/llm-health?provider=hybrid&quick_provider=openai_compatible&quick_backend_url=http://host.docker.internal:1234/v1&deep_provider=openrouter"
```

The response includes separate `quick` and `deep` health objects. Both must be reachable for the hybrid preset to run.

### Context recommendation

For the full hybrid preset, set LM Studio **Developer → Context Length** to **16384** and set:

```env
TRADINGAGENTS_CONTEXT_WINDOW=16384
```

If your model or VRAM budget only supports **8192**, choose **Hybrid budget mode (8K) - Market + News** in the web UI. That preset excludes Fundamentals by default, keeps debate/risk rounds at 1, and sends `max_context_tokens=8192` with the run.

---

## Local LLM (8 GB VRAM, LM Studio)

On an RTX 4060 Ti (8 GB) with **LM Studio** on the host, model choice and server settings matter more than cloud API tuning.

## How models are used

| Role | Config key | Graph nodes |
|------|------------|-------------|
| **Quick** | `TRADINGAGENTS_QUICK_THINK_LLM` | Market, sentiment, news, fundamentals analysts; bull/bear researchers; trader; risk debators; reflector |
| **Deep** | `TRADINGAGENTS_DEEP_THINK_LLM` | Research manager, portfolio manager (final synthesis) |

Quick-think handles **most** LLM calls. Deep-think runs only twice per full pipeline but needs reliable structured output.

**LM Studio loads one model at a time.** In local-only mode, both env vars must match the model **currently loaded** in LM Studio, or inference will fail or silently mis-route. In hybrid mode, only `TRADINGAGENTS_QUICK_THINK_LLM` needs to match the LM Studio model because deep synthesis is routed to OpenRouter.

## Models available on your host (checked via `/v1/models`)

| Model ID | Type | Recommendation |
|----------|------|----------------|
| `qwen/qwen3-4b-2507` | Qwen3 4B **Instruct** (non-thinking) | **Default — fastest reliable choice you have** |
| `google/gemma-4-e4b` | Gemma 4 E4B, native tool calling | **Alternative** if tool loops / bad JSON persist |
| `qwen/qwen3-4b` | Qwen3 4B base (thinking **on** by default) | **Avoid** — slow agent loops |
| `qwen/qwen3-8b` | Qwen3 8B (thinking by default) | **Avoid** unless thinking disabled; uses ~6–7 GB VRAM |

Past ~85 min runs were likely caused by a **thinking** Qwen variant, very long context (32K+), or all four analysts — not by `qwen3-4b-2507` itself.

## Recommendation summary

### Primary: same model for quick + deep (recommended)

```env
TRADINGAGENTS_DEEP_THINK_LLM=qwen/qwen3-4b-2507
TRADINGAGENTS_QUICK_THINK_LLM=qwen/qwen3-4b-2507
TRADINGAGENTS_TEMPERATURE=0.7
```

- Load **`qwen/qwen3-4b-2507`** in LM Studio (not `qwen3-4b` and not `qwen3-4b-thinking-2507`).
- LM Studio lists this as “Qwen3-4B Instruct 2507” — **non-thinking only**, trained for tool use.

### Alternative: Gemma 4 E4B (tool-calling reliability)

```env
TRADINGAGENTS_DEEP_THINK_LLM=google/gemma-4-e4b
TRADINGAGENTS_QUICK_THINK_LLM=google/gemma-4-e4b
```

Use when the model repeatedly hallucinates tool calls or drops parameters. Slightly higher VRAM (~5 GB) — use **8K context** in LM Studio.

### Split quick/deep (only if you run two LM Studio instances or swap models)

| Quick | Deep | Notes |
|-------|------|-------|
| `qwen/qwen3-4b-2507` | `google/gemma-4-e4b` | Theoretical quality gain on final decision; **not practical** with a single LM Studio server |
| `qwen/qwen3-4b-2507` | `qwen/qwen3-8b` | 8B does not fit comfortably with long context on 8 GB; thinking mode makes it worse |

**Verdict:** On 8 GB + one LM Studio server, use the **same** small instruct model for both slots.

## Comparison table (8 GB VRAM, LM Studio, Q4_K_M)

| Model | Est. speed | Tool reliability | VRAM (model) | Context sweet spot | Notes |
|-------|------------|------------------|--------------|-------------------|-------|
| **Qwen3-4B-Instruct-2507** | ~50–90 tok/s | Good | ~2–3 GB | **8K–16K** | Best speed/reliability balance; **use this** |
| **Gemma 4 E4B** | ~45–55 tok/s | Very good (native FC) | ~5 GB | **8K** | Better tool JSON; less KV headroom |
| Qwen3-4B (base) | ~30–50 tok/s | Good | ~2 GB | 8K | Thinking tokens bloat latency |
| Qwen3-8B | ~25–40 tok/s | Good | ~5–6 GB | 8K only | Thinking mode; tight on 8 GB |
| Llama 3.2 3B | ~60+ tok/s | Moderate | ~2 GB | 8K–16K | Fast but weaker tool calling |
| Phi-4-mini 3.8B | ~50+ tok/s | Good JSON | ~3 GB | 8K–16K | Strong structured output; not in your LM library |

Run time estimates (market-only analyst, 8K context, non-thinking):

| Setup | Typical wall time |
|-------|-------------------|
| Market only | **3–8 min** |
| Market + news | **8–15 min** |
| All 4 analysts | **15–35 min** |
| All 4 + thinking model | **30–90+ min** (avoid) |

## LM Studio settings

1. **Load** `qwen/qwen3-4b-2507` (or `google/gemma-4-e4b`).
2. **Developer → Context Length:** **8192** for budget mode; **16384** preferred for Hybrid full analysts; **32768** only if VRAM allows. Avoid 256K on 8 GB — KV cache eats VRAM and slows every token.
3. **GPU offload:** maximum layers.
4. **Sampling** (non-thinking): Temperature **0.7**, Top-P **0.8**, Top-K **20** (matches Qwen instruct defaults).
5. **Disable thinking** on any Qwen3 model that is not explicitly “Instruct 2507”:
   - Prompt template (Jinja), first line: `{%- set enable_thinking = false %}`
   - Or Developer → Inference → Custom Fields → Enable Thinking = off
6. Start **Local Server** on port **1234**.

Verify:

```powershell
curl http://localhost:1234/v1/models
curl http://localhost:8000/api/llm-health
```

## `.env` template (LM Studio + Docker)

```env
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=http://host.docker.internal:1234/v1
TRADINGAGENTS_DEEP_THINK_LLM=qwen/qwen3-4b-2507
TRADINGAGENTS_QUICK_THINK_LLM=qwen/qwen3-4b-2507
TRADINGAGENTS_TEMPERATURE=0.7
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
TRADINGAGENTS_CONTEXT_WINDOW=8192
```

## Web UI presets (local)

The Research form **Model settings** panel includes a **Preset** dropdown:

- **OpenRouter (free)** presets — cloud; set `OPENROUTER_API_KEY` in `.env`
- **Hybrid budget mode (8K)** — local quick + OpenRouter deep, Market+News only, safest for LM Studio 8192 context.
- **Hybrid: Local quick + OpenRouter deep** — all analysts, requires LM Studio 16K preferred.
- **Fast (local 8GB)** — `qwen/qwen3-4b-2507` for both; start with Market analyst only.
- **Balanced (local)** — `google/gemma-4-e4b` for both; better tool calling, slightly slower.
- **Custom** — use the deep/quick dropdowns manually.

Local and hybrid presets prefill **Local backend URL** with `http://host.docker.internal:1234/v1` for Docker. If you run the web server directly on the host instead of Docker, change it to `http://localhost:1234/v1`. The server rejects `openai_compatible` runs without a backend URL before enqueueing them.

## Smoke test (market-only AAPL)

```powershell
# Start run
$body = @{
  ticker = "AAPL"
  trade_date = (Get-Date).ToString("yyyy-MM-dd")
  analysts = @("market")
  provider = "openai_compatible"
  backend_url = "http://host.docker.internal:1234/v1"
  deep_model = "qwen/qwen3-4b-2507"
  quick_model = "qwen/qwen3-4b-2507"
} | ConvertTo-Json
$r = Invoke-RestMethod -Uri "http://localhost:8000/api/analyze" -Method POST -Body $body -ContentType "application/json"
$r.run_id

# Poll until completed (up to ~15 min)
```

Expect **completed** with a `market_report` and `final_trade_decision` in under 15 minutes when LM Studio has the matching model loaded at 8K context.

## Models to download later (optional)

If you outgrow 4B instruct models:

| Model | VRAM | Why |
|-------|------|-----|
| Qwen3.5-9B Q4_K_M | ~5.5 GB @ 8K | Best 8 GB “quality” tier in 2026 benchmarks; still fits at 8K |
| Phi-4-mini Q4 | ~3 GB | Excellent structured JSON for agent loops |

Do **not** use Qwen “Thinking” or QwQ variants for this pipeline unless you accept 3–10× latency.

## Dynamic model resolve, verify, and recovery

The web UI no longer depends on a preset model ID remaining forever:

1. **Resolve** — preferred quick/deep IDs are matched against the live `/models` catalog; if missing, the closest tool-capable match in the same role is selected.
2. **Auto-launch** — if a local (`openai_compatible` / Ollama) backend is down, the web service runs `TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD` (and optional `TRADINGAGENTS_LOCAL_LLM_LOAD_CMD`) then polls until ready.
3. **Verify** — `POST /api/llm-verify` performs a tool-capable smoke test; Run Analysis and Screen & Queue stay gated until verify passes.
4. **Recover** — if a run fails with model-not-found / no-tool-endpoints / local connection errors, that role is remapped and the run resumes from the last completed agent (one automatic chain per role).

Quick vs deep agent routing is unchanged: analysts and tool-heavy agents use quick; Research Manager and Portfolio Manager use deep.
