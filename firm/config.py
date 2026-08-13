"""Firm-wide configuration loaded from environment variables."""



from __future__ import annotations



import os

from dataclasses import dataclass, field, fields

from pathlib import Path



try:

    from dotenv import find_dotenv, load_dotenv



    load_dotenv(find_dotenv(usecwd=True))

    load_dotenv(find_dotenv(".env.enterprise", usecwd=True), override=False)

except ImportError:

    pass



_FIRM_HOME = Path(os.path.expanduser("~")) / ".tradingagents" / "firm"





def _env_bool(key: str, default: bool) -> bool:

    raw = os.environ.get(key)

    if raw is None or raw == "":

        return default

    return raw.strip().lower() in ("true", "1", "yes", "on")





def _env_float(key: str, default: float) -> float:

    raw = os.environ.get(key)

    return float(raw) if raw not in (None, "") else default





def _env_int(key: str, default: int) -> int:

    raw = os.environ.get(key)

    return int(raw) if raw not in (None, "") else default





@dataclass

class FirmConfig:

    """Moderate-risk defaults derived from trading-platform + Gemini experiments."""



    # Storage

    data_dir: Path = field(default_factory=lambda: _FIRM_HOME)



    # Trading mode — paper is the hard default

    trading_mode: str = "paper"  # paper | live

    allow_live_auto_execute: bool = False



    # Alpaca

    alpaca_api_key: str = ""

    alpaca_secret_key: str = ""

    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    alpaca_data_url: str = "https://data.alpaca.markets"



    # Risk limits

    max_positions: int = 15

    max_position_pct: float = 0.05

    daily_loss_limit_pct: float = 0.03

    risk_per_trade: float = 0.012

    stop_atr_mult: float = 2.0

    target_atr_mult: float = 3.0

    max_sector_positions: int = 3

    sector_warn_pct: float = 0.30



    # Hybrid fusion thresholds

    quant_min_score: float = 60.0

    screener_adx_min: float = 20.0

    screener_rsi_low: float = 30.0

    screener_rsi_high: float = 70.0

    screener_volume_ratio_min: float = 1.2

    screener_mode: str = "scoring"  # strict (all filters) | scoring (rank by score)

    fusion_entry_threshold: float = 0.5

    entry_ratings: tuple[str, ...] = ("Buy", "Overweight")

    exit_ratings: tuple[str, ...] = ("Sell", "Underweight")



    # Auto-execution throttles

    auto_buy_per_scan_cap: int = 6

    auto_buy_cooldown_minutes: int = 60

    max_entry_spread_pct: float = 0.003



    # Scheduler (ET)

    scheduler_enabled: bool = False

    premarket_screen_top_n: int = 5



    # Universe / market screener (stage 1)

    universe_mode: str = "watchlist"  # watchlist | market

    market_screener_max: int = 200

    market_screener_min_price: float = 5.0

    market_screener_actives_top: int = 100

    market_screener_include_actives: bool = True

    market_screener_include_actives_by_trades: bool = True

    market_screener_include_movers: bool = True

    market_screener_movers_min_pct: float = 2.0

    market_screener_include_watchlist: bool = True

    market_screener_include_spy_holdings: bool = False

    market_screener_spy_holdings_top: int = 15



    # Notifications

    discord_webhook_url: str = ""



    @classmethod

    def from_env(cls) -> FirmConfig:

        mode = os.environ.get("FIRM_TRADING_MODE", "paper").strip().lower()

        paper_url = "https://paper-api.alpaca.markets"

        live_url = "https://api.alpaca.markets"

        return cls(

            data_dir=Path(os.environ.get("FIRM_DATA_DIR", str(_FIRM_HOME))),

            trading_mode=mode,

            allow_live_auto_execute=_env_bool("FIRM_ALLOW_LIVE_AUTO_EXECUTE", False),

            alpaca_api_key=os.environ.get("ALPACA_API_KEY", ""),

            alpaca_secret_key=os.environ.get("ALPACA_SECRET_KEY")

            or os.environ.get("ALPACA_API_SECRET", ""),

            alpaca_base_url=os.environ.get(

                "ALPACA_BASE_URL", live_url if mode == "live" else paper_url

            ),

            alpaca_data_url=os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets"),

            max_positions=_env_int("FIRM_MAX_POSITIONS", 15),

            max_position_pct=_env_float("FIRM_MAX_POSITION_PCT", 0.05),

            daily_loss_limit_pct=_env_float("FIRM_DAILY_LOSS_LIMIT_PCT", 0.03),

            risk_per_trade=_env_float("FIRM_RISK_PER_TRADE", 0.012),

            quant_min_score=_env_float("FIRM_QUANT_MIN_SCORE", 60.0),

            screener_adx_min=_env_float("FIRM_SCREENER_ADX_MIN", 20.0),

            screener_rsi_low=_env_float("FIRM_SCREENER_RSI_LOW", 30.0),

            screener_rsi_high=_env_float("FIRM_SCREENER_RSI_HIGH", 70.0),

            screener_volume_ratio_min=_env_float("FIRM_SCREENER_VOLUME_RATIO_MIN", 1.2),

            screener_mode=os.environ.get("FIRM_SCREENER_MODE", "scoring").strip().lower(),

            fusion_entry_threshold=_env_float("FIRM_FUSION_ENTRY_THRESHOLD", 0.5),

            scheduler_enabled=_env_bool("FIRM_SCHEDULER_ENABLED", False),

            premarket_screen_top_n=_env_int("FIRM_PREMARKET_SCREEN_TOP_N", 5),

            universe_mode=os.environ.get("FIRM_UNIVERSE_MODE", "watchlist").strip().lower(),

            market_screener_max=_env_int("FIRM_MARKET_SCREENER_MAX", 200),

            market_screener_min_price=_env_float("FIRM_MARKET_SCREENER_MIN_PRICE", 5.0),

            market_screener_actives_top=_env_int("FIRM_MARKET_SCREENER_ACTIVES_TOP", 100),

            market_screener_include_actives=_env_bool("FIRM_MARKET_SCREENER_INCLUDE_ACTIVES", True),

            market_screener_include_actives_by_trades=_env_bool(
                "FIRM_MARKET_SCREENER_INCLUDE_ACTIVES_BY_TRADES", True
            ),

            market_screener_include_movers=_env_bool("FIRM_MARKET_SCREENER_INCLUDE_MOVERS", True),

            market_screener_movers_min_pct=_env_float("FIRM_MARKET_SCREENER_MOVERS_MIN_PCT", 2.0),

            market_screener_include_watchlist=_env_bool("FIRM_MARKET_SCREENER_INCLUDE_WATCHLIST", True),

            market_screener_include_spy_holdings=_env_bool(
                "FIRM_MARKET_SCREENER_INCLUDE_SPY_HOLDINGS", False
            ),

            market_screener_spy_holdings_top=_env_int("FIRM_MARKET_SCREENER_SPY_HOLDINGS_TOP", 15),

            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),

        )



    @classmethod

    def from_merged(cls) -> FirmConfig:

        """Defaults <- user_settings.json <- env (env wins when set)."""

        from firm.user_settings import TUNABLE_ENV, TUNABLE_KEYS, load_user_settings



        cfg = cls.from_env()

        user = load_user_settings(cfg.data_dir)

        for key in TUNABLE_KEYS:

            env_key = TUNABLE_ENV[key]

            if os.environ.get(env_key) in (None, "") and key in user:

                setattr(cfg, key, user[key])

        return cfg



    def can_auto_execute(self) -> bool:

        if self.trading_mode == "live" and not self.allow_live_auto_execute:

            return False

        return bool(self.alpaca_api_key and self.alpaca_secret_key)





FIRM_CONFIG = FirmConfig.from_merged()





def reload_firm_config() -> FirmConfig:

    """Reload merged config in-place so existing FIRM_CONFIG imports stay valid."""

    merged = FirmConfig.from_merged()

    for f in fields(FirmConfig):

        setattr(FIRM_CONFIG, f.name, getattr(merged, f.name))

    return FIRM_CONFIG

