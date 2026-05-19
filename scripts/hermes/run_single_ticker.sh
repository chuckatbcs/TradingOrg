#!/usr/bin/env bash
set -euo pipefail

TICKER="${1:-${TICKER:-SPY}}"
ANALYSIS_DATE="${2:-${ANALYSIS_DATE:-$(date +%F)}}"
PROVIDER="${LLM_PROVIDER:-ollama}"
BACKEND_URL="${LLM_BACKEND_URL:-${OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}}"
QUICK_MODEL="${QUICK_THINK_LLM:-${LOCAL_QUICK_MODEL:-qwen3:latest}}"
DEEP_MODEL="${DEEP_THINK_LLM:-${LOCAL_DEEP_MODEL:-qwen3:latest}}"
ANALYSTS="${TRADINGORG_ANALYSTS:-market,news,fundamentals}"
DEPTH="${TRADINGORG_DEPTH:-1}"

OUT_DIR="reports/signals/${TICKER}/${ANALYSIS_DATE}"
mkdir -p "${OUT_DIR}"

tradingagents-analyze-run \
  --ticker "${TICKER}" \
  --date "${ANALYSIS_DATE}" \
  --provider "${PROVIDER}" \
  --backend-url "${BACKEND_URL}" \
  --quick-model "${QUICK_MODEL}" \
  --deep-model "${DEEP_MODEL}" \
  --analysts "${ANALYSTS}" \
  --depth "${DEPTH}" \
  --checkpoint \
  --output-json "${OUT_DIR}/signal.json"

echo "Signal written to ${OUT_DIR}/signal.json"
