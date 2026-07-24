/* TradingAgents web UI: form -> POST /api/analyze -> poll /api/runs/{id}. */

const $ = (id) => document.getElementById(id);

/** Attach a listener only when the element exists (avoids aborting the whole script). */
function on(id, event, handler) {
  const el = $(id);
  if (el) el.addEventListener(event, handler);
  else console.warn(`UI element #${id} not found`);
}

const REPORT_TABS = [
  ["market_report", "Market"],
  ["sentiment_report", "Sentiment"],
  ["news_report", "News"],
  ["fundamentals_report", "Fundamentals"],
  ["bull_history", "Bull Case"],
  ["bear_history", "Bear Case"],
  ["research_judge", "Research Mgr"],
  ["trader_investment_plan", "Trader"],
  ["risk_history", "Risk Debate"],
  ["risk_judge", "Portfolio Mgr"],
  ["final_trade_decision", "Final Decision"],
];

let currentRunId = null;
let pollTimer = null;
let activeTab = null;
let modelPresets = [];
let appConfig = null;
let activeLlmProvider = null;
let activeBackendUrl = null;
let activeQuickProvider = null;
let activeDeepProvider = null;
let activeQuickBackendUrl = null;
let activeDeepBackendUrl = null;
let activeModelPreset = null;
let analystLabels = {};
let latestMarketDate = null;
let marketDateValidation = null;
let lastVerify = { signature: null, ok: false };
let llmHealthDown = false;

async function fetchJSON(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return resp.json();
}

function fillModelSelect(sel, models, def) {
  if (!sel) return;
  sel.innerHTML = "";
  const opts = models.length ? models : (def ? [def] : []);
  if (!opts.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "(no models configured)";
    sel.appendChild(o);
    return;
  }
  for (const m of opts) {
    const o = document.createElement("option");
    o.value = m;
    o.textContent = m;
    if (m === def) o.selected = true;
    sel.appendChild(o);
  }
}

function selectOption(sel, value) {
  if (!sel || !value) return;
  for (const o of sel.options) {
    if (o.value === value) {
      o.selected = true;
      return;
    }
  }
  const o = document.createElement("option");
  o.value = value;
  o.textContent = value;
  o.selected = true;
  sel.appendChild(o);
}

function fillPresetSelect(presets, activeId) {
  const sel = $("model-preset");
  if (!sel || !presets?.length) return;
  sel.innerHTML = "";
  for (const p of presets) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.label;
    if (p.id === activeId) o.selected = true;
    sel.appendChild(o);
  }
}

function applyModelPreset(preset, cfg) {
  const hint = $("preset-hint");
  if (!preset) return;
  activeModelPreset = preset;
  if (hint) hint.textContent = preset.hint || "";
  const presetProvider = preset.llm_provider || cfg?.llm_provider || activeLlmProvider;
  activeLlmProvider = presetProvider || null;
  activeBackendUrl = Object.prototype.hasOwnProperty.call(preset, "backend_url")
    ? preset.backend_url
    : (cfg?.backend_url || null);
  activeQuickProvider =
    preset.quick_provider ||
    (presetProvider === "hybrid" ? cfg?.quick_provider : presetProvider) ||
    cfg?.llm_provider ||
    null;
  activeDeepProvider =
    preset.deep_provider ||
    (presetProvider === "hybrid" ? cfg?.deep_provider : presetProvider) ||
    cfg?.llm_provider ||
    null;
  activeQuickBackendUrl = Object.prototype.hasOwnProperty.call(preset, "quick_backend_url")
    ? preset.quick_backend_url
    : (cfg?.quick_backend_url || null);
  activeDeepBackendUrl = Object.prototype.hasOwnProperty.call(preset, "deep_backend_url")
    ? preset.deep_backend_url
    : (cfg?.deep_backend_url || null);
  if (preset.deep_think_llm) {
    selectOption($("deep-model"), preset.deep_think_llm);
  }
  if (preset.quick_think_llm) {
    selectOption($("quick-model"), preset.quick_think_llm);
  }
  if (preset.max_debate_rounds && $("debate-rounds")) {
    $("debate-rounds").value = preset.max_debate_rounds;
  }
  if (preset.max_risk_rounds && $("risk-rounds")) {
    $("risk-rounds").value = preset.max_risk_rounds;
  }
  if (preset.max_context_tokens && $("context-window")) {
    $("context-window").value = preset.max_context_tokens;
  }
  if (preset.analysts?.length) {
    for (const input of document.querySelectorAll("#analyst-checks input")) {
      input.checked = preset.analysts.includes(input.value);
    }
  }
  syncLocalBackendField();
  updateLocalBackendWarning();
  updateModelToolWarning();
  updateOpenRouterFreeWarning();
  updateQueueModelSummary();
}

function providerForRole(role) {
  return role === "quick"
    ? (activeQuickProvider || activeLlmProvider || "")
    : (activeDeepProvider || activeLlmProvider || "");
}

function updateModelToolWarning() {
  const warn = $("model-warning");
  if (!warn) return;
  const deep = $("deep-model")?.value || "";
  const quick = $("quick-model")?.value || "";
  const ids = [
    [providerForRole("deep"), deep],
    [providerForRole("quick"), quick],
  ].filter(([, id]) => Boolean(id));
  const bad = ids.some(([provider, id]) => provider === "openrouter" && /reasoning/i.test(id));
  if (bad) {
    warn.textContent =
      "Warning: reasoning models on OpenRouter often lack tool support and will fail agent runs. " +
      "Use meta-llama/llama-3.3-70b-instruct:free or another tool-capable model.";
    warn.classList.remove("hidden");
  } else {
    warn.textContent = "";
    warn.classList.add("hidden");
  }
}

function selectedAnalysts() {
  return [...document.querySelectorAll("#analyst-checks input:checked")]
    .map((c) => c.value);
}

function currentModelRunOptions() {
  return {
    provider: activeLlmProvider || null,
    backend_url: activeLlmProvider === "hybrid" ? null : (activeBackendUrl || null),
    quick_provider: activeLlmProvider === "hybrid" ? (activeQuickProvider || null) : null,
    quick_backend_url: activeLlmProvider === "hybrid" ? (activeQuickBackendUrl || null) : null,
    deep_provider: activeLlmProvider === "hybrid" ? (activeDeepProvider || null) : null,
    deep_backend_url: activeLlmProvider === "hybrid" ? (activeDeepBackendUrl || null) : null,
    deep_model: $("deep-model")?.value || null,
    quick_model: $("quick-model")?.value || null,
    model_preset: $("model-preset")?.value || activeModelPreset?.id || null,
    max_debate_rounds: parseInt($("debate-rounds")?.value || activeModelPreset?.max_debate_rounds || 1) || 1,
    max_risk_rounds: parseInt($("risk-rounds")?.value || activeModelPreset?.max_risk_rounds || 1) || 1,
    max_recur_limit: activeModelPreset?.max_recur_limit || null,
    max_context_tokens:
      parseInt($("context-window")?.value || activeModelPreset?.max_context_tokens || 8192) || 8192,
  };
}

function selectedAnalystText() {
  const names = selectedAnalysts().map((key) => analystLabels[key] || key);
  return names.length ? names.join(", ") : "none selected";
}

function routeText(role, provider, model, backendUrl) {
  const parts = [
    `${role}:`,
    provider || activeLlmProvider || appConfig?.llm_provider || "configured provider",
    model || "configured model",
  ];
  if (backendUrl) parts.push(`@ ${backendUrl}`);
  return parts.join(" ");
}

function updateQueueModelSummary() {
  const el = $("firm-queue-models");
  if (!el) return;
  const opts = currentModelRunOptions();
  const presetLabel =
    $("model-preset")?.selectedOptions?.[0]?.textContent ||
    activeModelPreset?.label ||
    "Configured defaults";
  const quickProvider = activeLlmProvider === "hybrid"
    ? opts.quick_provider
    : (opts.provider || appConfig?.llm_provider);
  const deepProvider = activeLlmProvider === "hybrid"
    ? opts.deep_provider
    : (opts.provider || appConfig?.llm_provider);
  const quickBackend = activeLlmProvider === "hybrid"
    ? opts.quick_backend_url
    : opts.backend_url;
  const deepBackend = activeLlmProvider === "hybrid"
    ? opts.deep_backend_url
    : opts.backend_url;
  el.innerHTML =
    `<b>Screen &amp; Queue will use:</b> ${escapeHTML(presetLabel)}<br>` +
    `${escapeHTML(routeText("Quick", quickProvider, opts.quick_model, quickBackend))}<br>` +
    `${escapeHTML(routeText("Deep", deepProvider, opts.deep_model, deepBackend))}<br>` +
    `Analysts: ${escapeHTML(selectedAnalystText())}`;
}

function openRouterFreeRoles() {
  const deep = $("deep-model")?.value || "";
  const quick = $("quick-model")?.value || "";
  const free = (id) => id === "openrouter/free" || id.endsWith(":free");
  return {
    quick: providerForRole("quick") === "openrouter" && free(quick),
    deep: providerForRole("deep") === "openrouter" && free(deep),
  };
}

function isOpenRouterFreeSelection() {
  const roles = openRouterFreeRoles();
  return roles.quick || roles.deep;
}

function requiresOpenRouterFreeOverride() {
  const analysts = selectedAnalysts();
  return openRouterFreeRoles().quick && (
    analysts.length >= 3 || analysts.includes("fundamentals")
  );
}

function openRouterFreeGuardrailMessage() {
  const roles = openRouterFreeRoles();
  if (!roles.quick && !roles.deep) return "";
  const count = selectedAnalysts().length;
  const hasFundamentals = selectedAnalysts().includes("fundamentals");
  if (roles.quick && hasFundamentals) {
    return (
      "OpenRouter free warning: Fundamentals is tool-heavy and can loop or hit " +
      "free-tier rate/quota limits. Use Hybrid/local routing, uncheck Fundamentals, " +
      "or confirm the override to run anyway."
    );
  }
  if (roles.quick && count >= 3) {
    return (
      "OpenRouter free warning: 3+ analysts can exceed the ~50 requests/day free quota " +
      "or hit 20 RPM. Use the OpenRouter Free Budget preset (Market only), queue fewer names, " +
      "or switch to local LM Studio for all-analyst runs."
    );
  }
  if (roles.quick && count === 2) {
    return (
      "OpenRouter free caution: two analysts may still use many LLM calls. " +
      "Market-only is the safest first pass on the free tier."
    );
  }
  if (roles.deep) {
    return (
      "Hybrid OpenRouter free caution: final synthesis uses OpenRouter quota, " +
      "while high-call analyst/tool-loop work stays local."
    );
  }
  return "";
}

function updateOpenRouterFreeWarning() {
  const warn = $("openrouter-free-warning");
  if (!warn) return;
  const message = openRouterFreeGuardrailMessage();
  if (message) {
    warn.textContent = message;
    warn.classList.remove("hidden");
  } else {
    warn.textContent = "";
    warn.classList.add("hidden");
  }
}

function routeUsesOpenAICompatible() {
  if (activeLlmProvider === "openai_compatible") return true;
  if (activeLlmProvider !== "hybrid") return false;
  return activeQuickProvider === "openai_compatible" || activeDeepProvider === "openai_compatible";
}

function currentLocalBackendUrl() {
  if (activeLlmProvider === "hybrid") {
    if (activeQuickProvider === "openai_compatible") return activeQuickBackendUrl || "";
    if (activeDeepProvider === "openai_compatible") return activeDeepBackendUrl || "";
    return "";
  }
  return activeLlmProvider === "openai_compatible" ? (activeBackendUrl || "") : "";
}

function setCurrentLocalBackendUrl(value) {
  const cleaned = value.trim() || null;
  if (activeLlmProvider === "hybrid") {
    if (activeQuickProvider === "openai_compatible") activeQuickBackendUrl = cleaned;
    if (activeDeepProvider === "openai_compatible") activeDeepBackendUrl = cleaned;
  } else if (activeLlmProvider === "openai_compatible") {
    activeBackendUrl = cleaned;
  }
}

function localBackendValidationMessage() {
  if (!routeUsesOpenAICompatible()) return "";
  if (currentLocalBackendUrl()) return "";
  return "Local/OpenAI-compatible presets require a backend URL. For LM Studio in Docker, use http://host.docker.internal:1234/v1.";
}

function syncLocalBackendField() {
  const input = $("backend-url");
  const hint = $("backend-url-hint");
  if (!input) return;
  const usesLocal = routeUsesOpenAICompatible();
  input.disabled = !usesLocal;
  input.value = usesLocal ? currentLocalBackendUrl() : "";
  input.placeholder = usesLocal
    ? "http://host.docker.internal:1234/v1"
    : "Not needed for OpenRouter";
  if (hint) {
    if (activeLlmProvider === "hybrid" && usesLocal) {
      hint.textContent = "Hybrid uses this for the local route; the OpenRouter route does not need a backend URL.";
    } else if (activeLlmProvider === "openai_compatible") {
      hint.textContent = "Required for LM Studio/local servers. In Docker, the default is http://host.docker.internal:1234/v1.";
    } else {
      hint.textContent = "OpenRouter and other native cloud providers do not use this field.";
    }
  }
}

function updateLocalBackendWarning() {
  const message = localBackendValidationMessage();
  setInlineWarning("backend-url-warning", message);
  updateVerifyGating();
  return message;
}

function setInlineWarning(id, message) {
  const el = $(id);
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.classList.remove("hidden");
  } else {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function isWeekendDate(value) {
  if (!value) return false;
  const d = new Date(`${value}T12:00:00`);
  return d.getDay() === 0 || d.getDay() === 6;
}

async function validateMarketDate({ silent = false } = {}) {
  const ticker = $("ticker")?.value.trim();
  const date = $("trade-date")?.value;
  const warningId = "market-date-warning";
  if (!ticker || !date) {
    marketDateValidation = null;
    if (!silent) setInlineWarning(warningId, "Enter a ticker and analysis date.");
    return null;
  }
  try {
    const res = await fetchJSON(
      `/api/market-date/validate?ticker=${encodeURIComponent(ticker)}&date=${encodeURIComponent(date)}`
    );
    marketDateValidation = res;
    if (res.latest_valid_date && $("trade-date")) {
      latestMarketDate = res.latest_valid_date;
      $("trade-date").max = res.latest_valid_date;
    }
    if (res.valid) {
      setInlineWarning(warningId, "");
    } else {
      setInlineWarning(
        warningId,
        `${res.message} Pick a valid trading day or confirm the override when starting the run.`
      );
    }
    return res;
  } catch (e) {
    marketDateValidation = null;
    const message = `Could not validate market date: ${e.message}`;
    if (!silent) setInlineWarning(warningId, message);
    return { valid: false, can_override: true, message };
  }
}

function validateBacktestDates() {
  const start = $("firm-backtest-start")?.value;
  const end = $("firm-backtest-end")?.value;
  const max = latestMarketDate || appConfig?.latest_sensible_date || todayISO();
  let message = "";
  if (start && end && start > end) {
    message = "Backtest start date must be on or before end date.";
  } else if (end && end > max) {
    message = `Backtest end date should not be after ${max}.`;
  } else if (isWeekendDate(start) || isWeekendDate(end)) {
    message = "Backtest endpoints fall on a weekend; results may skip to nearby market bars.";
  }
  setInlineWarning("firm-backtest-warning", message);
  return !message;
}

function modelsNoteText(res, cfg) {
  if (res.mode === "hybrid") {
    const quick = res.quick || {};
    const deep = res.deep || {};
    const quickStatus = quick.reachable
      ? `${(quick.models || []).length} local quick model(s)`
      : `quick route unavailable (${quick.hint || quick.error || "check LM Studio"})`;
    const deepStatus = deep.reachable
      ? `${(deep.models || []).length} OpenRouter deep model(s)`
      : `deep route unavailable (${deep.hint || deep.error || "check OpenRouter"})`;
    return `Hybrid health: ${quickStatus}; ${deepStatus}.`;
  }
  const meta = res.models_meta;
  if (meta?.tool_capable_only) {
    const n = meta.included_count ?? (res.models || []).length;
    const ex = meta.excluded_count ?? 0;
    const limits = res.rate_limits?.daily_free_requests
      ? ` Limits: ${res.rate_limits.daily_free_requests}; ${res.rate_limits.requests_per_minute}.`
      : "";
    return (
      `${n} tool-capable free model(s) from OpenRouter` +
      (ex ? ` (${ex} non-tool models excluded)` : "") +
      ". Agent runs require tool-capable models." +
      limits
    );
  }
  if (res.error) {
    return `Model list unavailable (${res.error}); using configured defaults.`;
  }
  return `${(res.models || []).length} model(s) from ${res.backend_url || "backend"}`;
}

function llmQueryParams(cfg, { includeModels = false } = {}) {
  const params = new URLSearchParams();
  const provider = activeLlmProvider || cfg.llm_provider;
  if (provider) params.set("provider", provider);
  if (provider === "hybrid") {
    if (activeQuickProvider || cfg.quick_provider) {
      params.set("quick_provider", activeQuickProvider || cfg.quick_provider);
    }
    if (activeDeepProvider || cfg.deep_provider) {
      params.set("deep_provider", activeDeepProvider || cfg.deep_provider);
    }
    if (activeQuickBackendUrl || cfg.quick_backend_url) {
      params.set("quick_backend_url", activeQuickBackendUrl || cfg.quick_backend_url);
    }
    if (activeDeepBackendUrl || cfg.deep_backend_url) {
      params.set("deep_backend_url", activeDeepBackendUrl || cfg.deep_backend_url);
    }
  } else {
    const url = activeBackendUrl || cfg.backend_url;
    if (provider === "openai_compatible" && url) {
      params.set("backend_url", url);
    }
  }
  if (includeModels) {
    if ($("quick-model")?.value) params.set("quick_model", $("quick-model").value);
    if ($("deep-model")?.value) params.set("deep_model", $("deep-model").value);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

function invalidateVerify(message = "Models changed — verify again before running.") {
  lastVerify = { signature: null, ok: false };
  const statusEl = $("verify-status");
  if (statusEl) {
    statusEl.textContent = message;
    statusEl.className = "help-hint";
  }
  updateVerifyGating();
}

function updateVerifyGating() {
  const backendBlocked = Boolean(localBackendValidationMessage());
  const verified = lastVerify.ok;
  const runBtn = $("run-btn");
  const queueBtn = $("firm-queue-screen-btn");
  if (runBtn && runBtn.textContent !== "Starting\u2026") {
    runBtn.disabled = backendBlocked || !verified;
  }
  if (queueBtn && !queueBtn.textContent.includes("queuing")) {
    queueBtn.disabled = backendBlocked || !verified;
  }
}

async function verifyModels({ silent = false } = {}) {
  const statusEl = $("verify-status");
  const btn = $("verify-models-btn");
  const opts = currentModelRunOptions();
  if (btn) btn.disabled = true;
  if (statusEl && !silent) statusEl.textContent = "Verifying models\u2026";
  lastVerify = { signature: null, ok: false };
  updateVerifyGating();
  try {
    const res = await fetchJSON("/api/llm-verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: opts.provider,
        backend_url: opts.backend_url,
        quick_provider: opts.quick_provider,
        quick_backend_url: opts.quick_backend_url,
        deep_provider: opts.deep_provider,
        deep_backend_url: opts.deep_backend_url,
        deep_model: opts.deep_model,
        quick_model: opts.quick_model,
        model_preset: opts.model_preset,
      }),
    });
    for (const route of res.routes || []) {
      if (route.role === "quick" && route.resolved) {
        selectOption($("quick-model"), route.resolved);
      }
      if (route.role === "deep" && route.resolved) {
        selectOption($("deep-model"), route.resolved);
      }
    }
    lastVerify = { signature: res.route_signature || null, ok: Boolean(res.ok) };
    const notes = (res.notes || []).join(" ");
    if (statusEl) {
      if (res.ok) {
        statusEl.textContent = notes || "Models verified \u2014 tool smoke OK.";
        statusEl.className = "help-hint ok";
      } else {
        statusEl.textContent = notes || "Verify failed.";
        statusEl.className = "help-hint error";
      }
    }
    updateModelToolWarning();
    updateOpenRouterFreeWarning();
    updateQueueModelSummary();
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = e.message;
      statusEl.className = "help-hint error";
    }
  } finally {
    if (btn) btn.disabled = false;
    updateVerifyGating();
  }
  return lastVerify.ok;
}

// ---------- bootstrap ----------

async function init() {
  let cfg = {
    deep_think_llm: "",
    quick_think_llm: "",
    backend_url: null,
    quick_provider: null,
    quick_backend_url: null,
    deep_provider: null,
    deep_backend_url: null,
    max_context_tokens: 8192,
  };
  try {
    cfg = await fetchJSON("/api/config");
    appConfig = cfg;
    modelPresets = cfg.model_presets || [];
    activeLlmProvider = cfg.llm_provider || null;
    activeBackendUrl = cfg.backend_url || null;
    activeQuickProvider = cfg.quick_provider || cfg.llm_provider || null;
    activeDeepProvider = cfg.deep_provider || cfg.llm_provider || null;
    activeQuickBackendUrl = cfg.quick_backend_url || null;
    activeDeepBackendUrl = cfg.deep_backend_url || null;
    $("health-dot")?.classList.add("ok");
    const badge = $("provider-badge");
    if (badge) {
      badge.textContent = cfg.llm_provider === "hybrid"
        ? `hybrid \u00b7 quick ${cfg.quick_provider} \u00b7 deep ${cfg.deep_provider}`
        : `${cfg.llm_provider} \u00b7 ${cfg.deep_think_llm}`;
    }
    latestMarketDate = cfg.latest_sensible_date || cfg.today;
    if ($("trade-date")) {
      $("trade-date").max = latestMarketDate;
      $("trade-date").value = latestMarketDate;
    }
    if ($("debate-rounds")) $("debate-rounds").value = cfg.max_debate_rounds;
    if ($("risk-rounds")) $("risk-rounds").value = cfg.max_risk_rounds;
    if ($("context-window")) $("context-window").value = cfg.max_context_tokens || 8192;

    const checks = $("analyst-checks");
    if (checks) {
      analystLabels = {};
      for (const a of cfg.analysts || []) {
        analystLabels[a.key] = a.label;
        const label = document.createElement("label");
        label.innerHTML =
          `<input type="checkbox" value="${a.key}" checked> ${a.label}`;
        checks.appendChild(label);
      }
      checks.addEventListener("change", () => {
        updateOpenRouterFreeWarning();
        updateQueueModelSummary();
      });
    }
  } catch (e) {
    $("health-dot")?.classList.add("bad");
    const badge = $("provider-badge");
    if (badge) badge.textContent = "backend unreachable";
  }
  await checkLlmHealth(cfg);
  await loadModels(cfg);
  const defaultPreset =
    modelPresets.find(
      (p) =>
        p.deep_think_llm === cfg.deep_think_llm &&
        (!p.llm_provider || p.llm_provider === cfg.llm_provider),
    ) ||
    modelPresets.find((p) => p.llm_provider === cfg.llm_provider && p.id !== "custom") ||
    modelPresets.find((p) => p.id === "fast_local") ||
    modelPresets[0];
  fillPresetSelect(modelPresets, defaultPreset?.id);
  if (defaultPreset) applyModelPreset(defaultPreset, cfg);
  syncLocalBackendField();
  updateLocalBackendWarning();
  updateVerifyGating();
  updateQueueModelSummary();
  initBacktestDates(cfg);
  validateMarketDate({ silent: true });
  refreshHistory();
  setInterval(refreshHistory, 15000);
}

async function checkLlmHealth(cfg) {
  const note = $("models-note");
  const modelsUrl = `/api/llm-health${llmQueryParams(cfg)}`;
  llmHealthDown = false;
  try {
    const res = await fetchJSON(modelsUrl);
    if (!res.reachable) {
      llmHealthDown = true;
      $("health-dot")?.classList.remove("ok");
      $("health-dot")?.classList.add("bad");
      if (note) {
        note.textContent = res.hint || `LLM unreachable: ${res.error}`;
      }
      return;
    }
    if (res.api_key_set === false && note) {
      note.textContent = res.hint || "API key not set in .env — analyses will fail.";
    } else if (!res.models?.length && note) {
      note.textContent = res.hint || res.error || "No chat models loaded on LLM server.";
    }
  } catch (e) {
    if (note) note.textContent = `LLM health check failed: ${e.message}`;
  }
}

function formatRunError(error) {
  if (!error) return "";
  if (error.includes("GraphRecursionError")) {
    return (
      error +
      "\n\nTip: if the current agent is Fundamentals, use Hybrid/local routing " +
      "or uncheck Fundamentals on OpenRouter free. Otherwise start with Market only " +
      "or reduce debate/risk rounds."
    );
  }
  if (
    error.includes("No endpoints found that support tool use") ||
    error.includes("does not support tool/function calling")
  ) {
    return (
      error +
      "\n\nThis model cannot run TradingAgents tool loops. On OpenRouter, use " +
      "meta-llama/llama-3.3-70b-instruct:free or google/gemma-4-26b-a4b-it:free. " +
      "See docs/MODELS.md."
    );
  }
  if (
    error.includes("OpenRouter route") ||
    error.includes("openrouter.ai")
  ) {
    return (
      error +
      "\n\nOpenRouter: check OPENROUTER_API_KEY in .env, Docker network access to openrouter.ai, " +
      "and free-route rate/provider status before retrying."
    );
  }
  if (
    error.includes("Cannot reach the local LLM route") ||
    error.includes("Connection refused")
  ) {
    return (
      error +
      "\n\nLocal LLM: ensure LM Studio is running with a model loaded and reachable from Docker " +
      "(default: host.docker.internal:1234)."
    );
  }
  return error;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatConfidence(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return escapeHTML(value);
  return `${Math.round(n * 100)}%`;
}

function reportTitle(key, report) {
  const tab = REPORT_TABS.find(([k]) => k === key);
  return escapeHTML(report?.agent || tab?.[1] || key);
}

function reportTone(report) {
  const tone = String(report?.stance || report?.rating || "").toLowerCase();
  if (tone.includes("bull") || tone === "buy" || tone === "overweight") return "buy";
  if (tone.includes("bear") || tone === "sell" || tone === "underweight") return "sell";
  if (tone.includes("neutral") || tone === "hold" || tone.includes("mixed")) return "hold";
  return "";
}

function compactList(items, empty = "No extracted items") {
  const values = (items || []).filter(Boolean).slice(0, 3);
  if (!values.length) return `<p class="note">${escapeHTML(empty)}</p>`;
  return `<ul>${values.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>`;
}

function structuredChips(report) {
  const chips = [
    ["Stance", report.stance],
    ["Rating", report.rating],
    ["Confidence", formatConfidence(report.confidence)],
    ["Action", report.recommended_action],
  ].filter(([, value]) => value && value !== "—");
  return chips.map(([label, value]) =>
    `<span class="structured-chip"><b>${escapeHTML(label)}:</b> ${escapeHTML(value)}</span>`
  ).join("");
}

function renderStructuredSummary(run) {
  const panel = $("structured-panel");
  if (!panel) return;
  const structured = run.structured_reports || {};
  const selected = structured[activeTab];
  if (!selected) {
    panel.className = "structured-panel hidden";
    panel.innerHTML = "";
    return;
  }

  const completedCards = Object.entries(structured)
    .filter(([key]) => (run.reports || {})[key])
    .map(([key, report]) => {
      const main = report.stance || report.rating || report.recommended_action || "parsed";
      return (
        `<div class="structured-mini ${reportTone(report)}">` +
        `<b>${reportTitle(key, report)}</b>` +
        `<span>${escapeHTML(main)}</span>` +
        `</div>`
      );
    }).join("");

  const priceRows = [
    selected.price_target !== null && selected.price_target !== undefined
      ? `<span><b>Target:</b> ${escapeHTML(selected.price_target)}</span>` : "",
    selected.stop_loss !== null && selected.stop_loss !== undefined
      ? `<span><b>Stop:</b> ${escapeHTML(selected.stop_loss)}</span>` : "",
    selected.time_horizon ? `<span><b>Horizon:</b> ${escapeHTML(selected.time_horizon)}</span>` : "",
  ].filter(Boolean).join("");

  panel.className = "structured-panel";
  panel.innerHTML =
    `<div class="structured-header">` +
    `<h4>Structured Summary: ${reportTitle(activeTab, selected)}</h4>` +
    `<span class="parse-status">${escapeHTML(selected.parse_status || "parsed")}</span>` +
    `</div>` +
    `<div class="structured-chip-row">${structuredChips(selected)}</div>` +
    (selected.thesis_summary
      ? `<p class="structured-thesis">${escapeHTML(selected.thesis_summary)}</p>`
      : "") +
    (priceRows ? `<div class="structured-levels">${priceRows}</div>` : "") +
    `<div class="structured-columns">` +
    `<div><h5>Evidence</h5>${compactList(selected.evidence || selected.key_points)}</div>` +
    `<div><h5>Risks</h5>${compactList(selected.risks, "No extracted risks")}</div>` +
    `<div><h5>Catalysts</h5>${compactList(selected.catalysts, "No extracted catalysts")}</div>` +
    `</div>` +
    (completedCards
      ? `<h5 class="snapshot-title">Run Snapshot</h5><div class="structured-snapshot">${completedCards}</div>`
      : "");
}

function renderBullBearComparison(run) {
  const panel = $("bull-bear-panel");
  if (!panel) return;
  const structured = run.structured_reports || {};
  const bull = structured.bull_history;
  const bear = structured.bear_history;
  if (!bull || !bear) {
    panel.className = "structured-panel hidden";
    panel.innerHTML = "";
    return;
  }

  const disagreements = [];
  if (bull.stance && bear.stance && bull.stance !== bear.stance) {
    disagreements.push(`Stance differs: bull is ${bull.stance}, bear is ${bear.stance}.`);
  }
  if (bull.rating && bear.rating && bull.rating !== bear.rating) {
    disagreements.push(`Rating differs: bull is ${bull.rating}, bear is ${bear.rating}.`);
  }
  if (bull.evidence?.[0]) disagreements.push(`Bull emphasizes: ${bull.evidence[0]}`);
  if (bear.risks?.[0]) disagreements.push(`Bear emphasizes: ${bear.risks[0]}`);
  else if (bear.evidence?.[0]) disagreements.push(`Bear emphasizes: ${bear.evidence[0]}`);

  const sideCard = (title, report, focusLabel, focusItems) =>
    `<div class="comparison-card ${reportTone(report)}">` +
    `<h5>${escapeHTML(title)}</h5>` +
    `<div class="structured-chip-row">${structuredChips(report)}</div>` +
    `<p>${escapeHTML(report.thesis_summary || "No summary extracted.")}</p>` +
    `<b>${escapeHTML(focusLabel)}</b>` +
    compactList(focusItems) +
    `</div>`;

  panel.className = "structured-panel";
  panel.innerHTML =
    `<div class="structured-header"><h4>Bull vs Bear Comparison</h4></div>` +
    `<div class="comparison-grid">` +
    sideCard("Bull Case", bull, "Top Evidence", bull.evidence || bull.key_points) +
    sideCard("Bear Case", bear, "Top Risks / Evidence", (bear.risks || []).length ? bear.risks : bear.evidence) +
    `</div>` +
    `<div class="disagreements"><h5>Disagreements</h5>${compactList(disagreements, "No extracted disagreements yet")}</div>`;
}

async function loadModels(cfg) {
  const deepSel = $("deep-model");
  const quickSel = $("quick-model");
  const note = $("models-note");
  if (!deepSel || !quickSel) return;

  const modelsUrl = `/api/models${llmQueryParams(cfg, { includeModels: true })}`;
  const deepDefault = activeModelPreset?.deep_think_llm || cfg.deep_think_llm;
  const quickDefault = activeModelPreset?.quick_think_llm || cfg.quick_think_llm;
  let remapped = false;
  try {
    const res = await fetchJSON(modelsUrl);
    if (res.mode === "hybrid") {
      fillModelSelect(deepSel, res.deep?.models || [], deepDefault);
      fillModelSelect(quickSel, res.quick?.models || [], quickDefault);
    } else {
      fillModelSelect(deepSel, res.models || [], deepDefault);
      fillModelSelect(quickSel, res.models || [], quickDefault);
    }
    remapped = Boolean(
      res.resolved?.quick?.remapped || res.resolved?.deep?.remapped
    );
    if (note) {
      note.textContent = modelsNoteText(res, cfg);
    }
    updateModelToolWarning();
  } catch (e) {
    fillModelSelect(deepSel, [], deepDefault);
    fillModelSelect(quickSel, [], quickDefault);
    if (note) note.textContent = `Could not load models: ${e.message}`;
  }
  if (llmHealthDown || remapped) {
    await verifyModels({ silent: true });
  }
}

// Nav tabs first so a later binding error cannot block view switching.
on("nav-research", "click", () => showView("research"));
on("nav-firm", "click", () => showView("firm"));

on("model-preset", "change", () => {
  const id = $("model-preset")?.value;
  const preset = modelPresets.find((p) => p.id === id);
  applyModelPreset(preset, appConfig || {});
  invalidateVerify();
  if (appConfig) {
    checkLlmHealth(appConfig);
    loadModels(appConfig);
  }
  updateQueueModelSummary();
});
on("backend-url", "input", () => {
  setCurrentLocalBackendUrl($("backend-url")?.value || "");
  updateLocalBackendWarning();
  updateQueueModelSummary();
});
on("backend-url", "change", () => {
  setCurrentLocalBackendUrl($("backend-url")?.value || "");
  invalidateVerify();
  if (appConfig) {
    checkLlmHealth(appConfig);
    loadModels(appConfig);
  }
  updateLocalBackendWarning();
  updateQueueModelSummary();
});

on("verify-models-btn", "click", () => verifyModels());

on("deep-model", "change", () => {
  invalidateVerify();
  updateModelToolWarning();
  updateOpenRouterFreeWarning();
  updateQueueModelSummary();
});
on("quick-model", "change", () => {
  invalidateVerify();
  updateModelToolWarning();
  updateOpenRouterFreeWarning();
  updateQueueModelSummary();
});
on("ticker", "change", () => validateMarketDate({ silent: true }));
on("ticker", "blur", () => validateMarketDate({ silent: true }));
on("trade-date", "change", () => validateMarketDate());

// ---------- run lifecycle ----------

on("analyze-form", "submit", async (ev) => {
  ev.preventDefault();
  $("form-error").textContent = "";
  const analysts = selectedAnalysts();
  const backendMessage = updateLocalBackendWarning();
  if (backendMessage) {
    $("form-error").textContent = backendMessage;
    return;
  }
  let marketDateOverride = false;
  const dateCheck = await validateMarketDate();
  if (!dateCheck?.valid) {
    if (!dateCheck?.can_override) {
      $("form-error").textContent = dateCheck?.message || "Select a valid market date.";
      return;
    }
    marketDateOverride = confirm(
      `${dateCheck.message}\n\nRun anyway? Results may be stale, empty, or incomplete.`
    );
    if (!marketDateOverride) return;
  }
  let openrouterFreeOverride = false;
  const openrouterWarning = openRouterFreeGuardrailMessage();
  if (requiresOpenRouterFreeOverride()) {
    openrouterFreeOverride = confirm(
      `${openrouterWarning}\n\nRun anyway and risk a 429/quota failure?`
    );
    if (!openrouterFreeOverride) return;
  }
  if (!lastVerify.ok) {
    const ok = await verifyModels();
    if (!ok) {
      $("form-error").textContent = $("verify-status")?.textContent || "Verify models before running.";
      return;
    }
  }
  const body = {
    ticker: $("ticker").value.trim(),
    trade_date: $("trade-date").value,
    analysts,
    ...currentModelRunOptions(),
    openrouter_free_override: openrouterFreeOverride,
    market_date_override: marketDateOverride,
  };
  const btn = $("run-btn");
  btn.disabled = true;
  btn.textContent = "Starting\u2026";
  try {
    const res = await fetchJSON("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    watchRun(res.run_id);
    refreshHistory();
  } catch (e) {
    $("form-error").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Analysis";
    updateLocalBackendWarning();
  }
});

function clearSelectedRunFusion(message = "Fusion available when run completes.") {
  const firmPanel = $("firm-fusion-panel");
  const runBanner = $("run-fusion-banner");
  const firmBtn = $("firm-execute-btn");
  const executeBtn = $("execute-btn");
  const executeHint = $("execute-hint");
  const fusedSignal = $("fused-signal");
  if (firmPanel) {
    firmPanel.className = "firm-fusion note";
    firmPanel.textContent = message;
  }
  if (runBanner) {
    runBanner.className = "firm-fusion hidden";
    runBanner.innerHTML = "";
  }
  if (firmBtn) {
    firmBtn.classList.add("hidden");
    firmBtn.disabled = true;
  }
  if (executeBtn) {
    executeBtn.classList.add("hidden");
    executeBtn.disabled = false;
    executeBtn.textContent = "Paper Execute";
    executeBtn.onclick = null;
  }
  if (executeHint) executeHint.classList.add("hidden");
  if (fusedSignal) {
    fusedSignal.className = "hidden";
    fusedSignal.innerHTML = "";
  }
}

function clearResumeControls() {
  const panel = $("resume-panel");
  const hint = $("resume-hint");
  const currentBtn = $("resume-current-btn");
  const localBtn = $("resume-local-btn");
  if (panel) panel.classList.add("hidden");
  if (hint) hint.textContent = "";
  if (currentBtn) currentBtn.onclick = null;
  if (localBtn) localBtn.onclick = null;
}

function watchRun(runId) {
  const changed = runId !== currentRunId;
  currentRunId = runId;
  activeTab = null;
  if (pollTimer) clearInterval(pollTimer);
  if (changed) {
    clearSelectedRunFusion("Loading selected run fusion...");
    clearResumeControls();
  }
  pollRun();
  pollTimer = setInterval(pollRun, 3000);
}

async function pollRun() {
  if (!currentRunId) return;
  try {
    const run = await fetchJSON(`/api/runs/${currentRunId}`);
    renderRun(run);
    if (["completed", "failed", "paused", "failed_resumable"].includes(run.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      refreshHistory();
    }
  } catch (e) { /* transient poll errors are fine */ }
}

// ---------- rendering ----------

function renderRun(run) {
  $("empty-state").classList.add("hidden");
  $("run-view").classList.remove("hidden");
  renderFusionForRun(run);
  renderResearchFusion(run);

  $("run-title").textContent = `${run.ticker} \u2014 ${run.trade_date}`;
  $("run-meta").textContent =
    `started ${run.created_at}` +
    (run.finished_at ? ` \u00b7 finished ${run.finished_at}` : "") +
    (run.deep_model ? ` \u00b7 ${run.deep_model}` : "");

  const badge = $("run-status");
  badge.textContent = run.status;
  badge.className = `badge ${run.status}`;

  $("run-error").textContent = formatRunError(run.error || "");
  renderResumeControls(run);

  // decision banner
  const banner = $("decision-banner");
  if (run.decision) {
    const d = String(run.decision).toUpperCase();
    banner.textContent = `Final decision: ${d}`;
    banner.className = "";
    if (d.includes("BUY")) banner.classList.add("buy");
    else if (d.includes("SELL")) banner.classList.add("sell");
    else banner.classList.add("hold");
  } else {
    banner.className = "hidden";
  }

  // agents
  const list = $("agent-list");
  list.innerHTML = "";
  const agentMetrics = run.run_metrics?.agents || {};
  for (const a of run.agent_status || []) {
    const li = document.createElement("li");
    const m = agentMetrics[a.agent] || {};
    const metrics = [];
    if (m.tool_calls) metrics.push(`${m.tool_calls} tools`);
    if (m.duplicate_tool_calls) metrics.push(`${m.duplicate_tool_calls} repeat`);
    if (m.truncated_tool_results) metrics.push(`${m.truncated_tool_results} trunc`);
    if (m.cached_tool_results) metrics.push(`${m.cached_tool_results} cached`);
    if (m.report_chars) metrics.push(`${m.report_chars.toLocaleString()} chars`);
    const metricText = metrics.length
      ? `<span class="agent-metrics">${escapeHTML(metrics.join(" · "))}</span>`
      : "";
    li.innerHTML = `<span class="agent-main"><span>${escapeHTML(a.agent)}</span>${metricText}</span>` +
      `<span class="status-chip ${a.status}">${a.status.replace("_", " ")}</span>`;
    list.appendChild(li);
  }

  // report tabs
  const reports = run.reports || {};
  const tabs = $("report-tabs");
  tabs.innerHTML = "";
  const available = REPORT_TABS.filter(([k]) => reports[k]);
  if (!available.length) {
    $("report-body").innerHTML =
      '<p class="note">Reports appear as each agent completes.</p>';
    $("structured-panel")?.classList.add("hidden");
    $("bull-bear-panel")?.classList.add("hidden");
    return;
  }
  if (!activeTab || !reports[activeTab]) activeTab = available[0][0];
  for (const [key, label] of available) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (key === activeTab) b.classList.add("active");
    b.onclick = () => { activeTab = key; renderRun(run); };
    tabs.appendChild(b);
  }
  renderStructuredSummary(run);
  renderBullBearComparison(run);
  $("report-body").innerHTML = marked.parse(reports[activeTab] || "");
}

function currentResumeOverrides(localOnly = false) {
  const body = {
    provider: activeLlmProvider || null,
    backend_url: activeLlmProvider === "hybrid" ? null : (activeBackendUrl || null),
    quick_provider: activeLlmProvider === "hybrid" ? (activeQuickProvider || null) : null,
    quick_backend_url: activeLlmProvider === "hybrid" ? (activeQuickBackendUrl || null) : null,
    deep_provider: activeLlmProvider === "hybrid" ? (activeDeepProvider || null) : null,
    deep_backend_url: activeLlmProvider === "hybrid" ? (activeDeepBackendUrl || null) : null,
    deep_model: $("deep-model")?.value || null,
    quick_model: $("quick-model")?.value || null,
    max_context_tokens:
      parseInt($("context-window")?.value || activeModelPreset?.max_context_tokens || 8192) || 8192,
  };
  if (localOnly) {
    const backend =
      activeQuickBackendUrl ||
      appConfig?.quick_backend_url ||
      appConfig?.backend_url ||
      "http://host.docker.internal:1234/v1";
    body.local_only = true;
    body.provider = "hybrid";
    body.quick_provider = "openai_compatible";
    body.deep_provider = "openai_compatible";
    body.quick_backend_url = backend;
    body.deep_backend_url = backend;
  }
  return body;
}

function resumeCountdownText(run) {
  const retryAfter = Number(run.retry_after_seconds);
  if (!retryAfter) return "";
  const pausedAt = Date.parse(run.paused_at || run.finished_at || run.created_at || "");
  if (!Number.isFinite(pausedAt)) {
    return ` Provider suggested retry after about ${retryAfter} seconds.`;
  }
  const elapsed = Math.max(0, Math.floor((Date.now() - pausedAt) / 1000));
  const remaining = Math.max(0, retryAfter - elapsed);
  return remaining
    ? ` Provider suggested waiting about ${remaining} more second(s).`
    : " Provider retry window may be open now.";
}

function renderResumeControls(run) {
  const panel = $("resume-panel");
  const hint = $("resume-hint");
  const currentBtn = $("resume-current-btn");
  const localBtn = $("resume-local-btn");
  if (!panel || !hint || !currentBtn || !localBtn) return;
  if (!run.resume_available) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  hint.textContent =
    (run.resume_reason || "This run can resume from completed report sections.") +
    resumeCountdownText(run);
  currentBtn.onclick = () => resumeRun(run.id, false);
  localBtn.onclick = () => resumeRun(run.id, true);
}

async function resumeRun(runId, localOnly) {
  const currentBtn = $("resume-current-btn");
  const localBtn = $("resume-local-btn");
  if (currentBtn) currentBtn.disabled = true;
  if (localBtn) localBtn.disabled = true;
  try {
    const res = await fetchJSON(`/api/runs/${runId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentResumeOverrides(localOnly)),
    });
    watchRun(res.run_id);
    refreshHistory();
  } catch (e) {
    $("run-error").textContent = e.message;
  } finally {
    if (currentBtn) currentBtn.disabled = false;
    if (localBtn) localBtn.disabled = false;
  }
}

function renderResearchFusion(run) {
  const el = $("fused-signal");
  const btn = $("execute-btn");
  const hint = $("execute-hint");
  if (el) el.className = "hidden";
  const f = run.fused_signal;
  if (!f || run.status !== "completed") {
    if (el) el.innerHTML = "";
    if (btn) btn.classList.add("hidden");
    if (hint) hint.classList.add("hidden");
    return;
  }
  if (btn) {
    if (f.fused_pass) {
      btn.classList.remove("hidden");
      if (hint) hint.classList.remove("hidden");
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await fetchJSON(`/api/firm/execute/${run.id}`, { method: "POST" });
          btn.textContent = "Order submitted";
        } catch (e) {
          alert(e.message);
        } finally {
          btn.disabled = false;
        }
      };
    } else {
      btn.classList.add("hidden");
      if (hint) hint.classList.add("hidden");
    }
  }
}

function renderFusionForRun(run) {
  const panel = $("firm-fusion-panel");
  const runBanner = $("run-fusion-banner");
  const btn = $("firm-execute-btn");
  const f = run.fused_signal;
  if (!f) {
    if (panel) {
      panel.className = "firm-fusion note";
      panel.textContent = run.status === "completed"
        ? "Fusion pending or unavailable."
        : "Fusion available when run completes.";
    }
    if (runBanner) {
      runBanner.className = "firm-fusion hidden";
      runBanner.innerHTML = "";
    }
    if (btn) btn.classList.add("hidden");
    return;
  }
  const pass = f.fused_pass;
  const html =
    `<b>${f.ticker}</b> fused ${pass ? "PASS" : "FAIL"} ` +
    `(score ${f.fused_score ?? "—"})<br>` +
    `Quant: ${f.quant_pass ? "pass" : "fail"} (${f.quant_score}) · ` +
    `LLM: ${f.llm_rating} (${f.llm_pass ? "pass" : "fail"}) · ` +
    `Regime: ${f.regime} (×${f.regime_multiplier})` +
  ((f.blockers || []).length
    ? `<ul>${(f.blockers || []).map((b) => `<li class="blocker">${b}</li>`).join("")}</ul>`
    : "");
  if (panel) {
    panel.className = `firm-fusion ${pass ? "pass" : "fail"}`;
    panel.innerHTML = html;
  }
  if (runBanner) {
    runBanner.className = `firm-fusion ${pass ? "pass" : "fail"}`;
    runBanner.innerHTML = html;
  }
  if (btn) {
    if (pass && run.status === "completed") {
      btn.classList.remove("hidden");
      btn.disabled = false;
    } else {
      btn.classList.add("hidden");
      btn.disabled = true;
    }
  }
}

async function refreshHistory() {
  try {
    const res = await fetchJSON("/api/runs");
    const list = $("history-list");
    list.innerHTML = "";
    if (!res.runs.length) {
      list.innerHTML = '<span class="note">No runs yet.</span>';
      return;
    }
    for (const r of res.runs) {
      const item = document.createElement("div");
      item.className = "history-item" + (r.id === currentRunId ? " active" : "");
      item.innerHTML =
        `<span><b>${r.ticker}</b> ${r.trade_date}` +
        `<br><span class="when">${r.created_at}</span></span>` +
        `<span class="badge ${r.status}">${r.decision || r.status}</span>`;
      item.onclick = () => watchRun(r.id);
      list.appendChild(item);
    }
  } catch (e) { /* ignore */ }
}

// ---------- firm dashboard ----------

function showView(view) {
  const research = $("research-view");
  const firm = $("firm-view");
  if (!research || !firm) {
    console.warn("View containers missing; cannot switch tabs");
    return;
  }
  if (view === "firm") {
    research.classList.add("hidden");
    firm.classList.remove("hidden");
    $("nav-research")?.classList.remove("active");
    $("nav-firm")?.classList.add("active");
    updateQueueModelSummary();
    refreshFirmDashboard();
  } else {
    firm.classList.add("hidden");
    research.classList.remove("hidden");
    $("nav-firm")?.classList.remove("active");
    $("nav-research")?.classList.add("active");
  }
}

async function refreshWatchlistSummary() {
  const el = $("firm-watchlist-summary");
  if (!el) return;
  try {
    const wl = await fetchJSON("/api/firm/watchlist");
    const src = wl.sources || {};
    const gemini = src.gemini_core?.count ?? 30;
    const curated = src.curated_extra?.count ?? 20;
    const user = src.user_extra?.count ?? 0;
    el.innerHTML =
      `<p class="note">Universe: <b>${wl.count}</b> tickers ` +
      `(${gemini} Gemini core + ${curated} curated` +
      `${user ? ` + ${user} user` : ""}). ` +
      `${wl.expansion || ""}</p>`;
  } catch {
    el.textContent = "";
  }
}

async function refreshFirmDashboard() {
  try {
    const [cfg, regime, signals, runs] = await Promise.all([
      fetchJSON("/api/firm/config"),
      fetchJSON("/api/firm/regime"),
      fetchJSON("/api/firm/signals?limit=20"),
      fetchJSON("/api/runs"),
    ]);
    $("firm-mode-badge").textContent = cfg.trading_mode;
    const rb = $("regime-banner");
    rb.textContent = `Regime: ${regime.label} (×${regime.multiplier}) — ${regime.detail}`;
    rb.className = `regime-banner ${regime.label}`;
    renderSignalsTable(signals.signals || []);
    renderAutoRuns(runs.runs || [], cfg.premarket_screen_top_n || 5);
    await loadFirmSettings();
    await refreshWatchlistSummary();
    if (currentRunId) {
      const run = await fetchJSON(`/api/runs/${currentRunId}`);
      renderFusionForRun(run);
    }
  } catch (e) { /* ignore */ }
}

const SETTINGS_GROUP_LABELS = {
  screener: "Screener thresholds",
  universe: "Universe & market scan",
  fusion: "Fusion",
  scheduler: "Scheduler",
  risk: "Risk limits",
};

const SETTINGS_GROUP_HINTS = {
  screener: "Minimum scores and filters tickers must pass before queueing.",
  universe: "Which tickers to scan: watchlist, market movers, or hybrid.",
  fusion: "How quant scores, LLM ratings, and regime combine into a signal.",
  scheduler: "When auto-screen and auto-queue runs fire each day.",
  risk: "Position caps and gates applied before paper execution.",
};

const RISK_SCALING_EQUITY = 1000;

function renderRiskScalingCallout(settings) {
  const maxPos = settings.max_position_pct?.value ?? 0.05;
  const risk = settings.risk_per_trade?.value ?? 0.012;
  const daily = settings.daily_loss_limit_pct?.value ?? 0.03;
  const eq = RISK_SCALING_EQUITY;
  const capDollars = eq * maxPos;
  return (
    `<p class="note settings-scaling-callout">` +
    `<b>At $${eq.toLocaleString()} equity</b> (from settings above): ` +
    `max position <b>$${capDollars.toFixed(0)}</b>, ` +
    `risk/trade <b>$${(eq * risk).toFixed(2)}</b>, ` +
    `daily halt <b>$${(eq * daily).toFixed(0)}</b>. ` +
    `Whole shares only — tickers above <b>$${capDollars.toFixed(0)}/share</b> cannot be opened.` +
    `</p>`
  );
}

let firmSettingsCache = null;

async function loadFirmSettings() {
  try {
    const data = await fetchJSON("/api/firm/settings");
    firmSettingsCache = data;
    renderFirmSettings(data);
  } catch (e) {
    const err = $("firm-settings-error");
    if (err) err.textContent = e.message;
  }
}

function renderFirmSettings(data) {
  const readonlyEl = $("firm-settings-readonly");
  const formEl = $("firm-settings-form");
  const statusEl = $("firm-settings-status");
  const errEl = $("firm-settings-error");
  if (!readonlyEl || !formEl) return;
  if (errEl) errEl.textContent = "";

  const ro = data.read_only || {};
  readonlyEl.innerHTML = Object.values(ro).map((item) => {
    if (item.type === "bool") {
      const cls = item.value ? "on" : "off";
      return `<span class="settings-badge ${cls}">${item.label}: ${item.value ? "on" : "off"}</span>`;
    }
    return `<span class="settings-badge">${item.label}: ${item.value}</span>`;
  }).join("");

  const groups = data.groups || {};
  const settings = data.settings || {};
  let html = "";
  for (const [groupKey, keys] of Object.entries(groups)) {
    if (groupKey === "system") continue;
    const tunableKeys = (keys || []).filter((k) => settings[k]);
    if (!tunableKeys.length) continue;
    html += `<div class="settings-group">${SETTINGS_GROUP_LABELS[groupKey] || groupKey}</div>`;
    const groupHint = SETTINGS_GROUP_HINTS[groupKey];
    if (groupHint) {
      html += `<p class="help-hint settings-group-hint">${groupHint}</p>`;
    }
    if (groupKey === "risk") {
      html += renderRiskScalingCallout(settings);
    }
    for (const key of tunableKeys) {
      const s = settings[key];
      const step = s.type === "int" ? "1" : "0.01";
      const disabled = !s.editable ? "disabled" : "";
      const sourceNote = s.source === "env"
        ? `<span class="field-meta env-locked">Locked by ${s.env_key}</span>`
        : `<span class="field-meta">source: ${s.source}</span>`;
      const warning = s.warning
        ? `<div class="field-warning">${s.warning}</div>`
        : "";
      let inputHtml = "";
      if (s.type === "select") {
        const opts = (s.options || []).map((opt) => {
          const sel = String(s.value) === String(opt) ? "selected" : "";
          return `<option value="${opt}" ${sel}>${opt}</option>`;
        }).join("");
        inputHtml =
          `<select id="setting-${key}" data-key="${key}" data-type="select" ${disabled}>` +
          `${opts}</select>`;
      } else if (s.type === "bool") {
        const checked = s.value ? "checked" : "";
        inputHtml =
          `<input type="checkbox" id="setting-${key}" data-key="${key}" ` +
          `data-type="bool" ${checked} ${disabled}>`;
      } else {
        inputHtml =
          `<input type="number" id="setting-${key}" data-key="${key}" ` +
          `min="${s.min}" max="${s.max}" step="${step}" value="${s.value}" ${disabled}>`;
      }
      html +=
        `<div class="settings-field">` +
        `<label for="setting-${key}">${s.label}` +
        `${inputHtml}` +
        `</label>${sourceNote}${warning}</div>`;
    }
  }
  formEl.innerHTML = html;

  if (statusEl) {
    statusEl.textContent = data.user_settings_path
      ? `Saved to ${data.user_settings_path}`
      : "";
  }

  const wlEl = $("firm-watchlist-editor");
  if (wlEl) {
    const wl = data.watchlist || {};
    const uni = data.universe || {};
    const userExtra = (wl.user_extra || []).join(", ");
    const modeNote = uni.mode === "market"
      ? `<p class="note"><b>Market mode</b> — stage 1 unions enabled scans ` +
        `(actives, movers, watchlist, optional SPY; max ${uni.market_screener_max ?? 200}), ` +
        `then stage-2 ${uni.screener_mode ?? "scoring"} quant filters.</p>`
      : `<p class="note"><b>Watchlist mode</b> — static curated universe (~${wl.count ?? 50} tickers).</p>`;
    wlEl.innerHTML =
      `<div class="settings-group">Screening universe</div>` +
      modeNote +
      `<p class="note">${wl.note || ""}</p>` +
      `<p class="note">` +
      `<b>${wl.count ?? 50}</b> tickers in watchlist fallback ` +
      `(${wl.static_seed_count ?? 50} static seed` +
      `${(wl.user_extra || []).length ? ` + ${wl.user_extra.length} user` : ""}). ` +
      `${wl.expansion || ""}` +
      `</p>` +
      `<div class="settings-field">` +
      `<label for="setting-watchlist-extra">Extra tickers (comma-separated)` +
      `<input type="text" id="setting-watchlist-extra" ` +
      `placeholder="e.g. PLTR, SOFI, COIN" value="${userExtra}">` +
      `</label>` +
      `<span class="field-meta">Used in watchlist mode or as fallback if market scan fails</span>` +
      `</div>`;
  }
}

function collectSettingsPatch() {
  const patch = {};
  for (const input of document.querySelectorAll("#firm-settings-form [data-key]")) {
    if (input.disabled) continue;
    const key = input.dataset.key;
    const inputType = input.dataset.type || input.type;
    if (inputType === "bool" || input.type === "checkbox") {
      patch[key] = input.checked;
      continue;
    }
    if (inputType === "select" || input.tagName === "SELECT") {
      patch[key] = input.value;
      continue;
    }
    const val = input.value.trim();
    if (val === "") continue;
    patch[key] = input.step === "1" ? parseInt(val, 10) : parseFloat(val);
  }
  return patch;
}

on("firm-settings-save", "click", async () => {
  const errEl = $("firm-settings-error");
  const btn = $("firm-settings-save");
  if (!btn) return;
  btn.disabled = true;
  if (errEl) errEl.textContent = "";
  try {
    const patch = collectSettingsPatch();
    const extraInput = $("setting-watchlist-extra");
    const body = { settings: patch };
    if (extraInput) {
      body.watchlist_extra = extraInput.value.trim();
    }
    firmSettingsCache = await fetchJSON("/api/firm/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderFirmSettings(firmSettingsCache);
    refreshWatchlistSummary();
  } catch (e) {
    if (errEl) errEl.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

on("firm-settings-reset", "click", async () => {
  const errEl = $("firm-settings-error");
  const btn = $("firm-settings-reset");
  if (!btn) return;
  if (!confirm("Reset all UI-tuned settings to defaults?")) return;
  btn.disabled = true;
  if (errEl) errEl.textContent = "";
  try {
    firmSettingsCache = await fetchJSON("/api/firm/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reset: true }),
    });
    renderFirmSettings(firmSettingsCache);
  } catch (e) {
    if (errEl) errEl.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

function renderAutoRuns(runs, topN) {
  const el = $("firm-auto-runs");
  const today = new Date().toISOString().slice(0, 10);
  const autoSources = new Set(["premarket_screen", "manual_screen"]);
  const autoRuns = runs.filter(
    (r) => autoSources.has(r.source) && r.trade_date === today
  );
  if (!autoRuns.length) {
    el.innerHTML =
      `<span class="note">No auto-queued runs today (top ${topN} from screener).</span>`;
    return;
  }
  const rows = autoRuns.map(
    (r) => {
      const label = r.resume_available ? "Resume/View" : "View";
      return `<tr><td>${escapeHTML(r.ticker)}</td><td>${escapeHTML(r.trade_date)}</td>` +
        `<td><span class="badge ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></td>` +
        `<td>${escapeHTML(r.source || "auto")}</td>` +
        `<td><button type="button" class="inline-btn" data-run-id="${escapeHTML(r.id)}">` +
        `${label}</button></td></tr>`;
    }
  ).join("");
  el.innerHTML =
    `<table class="firm-table"><thead><tr>` +
    `<th>Ticker</th><th>Date</th><th>Status</th><th>Source</th><th>Action</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}

on("firm-auto-runs", "click", (ev) => {
  const btn = ev.target.closest("button[data-run-id]");
  if (!btn) return;
  showView("research");
  watchRun(btn.dataset.runId);
});

function renderSignalsTable(signals) {
  const el = $("firm-signals");
  if (!signals.length) {
    el.innerHTML = '<span class="note">No fused signals yet.</span>';
    return;
  }
  const rows = signals.map((s) =>
    `<tr><td>${s.ticker}</td><td>${s.fused_pass ? "PASS" : "fail"}</td>` +
    `<td>${s.quant_score}</td><td>${s.llm_rating}</td><td>${s.fused_score}</td></tr>`
  ).join("");
  el.innerHTML =
    `<table class="firm-table"><thead><tr>` +
    `<th>Ticker</th><th>Verdict</th><th>Quant</th><th>LLM</th><th>Fused</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}

on("firm-sync-btn", "click", async () => {
  const el = $("firm-positions");
  if (!el) return;
  el.textContent = "Syncing…";
  try {
    const res = await fetchJSON("/api/firm/sync", { method: "POST" });
    renderPositionsTable(res.positions || []);
  } catch (e) {
    el.textContent = e.message;
  }
});

function renderPositionsTable(positions) {
  const el = $("firm-positions");
  if (!positions.length) {
    el.innerHTML = '<span class="note">No open positions.</span>';
    return;
  }
  const rows = positions.map((p) =>
    `<tr><td>${p.ticker}</td><td>${p.qty}</td><td>${p.avg_entry}</td>` +
    `<td>${p.market_value}</td><td>${p.unrealized_pl}</td></tr>`
  ).join("");
  el.innerHTML =
    `<table class="firm-table"><thead><tr>` +
    `<th>Symbol</th><th>Qty</th><th>Entry</th><th>Value</th><th>P/L</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}

on("firm-screen-btn", "click", async () => {
  const el = $("firm-screen-results");
  if (!el) return;
  el.textContent = "Screening…";
  try {
    const [res, cfg] = await Promise.all([
      fetchJSON("/api/firm/screen?top_n=50&pass_only=false"),
      fetchJSON("/api/firm/config"),
    ]);
    renderScreenResults(el, res, cfg);
  } catch (e) {
    el.textContent = e.message;
  }
});

on("firm-queue-screen-btn", "click", async () => {
  const el = $("firm-screen-results");
  const btn = $("firm-queue-screen-btn");
  if (!el || !btn) return;
  const backendMessage = updateLocalBackendWarning();
  if (backendMessage) {
    el.textContent = backendMessage;
    return;
  }
  let openrouterFreeOverride = false;
  const openrouterWarning = openRouterFreeGuardrailMessage();
  if (requiresOpenRouterFreeOverride()) {
    openrouterFreeOverride = confirm(
      `${openrouterWarning}\n\nScreen & Queue anyway and risk 429/quota pauses?`
    );
    if (!openrouterFreeOverride) return;
  }
  if (!lastVerify.ok) {
    const ok = await verifyModels();
    if (!ok) {
      el.textContent = $("verify-status")?.textContent || "Verify models before queuing.";
      return;
    }
  }
  const body = {
    top_n: null,
    analysts: selectedAnalysts(),
    ...currentModelRunOptions(),
    openrouter_free_override: openrouterFreeOverride,
  };
  el.textContent = "Screening and queuing…";
  btn.disabled = true;
  try {
    const res = await fetchJSON("/api/firm/queue-screen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const cands = res.candidates || [];
    const queued = res.queued || [];
    const skipped = res.skipped || [];
    let summary = "";
    if (res.route_summary) {
      summary += `<p class="note">Queued runs used: ${escapeHTML(res.route_summary)}</p>`;
    }
    if (res.warning) {
      summary += `<p class="warning">${escapeHTML(res.warning)}</p>`;
    }
    if (queued.length) {
      summary += `<p class="note">Queued: <b>${queued.map(escapeHTML).join(", ")}</b></p>`;
    }
    if (skipped.length) {
      summary += `<p class="note">Skipped (already active): ${skipped.map(escapeHTML).join(", ")}</p>`;
    }
    if (!queued.length && !skipped.length && !cands.length) {
      el.innerHTML = '<span class="note">No passing candidates to queue.</span>';
    } else {
      el.innerHTML = summary + renderScreenTable(
        cands,
        res.screener_mode ?? "scoring"
      );
    }
    refreshFirmDashboard();
    refreshHistory();
  } catch (e) {
    el.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

function renderScreenerThresholds(cfg) {
  const s = cfg?.screener || {};
  const minScore = s.quant_min_score ?? cfg?.quant_min_score ?? 60;
  return (
    `<p class="screener-thresholds note">` +
    `Active filters: score ≥ ${minScore}, ADX ≥ ${s.adx_min ?? 20}, ` +
    `RSI ${s.rsi_low ?? 30}–${s.rsi_high ?? 70}, volume ratio ≥ ${s.volume_ratio_min ?? 1.2}` +
    `</p>`
  );
}

function renderScreenTable(cands, screenerMode = "scoring") {
  if (!cands.length) return "";
  const isScoring = screenerMode === "scoring";
  const colLabel = isScoring ? "Failed filters" : "Blockers";
  const rows = cands.map((c) => {
    const issuesCell = formatScreenIssues(c, screenerMode);
    const rowClass = c.passed ? "screen-pass" : "screen-fail";
    return (
      `<tr class="${rowClass}">` +
      `<td>${c.ticker}</td><td>${c.score}</td>` +
      `<td>${c.passed ? "yes" : "no"}</td><td>${issuesCell}</td></tr>`
    );
  }).join("");
  return (
    `<table class="firm-table"><thead><tr>` +
    `<th>Ticker</th><th>Score</th><th>Pass</th><th>${colLabel}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`
  );
}

function formatScreenIssues(c, screenerMode = "scoring") {
  const isScoring = screenerMode === "scoring";
  if (c.passed && isScoring) {
    const items = (c.advisory || []).length
      ? c.advisory
      : (c.blockers || []);
    if (!items.length) return "—";
    return `<span class="advisory">${items.join(", ")}</span>`;
  }
  if (!c.passed && (c.blockers || []).length) {
    return `<span class="blocker-text">${c.blockers.join(", ")}</span>`;
  }
  return "—";
}

function renderScreenResults(el, res, cfg) {
  const cands = res.candidates || [];
  const total = res.total_screened ?? cands.length;
  const passing = res.passing_count ?? cands.filter((c) => c.passed).length;
  const uni = res.universe || {};
  const wl = res.watchlist || {};
  let universeNote = "";
  if (uni.mode === "market") {
    const fb = uni.fallback ? " (fell back to watchlist)" : "";
    universeNote =
      `<p class="note">Stage 1: <b>market scan</b>${fb} — ` +
      `<b>${total}</b> symbols from Alpaca screener ` +
      `(${uni.source || "alpaca_screener"}). Stage 2: firm quant filters.</p>`;
  } else if (wl.count) {
    universeNote =
      `<p class="note">Screened <b>${total}</b> tickers from curated watchlist ` +
      `(${wl.static_seed_count ?? 50} seed` +
      `${wl.user_extra_count ? ` + ${wl.user_extra_count} user` : ""}).</p>`;
  }
  const summary =
    universeNote +
    `<p class="note"><b>${passing}</b> passing` +
    (passing === 0 ? " — top scores shown below." : ".") +
    `</p>`;
  if (!cands.length) {
    el.innerHTML = summary + '<span class="note">No screener data returned.</span>';
    return;
  }
  el.innerHTML = summary + renderScreenerThresholds(cfg) +
    renderScreenTable(cands, res.screener_mode ?? cfg?.screener?.mode ?? "scoring");
}

on("firm-execute-btn", "click", async () => {
  if (!currentRunId) return;
  const btn = $("firm-execute-btn");
  const out = $("firm-execute-result");
  if (!btn || !out) return;
  btn.disabled = true;
  out.textContent = "Submitting paper order…";
  try {
    const res = await fetchJSON(`/api/firm/execute/${currentRunId}`, { method: "POST" });
    out.textContent = JSON.stringify(res, null, 2);
    refreshFirmDashboard();
  } catch (e) {
    out.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

function initBacktestDates(cfg = appConfig) {
  const startEl = $("firm-backtest-start");
  const endEl = $("firm-backtest-end");
  if (!startEl || !endEl) return;
  const max = latestMarketDate || cfg?.latest_sensible_date || todayISO();
  const end = new Date(`${max}T12:00:00`);
  const start = new Date(end);
  start.setFullYear(start.getFullYear() - 1);
  startEl.max = max;
  endEl.max = max;
  endEl.value = max;
  startEl.value = start.toISOString().slice(0, 10);
  validateBacktestDates();
}

function renderBacktestResults(data) {
  const el = $("firm-backtest-results");
  if (!el) return;
  const rows = [];
  for (const [sm, block] of Object.entries(data.screener_comparison || {})) {
    rows.push(`<h4>Screener: ${sm}</h4>`);
    rows.push(
      `<table class="firm-table"><thead><tr>` +
        `<th>Strategy</th><th>Signals</th><th>Pass rate</th>` +
        `<th>Hit 5d</th><th>Avg 5d</th><th>Hit 20d</th><th>Avg 20d</th><th>Max DD</th>` +
        `</tr></thead><tbody>`
    );
    for (const [name, m] of Object.entries(block.metrics || {})) {
      const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
      const num = (v) => (v == null ? "—" : (v * 100).toFixed(2) + "%");
      rows.push(
        `<tr><td>${name}</td><td>${m.signals}/${m.evaluations}</td>` +
          `<td>${pct(m.pass_rate)}</td><td>${pct(m.hit_rate_5d)}</td><td>${num(m.avg_forward_return_5d)}</td>` +
          `<td>${pct(m.hit_rate_20d)}</td><td>${num(m.avg_forward_return_20d)}</td>` +
          `<td>${pct(m.max_drawdown)}</td></tr>`
      );
    }
    rows.push("</tbody></table>");
  }
  el.innerHTML = rows.join("");
}

on("firm-backtest-run", "click", async () => {
  const status = $("firm-backtest-status");
  const btn = $("firm-backtest-run");
  const results = $("firm-backtest-results");
  const start = $("firm-backtest-start")?.value;
  const end = $("firm-backtest-end")?.value;
  if (!start || !end) {
    if (status) status.textContent = "Set start and end dates.";
    return;
  }
  if (!validateBacktestDates() && !confirm("Backtest date range has a warning. Run anyway?")) {
    return;
  }
  btn.disabled = true;
  if (status) status.textContent = "Running backtest (yfinance)…";
  if (results) results.textContent = "";
  try {
    const data = await fetchJSON("/api/firm/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_date: start,
        end_date: end,
        mode: $("firm-backtest-mode")?.value || "compare",
        llm_proxy: $("firm-backtest-llm-proxy")?.value || "momentum",
      }),
    });
    if (status) {
      status.textContent =
        `${data.llm_proxy_disclaimer} ` +
        `(${data.tickers_loaded} tickers, ${data.evaluation_dates} dates)`;
    }
    renderBacktestResults(data);
  } catch (e) {
    if (status) status.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

on("firm-backtest-start", "change", validateBacktestDates);
on("firm-backtest-end", "change", validateBacktestDates);

init();
