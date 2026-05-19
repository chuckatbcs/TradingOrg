#!/usr/bin/env bash
set -euo pipefail

WATCHLIST="${WATCHLIST:-SPY,NVDA,AAPL,MSFT}"
ANALYSIS_DATE="${ANALYSIS_DATE:-$(date +%F)}"

IFS=',' read -ra SYMBOLS <<< "${WATCHLIST}"
for symbol in "${SYMBOLS[@]}"; do
  symbol="$(echo "${symbol}" | xargs)"
  if [[ -z "${symbol}" ]]; then
    continue
  fi
  echo "=== Running TradingOrg analysis for ${symbol} on ${ANALYSIS_DATE} ==="
  "$(dirname "$0")/run_single_ticker.sh" "${symbol}" "${ANALYSIS_DATE}"
done

echo "Daily watchlist complete for ${ANALYSIS_DATE}"
