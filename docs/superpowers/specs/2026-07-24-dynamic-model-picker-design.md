# Dynamic model picker, verify, and recovery — Design

**Date:** 2026-07-24  
**Status:** Approved for implementation planning (pending user review of this file)  
**Approach:** B — Shared resolver + tool smoke test + remap/resume + auto-launch local runner

## Problem

Local (LM Studio) and OpenRouter model catalogs change outside this app. Static preset IDs can disappear, the local runner may not be serving, and mid-run model/tool failures abort full scans. The UI needs to stay resilient without changing which agents use quick vs deep models, or the preset → analyst scan patterns.

## Goals

1. Resolve preferred model IDs against **live** catalogs; if missing, pick a **closest match** in the same role/provider constraints.
2. If the local model runner is not serving, the **web service automatically launches** it (no user click).
3. **Tool-capable smoke test** before a full Research run or Screen & Queue.
4. On mid-scan model/tool failure: **remap** the failed role and **resume** from the last successful agent.
5. Preserve existing quick/deep routing and OpenRouter free-tier analyst guardrails.

## Non-goals

- Per-analyst model maps
- Changing `setup.py` agent → quick/deep bindings
- Replacing preset → analyst checkbox patterns
- Expanding paid OpenRouter catalog beyond current filter needs for matching
- User-driven copy-paste launch instructions as the primary path

## Current behavior (baseline)

- Presets in `webapp/model_presets.py` pin provider, deep/quick models, and often default analysts.
- Live lists already come from `GET /api/models` / `GET /api/llm-health` via `probe_llm_endpoint`.
- Graph routing (`tradingagents/graph/setup.py`):
  - **Quick:** Market, Sentiment, News, Fundamentals; Bull/Bear; Trader; risk debators; reflector; signal processor
  - **Deep:** Research Manager; Portfolio Manager
- `webapp/runs.py` already supports `resume_run` / `_first_resume_node`.
- OpenRouter free guardrails block high-cost free-tier analyst combos unless overridden.

## Design

### 1. Shared model resolver

**Module:** `webapp/model_resolution.py`

Used by UI load, verify, analyze, queue-screen, and mid-run recovery.

**Inputs (per role `quick` | `deep`):** preferred model ID, provider, backend URL, live catalog (same filters as today: OpenRouter tool-capable; local non-embedding).

**Resolution order:**

1. Exact match in catalog → use it.
2. Else closest match within that role’s catalog:
   - Prefer same family/prefix (`meta-llama/`, `qwen/`, `google/gemma`, etc.)
   - Prefer same tier flags (`:free` stays `:free` when preferred was free)
   - Prefer tool-capable (`supported_parameters` includes `tools` / known allowlists)
   - Prefer larger instruct/chat over coder/tiny on ties
   - Never cross hybrid roles (quick catalog ≠ deep catalog)
3. Else fail soft with a clear error (do not invent IDs outside the catalog).

**Output:** `{ requested, resolved, remapped: bool, reason, catalog_fingerprint }`

UI and run records store remaps so History shows what actually ran.

**Unchanged:** `resolve_llm_config`, `setup.py` bindings, preset analyst patterns, free-tier guardrails.

### 2. Auto-start local runner

**Module:** `webapp/llm_launch.py`

**When:** Any path that needs a local / `openai_compatible` route finds the backend unreachable (health, verify, analyze, queue, mid-run recovery).

**Flow:**

1. Probe local backend URL.
2. If down → exec configured launcher (non-interactive).
3. Poll `/models` until ready or timeout.
4. Continue resolve → smoke test / run.
5. Status text only (e.g. “Starting local LLM…”).

**Config (env):**

| Variable | Purpose |
|----------|---------|
| `TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD` | Command to start server (and load if combined) |
| `TRADINGAGENTS_LOCAL_LLM_LOAD_CMD` | Optional separate load command (`{model}` placeholder) |
| `TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC` | Wait budget |

**Constraint:** The web process must be able to exec the launcher (host install, or Docker with a host-reachable launcher). On launch/timeout failure → hard error with log detail; no fake model IDs.

Document defaults for Windows + LM Studio CLI (`lms`) in `.env.example` and `docs/MODELS.md`.

### 3. Verify (tool-capable smoke test)

**API:** `POST /api/llm-verify`  
Body: same route fields as analyze (`provider`, hybrid quick/deep, models, backend URLs, preset id).

**Steps:**

1. Auto-start local if needed.
2. Resolve quick (and deep if used).
3. For each active route: one short chat completion that requires a trivial tool call and confirms a valid tool response.
4. Return `{ ok, routes: [...], launch_attempted, notes[] }`.

**UI:**

- **Verify models** in Model settings; auto-run after preset/backend change when local was down or IDs remapped.
- Status shows remaps + smoke pass/fail.
- **Run Analysis** and **Screen & Queue** gated until last verify for the current route signature is `ok`.
- Changing preset/model/URL invalidates verify cache (prefer auto re-verify).

Verify does **not** bypass OpenRouter free analyst guardrails.

### 4. Mid-run remap + resume

**Trigger (model-route failures only):**

- Model not found / unloaded / model-id 404
- Tool-calling unsupported / no tool endpoints
- Local connection refused after previously healthy (retry auto-launch once, then remap if still failing)

**Flow:**

1. Persist current reports/progress.
2. Identify failed role from active agent (`DEEP_ROUTE_AGENTS` vs quick).
3. Auto-launch local if that role is local and down.
4. Re-probe → resolve closest match **≠** current failing ID (exclude known-bad for this run).
5. Smoke-test replacement; on failure try next candidate (cap ~3) then hard-fail.
6. Resume via existing `resume_run` / `_first_resume_node` with updated model IDs.
7. Append `recovery_events[]`: `{ at, role, from, to, reason, resume_mode }`.
8. UI live note: remapped + resume node.

**Limits:** One automatic recovery chain per role per run. Non-model errors do not remap. Scheduler LLM runs use the same resolver + recovery when applicable.

### 5. Files and tests

| Path | Role |
|------|------|
| `webapp/model_resolution.py` | Closest-match resolver |
| `webapp/llm_launch.py` | Auto-start + poll |
| `webapp/llm_verify.py` | Tool smoke test |
| `webapp/server.py` | `/api/llm-verify`; wire resolve/launch |
| `webapp/runs.py` | Failure classify → remap → resume; `recovery_events` |
| `webapp/static/app.js`, `index.html` | Verify, gate, status |
| `.env.example`, `docs/MODELS.md` | Launch env + behavior notes |
| Tests | Resolver, launch mock, verify mock, recovery remap+resume |

## Success criteria

- Missing preset model → auto closest match, no crash.
- Local server down → web auto-launches, then verify/run proceeds.
- Verify fails → Run / Screen & Queue blocked.
- Mid-run model/tool failure → remap role + resume from last good node.
- Quick/deep analyst routing and preset analyst patterns unchanged.

## Open implementation notes

- Prefer reusing `probe_llm_endpoint` and OpenRouter tool filters; do not duplicate filter logic.
- Route signature for verify cache should include provider, backend URLs, and resolved (or preferred) model IDs.
- Docker: document that `TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD` must be executable from the web container (host helper or non-Docker web) for auto-launch to work.

## Approval record

- Approach B approved.
- §1 Shared resolver approved.
- §1b Auto-start local runner approved (web service invokes launcher).
- §2 Verify + run gate approved.
- §3 Mid-run remap + resume approved.
- §4 Scope approved.
