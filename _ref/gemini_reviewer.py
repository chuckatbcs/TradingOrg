import os
import json
import glob
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import requests
import sys

# Add root folder to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from utils.llm_client import call_gemini
from utils.portfolio_mgr import load_portfolio
from utils.data_fetcher import get_historical_data, calculate_atr

ORDERS_DIR = os.path.join(ROOT_DIR, "orders")
RESEARCH_DIR = os.path.join(ROOT_DIR, "research")
SUMMARY_DIR = os.path.join(ROOT_DIR, "summary")

class ReviewerAgent:
    def __init__(self, run_date: str = None):
        self.run_date = run_date or datetime.today().strftime("%Y-%m-%d")
        self.api_key = os.environ.get("ALPACA_API_KEY")
        self.api_secret = os.environ.get("ALPACA_API_SECRET")
        self.base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        
    def classify_macro_regime(self) -> dict:
        """
        Classifies the macro market regime using SPY historical data.
        Returns a dict: {"regime": "BULLISH_LOW_VOLATILITY", "spy_close": 540.2, "spy_sma20": 535.1, "atr_pct": 0.008}
        """
        print(f"[Reviewer] Classifying macro regime via SPY...")
        try:
            # Fetch SPY bars for the last 2 months to ensure 20 trading days are covered
            spy_df = get_historical_data("SPY", period="2mo")
            if spy_df.empty or len(spy_df) < 20:
                print("[Reviewer] SPY historical data insufficient. Defaulting to neutral regime.")
                return {
                    "regime": "CHOPPY_LOW_VOLATILITY",
                    "spy_close": 500.0,
                    "spy_sma20": 500.0,
                    "atr_pct": 0.01
                }
                
            spy_df['sma20'] = spy_df['Close'].rolling(window=20).mean()
            latest_close = float(spy_df['Close'].iloc[-1])
            latest_sma20 = float(spy_df['sma20'].iloc[-1])
            
            # Compute volatility using ATR
            atr_pct, abs_atr = calculate_atr(spy_df, period=14)
            
            # Trend Classification
            # If close is within 0.5% of SMA20, we call it Choppy
            pct_diff = (latest_close - latest_sma20) / latest_sma20
            if pct_diff > 0.005:
                trend = "BULLISH"
            elif pct_diff < -0.005:
                trend = "BEARISH"
            else:
                trend = "CHOPPY"
                
            # Volatility Classification (Median SPY daily ATR is around 1.0% - 1.2%)
            volatility = "HIGH_VOLATILITY" if atr_pct > 0.012 else "LOW_VOLATILITY"
            
            regime_name = f"{trend}_{volatility}"
            print(f"[Reviewer] SPY Regime: {regime_name} (Close: ${latest_close:.2f} | 20 SMA: ${latest_sma20:.2f} | ATR: {atr_pct*100:.2f}%)")
            
            return {
                "regime": regime_name,
                "spy_close": latest_close,
                "spy_sma20": latest_sma20,
                "atr_pct": atr_pct
            }
        except Exception as e:
            print(f"[Reviewer] Error classifying regime: {e}")
            return {
                "regime": "CHOPPY_LOW_VOLATILITY",
                "spy_close": 500.0,
                "spy_sma20": 500.0,
                "atr_pct": 0.01
            }

    def sync_todays_orders(self):
        """
        Queries Alpaca API to fetch fill details for today's orders.
        Updates local orders log files.
        """
        orders_file = os.path.join(ORDERS_DIR, f"approved_{self.run_date}.json")
        if not os.path.exists(orders_file):
            print(f"[Reviewer] No orders file found for {self.run_date} to sync.")
            return
            
        with open(orders_file, "r") as f:
            orders = json.load(f)
            
        if not self.api_key or not self.api_secret or not orders:
            return
            
        headers = {
            "APCA-API-KEY-ID": self.api_key.strip(),
            "APCA-API-SECRET-KEY": self.api_secret.strip()
        }
        
        updated = False
        for order in orders:
            order_id = order.get("alpaca_order_id")
            if not order_id or order_id.startswith("sim_") or order_id.startswith("err_"):
                continue
                
            # Only query Alpaca for non-terminal orders or to confirm details
            url = f"{self.base_url.strip('/')}/v2/orders/{order_id}"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    alpaca_order = res.json()
                    status = alpaca_order.get("status", "").upper()
                    fill_price = alpaca_order.get("filled_avg_price")
                    filled_qty = alpaca_order.get("filled_qty")
                    
                    if status:
                        order["status"] = f"ROUTED_ALPACA_{status}"
                    if fill_price:
                        order["fill_price"] = float(fill_price)
                    if filled_qty:
                        order["filled_qty"] = float(filled_qty)
                    updated = True
            except Exception as e:
                print(f"[Reviewer] Failed to sync order {order_id} from Alpaca: {e}")
                
        if updated:
            with open(orders_file, "w") as f:
                json.dump(orders, f, indent=2)
            print(f"[Reviewer] Updated {self.run_date} orders status and fill details.")

    def run_transaction_cost_analysis(self) -> dict:
        """
        Computes slippage across today's filled orders.
        """
        orders_file = os.path.join(ORDERS_DIR, f"approved_{self.run_date}.json")
        if not os.path.exists(orders_file):
            return {"avg_slippage_pct": 0.0, "total_trades": 0, "slippage_by_ticker": {}}
            
        with open(orders_file, "r") as f:
            orders = json.load(f)
            
        total_slippage = 0.0
        filled_count = 0
        slippage_by_ticker = {}
        
        for o in orders:
            # Check if filled
            status = o.get("status", "")
            is_filled = "FILLED" in status
            
            # If simulated, or real Alpaca, check execution price
            target_price = o.get("price", 0.0)
            fill_price = o.get("fill_price") or o.get("price")  # Fallback to target price for simulation
            
            # For simulated orders, let's assume they were filled
            if not is_filled and (o.get("alpaca_order_id") or "").startswith("sim_"):
                is_filled = True
                fill_price = target_price
                
            if is_filled and fill_price and target_price > 0:
                side = "buy" if o.get("action") in ["BUY", "RE-ENTER"] else "sell"
                
                if side == "buy":
                    slippage = (fill_price - target_price) / target_price
                else:
                    slippage = (target_price - fill_price) / target_price
                    
                slippage_pct = slippage * 100
                total_slippage += slippage_pct
                filled_count += 1
                
                slippage_by_ticker[o["ticker"]] = {
                    "target_price": target_price,
                    "fill_price": fill_price,
                    "slippage_pct": slippage_pct,
                    "side": side
                }
                
        avg_slippage = (total_slippage / filled_count) if filled_count > 0 else 0.0
        return {
            "avg_slippage_pct": avg_slippage,
            "total_trades": filled_count,
            "slippage_by_ticker": slippage_by_ticker
        }

    def calculate_pnl(self) -> dict:
        """
        Calculates realized PnL by crawling historical order files chronologically,
        and unrealized PnL of active positions.
        """
        print("[Reviewer] Analyzing realized and unrealized PnL...")
        # 1. Realized PnL calculations from approved order history
        approved_files = sorted(glob.glob(os.path.join(ORDERS_DIR, "approved_*.json")))
        
        # Track simulated holdings state to match buys/sells
        holdings = {} # ticker -> list of {"price": float, "shares": float, "date": str}
        realized_pnl = 0.0
        total_realized_trades = 0
        winning_trades = 0
        losing_trades = 0
        
        ticker_stats = {} # ticker -> {"realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0}
        
        for f_path in approved_files:
            try:
                with open(f_path, "r") as f:
                    orders = json.load(f)
            except Exception:
                continue
                
            for o in orders:
                ticker = o["ticker"]
                action = o["action"]
                shares = float(o.get("shares", 0.0))
                target_price = float(o.get("price", 0.0))
                # Reconcile price with actual fill if logged
                fill_price = float(o.get("fill_price") or target_price)
                
                status = o.get("status", "")
                is_filled = "FILLED" in status or (o.get("alpaca_order_id") or "").startswith("sim_")
                
                if not is_filled or shares <= 0:
                    continue
                    
                if ticker not in ticker_stats:
                    ticker_stats[ticker] = {"realized_pnl": 0.0, "trades_count": 0, "wins": 0, "losses": 0, "total_slippage_pct": 0.0}
                    
                if action in ["BUY", "RE-ENTER"]:
                    if ticker not in holdings:
                        holdings[ticker] = []
                    holdings[ticker].append({
                        "price": fill_price,
                        "shares": shares
                    })
                    
                elif action in ["SELL", "DOWNSIZE"]:
                    shares_to_sell = shares
                    trade_pnl = 0.0
                    
                    # FIFO matching
                    if ticker in holdings and holdings[ticker]:
                        while shares_to_sell > 0 and holdings[ticker]:
                            lot = holdings[ticker][0]
                            matched_shares = min(shares_to_sell, lot["shares"])
                            
                            pnl_chunk = (fill_price - lot["price"]) * matched_shares
                            trade_pnl += pnl_chunk
                            
                            lot["shares"] -= matched_shares
                            shares_to_sell -= matched_shares
                            
                            if lot["shares"] <= 0:
                                holdings[ticker].pop(0)
                                
                        realized_pnl += trade_pnl
                        total_realized_trades += 1
                        ticker_stats[ticker]["trades_count"] += 1
                        ticker_stats[ticker]["realized_pnl"] += trade_pnl
                        
                        if trade_pnl > 0:
                            winning_trades += 1
                            ticker_stats[ticker]["wins"] += 1
                        else:
                            losing_trades += 1
                            ticker_stats[ticker]["losses"] += 1
                            
        # 2. Unrealized PnL from current portfolio positions
        portfolio = load_portfolio(sync_live=True)
        unrealized_pnl = 0.0
        portfolio_value = portfolio.get("portfolio_value", 1000.0)
        positions = portfolio.get("positions", [])
        
        # If we have real positions, calculate unrealized PL
        active_positions_details = []
        for pos in positions:
            ticker = pos["ticker"]
            shares = pos["shares"]
            entry_price = pos["entry_price"]
            
            # Fetch latest price
            current_price = entry_price
            if self.api_key and self.api_secret:
                try:
                    # Get quote
                    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
                    headers = {
                        "APCA-API-KEY-ID": self.api_key.strip(),
                        "APCA-API-SECRET-KEY": self.api_secret.strip()
                    }
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        current_price = float(res.json().get("trade", {}).get("p", entry_price))
                except:
                    pass
            
            pos_pnl = (current_price - entry_price) * shares
            unrealized_pnl += pos_pnl
            active_positions_details.append({
                "ticker": ticker,
                "shares": shares,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": pos_pnl,
                "unrealized_pnl_pct": ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
            })
            
        win_rate = (winning_trades / total_realized_trades) if total_realized_trades > 0 else 0.0
        
        return {
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
            "total_realized_trades": total_realized_trades,
            "win_rate": win_rate,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "ticker_stats": ticker_stats,
            "active_positions": active_positions_details,
            "portfolio_value": portfolio_value
        }

    def generate_lessons_learned(self, pnl_data: dict, regime_data: dict, tca_data: dict):
        """
        Uses Gemini to generate lessons learned and self-corrective directives for analysts.
        Saves to research/lessons_learned.md.
        """
        print("[Reviewer] Formulation lessons learned via Gemini...")
        
        # Build trade performance context
        trades_str = ""
        for ticker, stat in pnl_data["ticker_stats"].items():
            trades_str += f"- {ticker}: PnL: ${stat['realized_pnl']:.2f} | Trades: {stat['trades_count']} | Win Rate: {stat['wins']/(stat['trades_count'] or 1)*100:.1f}%\n"
            
        active_str = ""
        for pos in pnl_data["active_positions"]:
            active_str += f"- {pos['ticker']}: Cost: ${pos['entry_price']:.2f} | Current: ${pos['current_price']:.2f} | Unpl: ${pos['unrealized_pnl']:.2f} ({pos['unrealized_pnl_pct']:.1f}%)\n"
            
        prompt = (
            f"Analyze the performance of our quantitative trading system and formulate lessons learned.\n\n"
            f"### Market Environment\n"
            f"- Macro Regime: {regime_data['regime']}\n"
            f"- SPY Close vs 20-day SMA: ${regime_data['spy_close']:.2f} vs ${regime_data['spy_sma20']:.2f}\n"
            f"- SPY ATR Volatility: {regime_data['atr_pct']*100:.2f}%\n\n"
            f"### Closed Trades (Realized PnL)\n"
            f"Total Realized PnL: ${pnl_data['realized_pnl']:.2f}\n"
            f"Win Rate: {pnl_data['win_rate']*100:.1f}% ({pnl_data['winning_trades']} wins, {pnl_data['losing_trades']} losses)\n"
            f"{trades_str or 'No closed trades logged yet.'}\n\n"
            f"### Active Positions (Unrealized PnL)\n"
            f"Total Unrealized PnL: ${pnl_data['unrealized_pnl']:.2f}\n"
            f"{active_str or 'No active positions.'}\n\n"
            f"### Execution Analysis (Slippage)\n"
            f"Average slippage: {tca_data['avg_slippage_pct']:.3f}%\n"
            f"Analyze these results, identify analyst biases, and provide qualitative guidelines. "
            f"At the very end of your response, output a section titled '### Directives' with 3-5 bullet points of instructions for Bull and Bear Analysts. "
            f"Keep directives short, punchy, and actionable (e.g., 'Avoid long catalysts when SPY is in BEARISH_HIGH_VOLATILITY', 'Increase discount margins on high slippage tickers')."
        )
        
        system_instruction = (
            "You are an expert Post-Trade Reviewer Agent at a quantitative trading firm.\n"
            "Formulate detailed qualitative reflection reports on trading strategy performance.\n"
            "Be critical of mistakes, identify slippage issues, and output clear analyst system guidelines in a '### Directives' section."
        )
        
        try:
            report_content = call_gemini(prompt, system_instruction)
            os.makedirs(RESEARCH_DIR, exist_ok=True)
            report_path = os.path.join(RESEARCH_DIR, "lessons_learned.md")
            with open(report_path, "w") as f:
                f.write(report_content)
            print(f"[Reviewer] Generated lessons learned in {os.path.basename(report_path)}")
        except Exception as e:
            print(f"[Reviewer] Gemini lessons learned generation failed: {e}")

    def compute_suggestions(self, pnl_data: dict, regime_data: dict, tca_data: dict) -> dict:
        """
        Suggests system-level rules (blacklist, arbitrator weight shift) based on quantitative performance.
        Saves to research/suggested_rules.json.
        """
        print("[Reviewer] Computing suggestions...")
        
        # 1. Weight shifts based on regime
        # Default: 0.6 bull, 0.4 bear
        regime = regime_data.get("regime", "CHOPPY_LOW_VOLATILITY")
        
        if "BULLISH" in regime:
            bull_weight = 0.70
            bear_weight = 0.30
            weight_reason = "Market regime is Bullish. Shifting weight to Bull Analyst for long-bias dominance."
        elif "BEARISH" in regime:
            bull_weight = 0.30
            bear_weight = 0.70
            weight_reason = "Market regime is Bearish. Shifting weight to Bear Analyst for defensive/short hedging."
        else:
            bull_weight = 0.50
            bear_weight = 0.50
            weight_reason = "Market is Choppy/Neutral. Balancing weights equally (0.50 Bull, 0.50 Bear) to prevent trend bias."
            
        suggested_weights = {
            "bull_weight": bull_weight,
            "bear_weight": bear_weight
        }
        
        # 2. Blacklist candidate search
        suggested_blacklist = []
        blacklist_reasons = {}
        
        # Ticker stats review
        portfolio_value = pnl_data.get("portfolio_value", 1000.0)
        loss_threshold = portfolio_value * 0.01  # 1% of portfolio value (e.g. $10.00 for $1000 portfolio)
        
        ticker_stats = pnl_data.get("ticker_stats", {})
        for ticker, stat in ticker_stats.items():
            real_pnl = stat.get("realized_pnl", 0.0)
            trades = stat.get("trades_count", 0)
            wins = stat.get("wins", 0)
            
            # Loss threshold check
            if real_pnl < -loss_threshold:
                suggested_blacklist.append(ticker)
                blacklist_reasons[ticker] = f"Cumulative realized loss (${abs(real_pnl):.2f}) exceeded 1% of portfolio value."
                
            # Win rate check (at least 2 trades, win rate < 30%)
            elif trades >= 2 and (wins / trades) < 0.30:
                suggested_blacklist.append(ticker)
                blacklist_reasons[ticker] = f"Win rate too low ({wins}/{trades} wins) after multiple trades."
                
        # TCA slippage check
        slippage_by_ticker = tca_data.get("slippage_by_ticker", {})
        for ticker, info in slippage_by_ticker.items():
            slip_pct = info.get("slippage_pct", 0.0)
            if slip_pct > 1.0: # Exceeds 1.0% slippage
                if ticker not in suggested_blacklist:
                    suggested_blacklist.append(ticker)
                    blacklist_reasons[ticker] = f"Average execution slippage ({slip_pct:.2f}%) exceeds acceptable threshold of 1.0%."
                    
        suggestions = {
            "suggested_weights": suggested_weights,
            "suggested_blacklist": suggested_blacklist,
            "reasons": {
                "weights": weight_reason,
                "blacklist": blacklist_reasons
            }
        }
        
        suggested_file = os.path.join(RESEARCH_DIR, "suggested_rules.json")
        with open(suggested_file, "w") as f:
            json.dump(suggestions, f, indent=2)
            
        print(f"[Reviewer] Saved rule recommendations to {os.path.basename(suggested_file)}")
        return suggestions

    def execute_review(self) -> dict:
        """
        Executes the full post-trade review pipeline.
        Produces summary/reviewer_performance.json.
        """
        print(f"\n=== Running Post-Trade Reviewer for {self.run_date} ===")
        
        # 1. Sync orders
        self.sync_todays_orders()
        
        # 2. Run TCA
        tca_data = self.run_transaction_cost_analysis()
        
        # 3. Classify Regime
        regime_data = self.classify_macro_regime()
        
        # 4. Calculate PnL
        pnl_data = self.calculate_pnl()
        
        # 5. Formulate Lessons Learned
        self.generate_lessons_learned(pnl_data, regime_data, tca_data)
        
        # 6. Compute suggestions
        suggestions = self.compute_suggestions(pnl_data, regime_data, tca_data)
        
        # 7. Compile final Performance summary
        performance_summary = {
            "run_date": self.run_date,
            "macro_regime": regime_data["regime"],
            "spy_close": regime_data["spy_close"],
            "spy_sma20": regime_data["spy_sma20"],
            "spy_atr_pct": regime_data["atr_pct"],
            "realized_pnl": pnl_data["realized_pnl"],
            "unrealized_pnl": pnl_data["unrealized_pnl"],
            "total_pnl": pnl_data["total_pnl"],
            "win_rate": pnl_data["win_rate"],
            "total_realized_trades": pnl_data["total_realized_trades"],
            "avg_slippage_pct": tca_data["avg_slippage_pct"],
            "winning_trades": pnl_data["winning_trades"],
            "losing_trades": pnl_data["losing_trades"]
        }
        
        os.makedirs(SUMMARY_DIR, exist_ok=True)
        summary_path = os.path.join(SUMMARY_DIR, "reviewer_performance.json")
        with open(summary_path, "w") as f:
            json.dump(performance_summary, f, indent=2)
            
        print(f"[Reviewer] Saved performance summary to {os.path.basename(summary_path)}")
        return performance_summary

if __name__ == "__main__":
    reviewer = ReviewerAgent()
    reviewer.execute_review()
