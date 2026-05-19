#!/usr/bin/env bash
set -euo pipefail

TICKER="${1:-${TICKER:-SPY}}"
ANALYSIS_DATE="${2:-${ANALYSIS_DATE:-$(date +%F)}}"
HOLDING_DAYS="${HOLDING_DAYS:-1}"
SIGNAL_PATH="${SIGNAL_PATH:-reports/signals/${TICKER}/${ANALYSIS_DATE}/signal.json}"
OUT_DIR="reports/postgame/${TICKER}/${ANALYSIS_DATE}"
REVIEW_PATH="${OUT_DIR}/review.json"
LESSONS_PATH="${LESSONS_PATH:-memory/lessons.jsonl}"

mkdir -p "${OUT_DIR}" memory

tradingagents-postgame \
  --signal "${SIGNAL_PATH}" \
  --ticker "${TICKER}" \
  --date "${ANALYSIS_DATE}" \
  --holding-days "${HOLDING_DAYS}" \
  --output-json "${REVIEW_PATH}"

tradingagents-update-lessons \
  --review "${REVIEW_PATH}" \
  --lessons "${LESSONS_PATH}"

echo "Review written to ${REVIEW_PATH}"
echo "Lesson appended to ${LESSONS_PATH}"
