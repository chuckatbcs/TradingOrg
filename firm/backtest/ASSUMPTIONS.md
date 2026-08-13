# Firm Backtest — Parameter Assumption Validation

This document compares TradingOrg firm defaults against authoritative references.
Validated via web research on 2026-07-02. The LLM fusion threshold is internal design only.

**Disclaimer:** These are guidelines, not guarantees. Our hybrid backtest uses an LLM *proxy*, not replayed agent output.

---

## Risk per trade: 1.2% (`FIRM_RISK_PER_TRADE=0.012`)

| | Value |
|---|--------|
| **Our default** | 1.2% of equity per trade |
| **Authority guidance** | Van Tharp teaches fixed-percentage risk sizing; examples commonly use **1%** per idea on a $100k account ([Tharp Think Concepts](https://vantharpinstitute.com/tharp-think-trading-concepts/)). The Van Tharp Institute position-sizing calculator recommends **0.25%–1%** for most traders, with **1%–2%** for advanced traders ([Position Sizing Calculator](https://vantharpinstitute.com/tools/position-sizing-calculator/)). |
| **Discrepancy** | 1.2% sits at the upper end of the “typical” 0.25%–1% band but within the advanced 1%–2% range. |
| **Recommendation** | Reasonable for moderate-risk paper trading. Consider **1.0%** if drawdowns feel steep, or cap at **1%** until live track record exists. |

---

## Max position: 5% (`FIRM_MAX_POSITION_PCT=0.05`)

| | Value |
|---|--------|
| **Our default** | 5% notional cap per position (also used in Gemini `min(5%, 0.03/atr_pct)` cap) |
| **Authority guidance** | US mutual fund **50/5/10 rule**: no single issuer may exceed **5%** of fund assets in the diversified bucket ([NBER Digest](https://www.nber.org/digest/202607/regulatory-limits-concentration-mutual-fund-portfolios)). Institutional managers often treat **5%** as a high-conviction ceiling; diversified funds often stay **2–3%** per name ([HedgeTrace position sizing](https://hedgetrace.com/learn/position-sizing-institutions)). |
| **Discrepancy** | 5% aligns with regulatory “large position” threshold but is aggressive for a 15-position retail-style book. |
| **Recommendation** | Keep 5% as hard cap; typical fills should be smaller via ATR sizing. For live auto-execute, **3%** cap may be more conservative. |

---

## Daily loss limit: 3% (`FIRM_DAILY_LOSS_LIMIT_PCT=0.03`)

| | Value |
|---|--------|
| **Our default** | 3% daily loss triggers kill switch |
| **Authority guidance** | Major prop firms commonly set **4–5%** daily loss limits ([PropJournal daily loss guide](https://propjournal.net/guides/daily-loss-limit-guide)). Some programs use **3%** (e.g. The5ers Hyper Growth). Traders are advised to stop personally at **60–70%** of the firm limit ([Pass Prop Trading Firms](https://www.passproptradingfirms.com/rules/daily-loss-limit/)). |
| **Discrepancy** | Our 3% is **stricter** than the 4–5% industry norm — effectively a built-in safety buffer. |
| **Recommendation** | **No change needed** for risk protection. Document that this is tighter than FTMO-style 5% rules. |

---

## Stop 2× ATR / target 3× ATR (`stop_atr_mult=2`, `target_atr_mult=3`)

| | Value |
|---|--------|
| **Our default** | Stop at 2× ATR(14); target at 3× ATR |
| **Authority guidance** | **2× ATR** stop is widely cited as the standard swing-trade buffer ([TradeAlgo ATR guide](https://www.tradealgo.com/trading-guides/technical-analysis/average-true-range-atr-how-to-measure-volatility-for-better-trade-sizing), [Quant Signals backtest](https://quant-signals.com/atr-stop-loss-take-profit/)). **3× ATR** matches Chandelier Exit trailing convention ([Teqmo Charts](https://www.teqmocharts.com/2024/12/atr-stop-loss-strategy-how-to-set.html)). **2× stop / 3× target** implies ~1:1.5 R:R before costs ([HorizonAI ATR guide](https://www.horizontrading.ai/learn/atr-indicator-explained)). |
| **Discrepancy** | Aligned with common practice. Van Tharp often cites **2:1 or 3:1** reward-to-risk targets on the *trade plan*, which would imply wider targets relative to stop — our 2/3 ATR pair is slightly conservative on reward. |
| **Recommendation** | Keep defaults. Optionally test **2× / 4×** or **2× / 6×** for explicit 1:2 or 1:3 R:R in backtests. |

---

## ADX ≥ 20 trend filter (`FIRM_SCREENER_ADX_MIN=20`)

| | Value |
|---|--------|
| **Our default** | ADX ≥ 20 for momentum filter |
| **Authority guidance** | Wilder: ADX **below 20** = weak/trendless; **above 25** = strong trend ([Investopedia ADX](https://www.investopedia.com/terms/a/adx.asp)). Many practitioners use **25** as the confirmation threshold ([NexusFi ADX guide](https://nexusfi.com/a/indicators/adx-average-directional-index)). |
| **Discrepancy** | Our **20** is **more permissive** than Wilder’s 25 strong-trend line — accepts emerging/transition trends. |
| **Recommendation** | Reasonable for scoring mode. For strict mode, consider **ADX ≥ 25** to reduce chop entries. |

---

## RSI 30–70 band (`FIRM_SCREENER_RSI_LOW=30`, `FIRM_SCREENER_RSI_HIGH=70`)

| | Value |
|---|--------|
| **Our default** | Pass when RSI ∈ [30, 70] |
| **Authority guidance** | Wilder (1978): **70** overbought, **30** oversold, 14-period default ([Wikipedia RSI](https://en.wikipedia.org/wiki/Relative_strength_index), [Greeks.live](https://learn.greeks.live/path/who-established-the-70-and-30-rsi-levels/)). Zone between 30–70 is neutral ([Wikipedia](https://en.wikipedia.org/wiki/Relative_strength_index)). |
| **Discrepancy** | **Aligned** with canonical Wilder levels. Note: in strong trends RSI can remain >70 — band filter may exclude extended momentum names. |
| **Recommendation** | Keep defaults; consider widening to 25–75 or disabling band in bull regimes if backtests show missed trends. |

---

## Volume ≥ 1.2× 20-day average (`FIRM_SCREENER_VOLUME_RATIO_MIN=1.2`)

| | Value |
|---|--------|
| **Our default** | Current volume / 20-day SMA ≥ 1.2 |
| **Authority guidance** | Breakout confirmation literature often prefers **1.5×–2.0×** ([Trends and Breakouts](https://trendsandbreakouts.com/volume-confirmation-breakout-candles), [HeyGoTrade](https://www.heygotrade.com/en/blog/volume-confirms-breakouts-us-stock-traders/)). **1.2×** appears as a minimum screener threshold ([ChartMath consolidation breakout](https://chartmath.com/screens/20-day-consolidation-breakout-daily)). Pullback re-entries sometimes use **1.2–1.3×** ([EasySwing volume analysis](https://easyswing.trading/blog/volume-analysis-swing-trading/)). |
| **Discrepancy** | Our **1.2×** is on the **low** end — acceptable for pullbacks, loose for breakouts. |
| **Recommendation** | Consider **1.5×** for strict mode or breakout-focused screens; keep 1.2× for scoring mode breadth. |

---

## Fusion entry threshold: 0.5 (`FIRM_FUSION_ENTRY_THRESHOLD=0.5`)

| | Value |
|---|--------|
| **Our default** | `fused_score = (quant/100) × |llm| × regime_mult` must be ≥ 0.5 |
| **Authority guidance** | No external standard — internal hybrid design. |
| **Discrepancy** | N/A |
| **Recommendation** | Tune via hybrid backtest pass rate vs forward returns. Example: quant 80 + Buy (1.0) + bull (1.0) → 0.8 pass; quant 60 + Overweight (0.7) + choppy (0.5) → 0.21 fail. |

---

## Summary of recommended adjustments

| Parameter | Current | Suggested consideration |
|-----------|---------|-------------------------|
| Risk per trade | 1.2% | Optional tighten to 1.0% for live |
| Max position | 5% | OK as cap; typical size from ATR |
| Daily loss limit | 3% | Keep (conservative vs prop norms) |
| ATR stop / target | 2× / 3× | Keep; optional 2× / 4× for higher R:R |
| ADX min | 20 | Optional 25 in strict mode |
| RSI band | 30–70 | Keep |
| Volume ratio | 1.2× | Optional 1.5× in strict mode |
| Fusion threshold | 0.5 | Tune empirically |

---

## Portfolio scaling from $1,000

This section extrapolates the risk defaults above to a **$1,000** paper account and shows how rules behave as equity grows or shrinks. Dollar amounts use `FirmConfig` defaults unless noted. Sizing math matches `firm/risk/manager.py`, `firm/risk/sizing.py`, and `firm/ops/killswitch.py`.

**Fractional shares are not implemented** — all orders are whole shares. That constraint dominates small accounts.

### Dollar limits at $1,000 (defaults)

| Rule | Config key | % | At $1,000 |
|------|------------|---|-----------|
| Max position (notional cap) | `max_position_pct` | 5% | **$50** per name |
| Risk per trade (ATR budget) | `risk_per_trade` | 1.2% | **$12** at risk to stop |
| Daily loss kill switch | `daily_loss_limit_pct` | 3% | **$30** drawdown from session baseline halts new buys |
| Max open positions | `max_positions` | — | **15** (rarely reachable at $1k) |
| Max per sector | `max_sector_positions` | — | **3** names |
| Sector concentration warn | `sector_warn_pct` | 30% | **$300** notional in one sector |
| Peak drawdown size cut | (code constant) | −10% | Risk/trade halves → **$6** |
| Peak drawdown halt | (code constant) | −15% | **0 shares** — no new sizing |

Stop/target distances: **2× ATR** stop, **3× ATR** target (`stop_atr_mult`, `target_atr_mult`).

### ATR sizing + 5% cap at $1k

Sizing path (`compute_order_qty`):

1. `risk_amount = equity × risk_per_trade` → **$12**
2. `shares = max(1, floor(risk_amount / (ATR × 2)))` — ATR path wants multiple shares when volatility is low
3. Notional capped at `min(5%, 0.03 / atr_pct)` — for normal stocks the **5% cap binds first**; Gemini cap only shrinks below 5% when ATR% ≥ 60% (extreme volatility)
4. Whole shares only; **no trade** if 1 share costs more than the 5% cap

**Representative fills** (verified against `compute_order_qty`):

| Stock profile | Price | Typical ATR | Shares @ $1k | Notional | % of equity | Binding rule |
|---------------|-------|-------------|--------------|----------|-------------|--------------|
| Budget (e.g. F) | $12 | $0.30 | 4 | $48 | 4.8% | ATR sizing, under cap |
| Mid | $25 | $0.75 | 2 | $50 | 5.0% | Cap |
| Mid | $50 | $1.25 | 1 | $50 | 5.0% | Cap (ATR wanted 4 shares) |
| Large cap | $100 | $2.50 | **0** | — | — | **Untradeable** (1 share = $100 > $50 cap) |
| Mega cap | $500+ | — | **0** | — | — | **Untradeable** |

**Key implication:** at $1,000 the **affordable price ceiling is $50/share**. Most S&P 100 names (AAPL, MSFT, NVDA, etc.) cannot be bought at all. The system is effectively limited to lower-priced watchlist/universe names (`market_screener_min_price` = $5 floor, but practical ceiling is $50 at $1k).

When ATR sizing would be &lt; 1 share but price ≤ cap, the code forces **1 share** (`manager.py` lines 58–60). That can put actual risk **above** the 1.2% budget (e.g. 1× $50 name risks ~$2.50 to a $2.50 stop = 0.25% if stop holds, but gap risk is 5% of equity in one name).

### Scenarios: when rules bind vs. become practical

| Equity | Max position $ | Risk/trade $ | Daily halt $ | Max affordable price | Practical notes |
|--------|----------------|--------------|--------------|----------------------|-----------------|
| **$500** | $25 | $6 | $15 | $25/share | Only very low-priced names; 1–2 positions realistic |
| **$1,000** | $50 | $12 | $30 | $50/share | Mid-caps ≤ $50 tradeable; large caps blocked |
| **$2,500** | $125 | $30 | $75 | $125/share | $100 stocks open (1 share); still no $200+ mega caps |
| **$5,000** | $250 | $60 | $150 | $250/share | $200 names tradeable; $500+ still blocked |
| **$10,000** | $500 | $120 | $300 | $500/share | Most liquid US equities tradeable; ATR sizing starts to matter on cheap names |
| **$25,000** | $1,250 | $300 | $750 | $1,250/share | Rules approach design intent: cap rarely blocks; 15-position diversification feasible |

**Binding vs. practical:**

| Equity band | What binds |
|-------------|------------|
| $500–$1k | **Whole-share + 5% cap** — dominant; `max_positions` (15) and sector limits are irrelevant |
| $2.5k–$5k | **Cap still binds** on $100–$500 names; ATR sizing binds on volatile low-priced names |
| $10k+ | **ATR risk budget** drives size on most names; 5% cap is occasional on high-priced or forced 1-share fills |
| $25k+ | **Screener/fusion gates** and `max_positions` / sector limits matter more than share granularity |

Full 15-name diversification at 5% each deploys **75% of equity** ($750 at $1k). Remaining cash covers stops and avoids full investment — but at $1k you will not find 15 distinct affordable signals on the same day.

### Growth path (equity increases)

As the account grows:

1. **More tickers become eligible** — affordable price ceiling rises linearly with equity (5% of equity).
2. **ATR sizing gains granularity** — multiple shares per idea; actual risk approaches the 1.2% budget instead of “1 share at cap.”
3. **Diversification kicks in** — `max_positions` (15) and `max_sector_positions` (3) become meaningful above ~$5k–$10k.
4. **Sector warn (30%)** — at $10k, $3,000 in one sector triggers concentration awareness.
5. **Auto-buy throttle** — `auto_buy_per_scan_cap` (6) and cooldown (60 min) may cap activity before risk limits do.
6. **Kill switch scales** — daily halt at 3% grows ($30 → $300 at $10k), giving more dollar room but same percentage discipline.

### Retraction path (equity decreases)

Two independent mechanisms:

**A. Daily kill switch** (`killswitch.py`) — session baseline equity:

| Event | At $1,000 baseline |
|-------|-------------------|
| Equity drops to $970 | **−3%** → kill switch fires |
| Action | Close losing positions; block new buys until end of session |
| Dollar pain | **−$30** triggers protection |

**B. Peak drawdown sizing** (`manager.py`) — vs. peak equity, not daily baseline:

| Drawdown | Effect @ $1k | Risk/trade $ |
|----------|--------------|--------------|
| 0% to −9.9% | Full sizing | $12 |
| −10% to −14.9% | Half risk | $6 |
| ≤ −15% | **Halt sizing** | $0 (no new positions sized) |

**Combined retraction story at $1k:**

- A bad morning (−$30) → kill switch; no new buys rest of day even if sizing would allow.
- A sustained slide from $1,000 peak to $850 (−15%) → sizing returns **0 shares** until recovery.
- Open positions shrink in mark-to-market value; the system does not auto-scale down existing holdings.
- At $500 equity (50% peak drawdown), affordable ceiling drops to **$25/share** — many former positions become untradeable on add.

### Tie-in to backtest / screener findings

From the parameter validation above:

| Finding | Small-account impact |
|---------|---------------------|
| ADX ≥ 20 (permissive vs Wilder 25) | More candidates pass than strict trend filter — but still need affordable price |
| Volume ≥ 1.2× (loose vs 1.5–2× breakout norm) | Scoring mode keeps breadth; **strict mode + small size = very few trades** |
| Fusion threshold 0.5 | Many screened names never reach execution even if affordable |
| 1.2% risk / 5% cap | Designed for $10k+ books; at $1k the **cap and integer shares** override ATR math |

**Net effect:** backtest may show signal counts across a broad universe, but a **$1k paper account** will execute only a **subset** — mostly lower-priced passers. Expect **0–2 new positions per week** in practice, not 15.

### Paper trading expectations at $1,000

Realistic expectations (honest):

- **Universe mismatch** — watchlist and market screens include names you cannot afford; fusion may pass signals the sizer rejects (0 shares).
- **No fractional shares** — Alpaca supports fractions in live API, but this firm path does not use them.
- **Concentration** — each fill is often **5% of equity** (the cap), not the 1.2% risk target; a few correlated losers hit the **$30 daily halt** quickly.
- **Kill switch is tight** — 3% daily is stricter than many prop-firm 4–5% rules; one bad session pauses buying.
- **Growth to $2.5k–$5k** is the first threshold where behavior matches the documented risk design.
- **Paper mode** — `trading_mode=paper` uses Alpaca paper API; fills assume whole-share market orders with spread gate (`max_entry_spread_pct` 0.3%).

For tuning at small size (optional, not defaults): lower `max_position_pct` does not help affordability; raising equity or adding fractional-share support are the real fixes. Biasing the watchlist toward sub-$50 names is a practical workaround.

---

## Sources

- [Van Tharp — Tharp Think Trading Concepts](https://vantharpinstitute.com/tharp-think-trading-concepts/) (accessed Jul 2026)
- [Van Tharp Institute — Position Sizing Calculator](https://vantharpinstitute.com/tools/position-sizing-calculator/) (accessed Jul 2026)
- [MarketMates — Position Sizing (Van Tharp)](https://marketmates.com/learn/forex/position-sizing/) (accessed Jul 2026)
- [NBER — Regulatory Limits on Mutual Fund Concentration](https://www.nber.org/digest/202607/regulatory-limits-concentration-mutual-fund-portfolios) (accessed Jul 2026)
- [HedgeTrace — Institutional Position Sizing](https://hedgetrace.com/learn/position-sizing-institutions) (accessed Jul 2026)
- [PropJournal — Daily Loss Limit Guide](https://propjournal.net/guides/daily-loss-limit-guide) (accessed Jul 2026)
- [Pass Prop Trading Firms — Daily Loss Limit](https://www.passproptradingfirms.com/rules/daily-loss-limit/) (accessed Jul 2026)
- [Quant Signals — ATR Stop Loss Multipliers](https://quant-signals.com/atr-stop-loss-take-profit/) (accessed Jul 2026)
- [TradeAlgo — ATR Volatility and Trade Sizing](https://www.tradealgo.com/trading-guides/technical-analysis/average-true-range-atr-how-to-measure-volatility-for-better-trade-sizing) (accessed Jul 2026)
- [HorizonAI — ATR Indicator Explained](https://www.horizontrading.ai/learn/atr-indicator-explained) (accessed Jul 2026)
- [Investopedia — Average Directional Index (ADX)](https://www.investopedia.com/terms/a/adx.asp) (accessed Jul 2026)
- [NexusFi Academy — ADX Trend Strength Filter](https://nexusfi.com/a/indicators/adx-average-directional-index) (accessed Jul 2026)
- [Wikipedia — Relative Strength Index](https://en.wikipedia.org/wiki/Relative_strength_index) (accessed Jul 2026)
- [Trends and Breakouts — Volume Confirmation on Breakouts](https://trendsandbreakouts.com/volume-confirmation-breakout-candles) (accessed Jul 2026)
- [ChartMath — 20-Day Consolidation Breakout Screen](https://chartmath.com/screens/20-day-consolidation-breakout-daily) (accessed Jul 2026)
