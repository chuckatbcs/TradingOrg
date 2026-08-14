"""User-tunable firm settings persisted to JSON (merged with env on load)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Source = Literal["default", "user_file", "env"]

USER_SETTINGS_FILENAME = "user_settings.json"

# Keys the UI may read/write. Secrets and live-trading flags are excluded.
TUNABLE_KEYS: tuple[str, ...] = (
    "quant_min_score",
    "screener_adx_min",
    "screener_rsi_low",
    "screener_rsi_high",
    "screener_volume_ratio_min",
    "screener_mode",
    "fusion_entry_threshold",
    "premarket_screen_top_n",
    "max_positions",
    "max_position_pct",
    "daily_loss_limit_pct",
    "risk_per_trade",
    "universe_mode",
    "market_screener_max",
    "market_screener_min_price",
    "market_screener_actives_top",
    "market_screener_include_actives",
    "market_screener_include_actives_by_trades",
    "market_screener_include_movers",
    "market_screener_movers_min_pct",
    "market_screener_include_watchlist",
    "market_screener_include_spy_holdings",
    "market_screener_spy_holdings_top",
)

TUNABLE_ENV: dict[str, str] = {
    "quant_min_score": "FIRM_QUANT_MIN_SCORE",
    "screener_adx_min": "FIRM_SCREENER_ADX_MIN",
    "screener_rsi_low": "FIRM_SCREENER_RSI_LOW",
    "screener_rsi_high": "FIRM_SCREENER_RSI_HIGH",
    "screener_volume_ratio_min": "FIRM_SCREENER_VOLUME_RATIO_MIN",
    "screener_mode": "FIRM_SCREENER_MODE",
    "fusion_entry_threshold": "FIRM_FUSION_ENTRY_THRESHOLD",
    "premarket_screen_top_n": "FIRM_PREMARKET_SCREEN_TOP_N",
    "max_positions": "FIRM_MAX_POSITIONS",
    "max_position_pct": "FIRM_MAX_POSITION_PCT",
    "daily_loss_limit_pct": "FIRM_DAILY_LOSS_LIMIT_PCT",
    "risk_per_trade": "FIRM_RISK_PER_TRADE",
    "universe_mode": "FIRM_UNIVERSE_MODE",
    "market_screener_max": "FIRM_MARKET_SCREENER_MAX",
    "market_screener_min_price": "FIRM_MARKET_SCREENER_MIN_PRICE",
    "market_screener_actives_top": "FIRM_MARKET_SCREENER_ACTIVES_TOP",
    "market_screener_include_actives": "FIRM_MARKET_SCREENER_INCLUDE_ACTIVES",
    "market_screener_include_actives_by_trades": "FIRM_MARKET_SCREENER_INCLUDE_ACTIVES_BY_TRADES",
    "market_screener_include_movers": "FIRM_MARKET_SCREENER_INCLUDE_MOVERS",
    "market_screener_movers_min_pct": "FIRM_MARKET_SCREENER_MOVERS_MIN_PCT",
    "market_screener_include_watchlist": "FIRM_MARKET_SCREENER_INCLUDE_WATCHLIST",
    "market_screener_include_spy_holdings": "FIRM_MARKET_SCREENER_INCLUDE_SPY_HOLDINGS",
    "market_screener_spy_holdings_top": "FIRM_MARKET_SCREENER_SPY_HOLDINGS_TOP",
}

SETTING_META: dict[str, dict[str, Any]] = {
    "quant_min_score": {
        "label": "Min quant score",
        "group": "screener",
        "type": "float",
        "min": 0,
        "max": 100,
    },
    "screener_adx_min": {
        "label": "ADX minimum",
        "group": "screener",
        "type": "float",
        "min": 0,
        "max": 100,
    },
    "screener_rsi_low": {
        "label": "RSI low",
        "group": "screener",
        "type": "float",
        "min": 0,
        "max": 100,
    },
    "screener_rsi_high": {
        "label": "RSI high",
        "group": "screener",
        "type": "float",
        "min": 0,
        "max": 100,
    },
    "screener_volume_ratio_min": {
        "label": "Volume ratio min",
        "group": "screener",
        "type": "float",
        "min": 0.1,
        "max": 10,
    },
    "screener_mode": {
        "label": "Stage-2 screener mode",
        "group": "screener",
        "type": "select",
        "options": ["strict", "scoring"],
    },
    "fusion_entry_threshold": {
        "label": "Fusion entry threshold",
        "group": "fusion",
        "type": "float",
        "min": 0,
        "max": 1,
    },
    "premarket_screen_top_n": {
        "label": "Premarket screen top N",
        "group": "scheduler",
        "type": "int",
        "min": 1,
        "max": 50,
    },
    "max_positions": {
        "label": "Max positions",
        "group": "risk",
        "type": "int",
        "min": 1,
        "max": 100,
        "warning": "Changing risk limits affects live portfolio exposure.",
    },
    "max_position_pct": {
        "label": "Max position %",
        "group": "risk",
        "type": "float",
        "min": 0.001,
        "max": 0.5,
        "warning": "Changing risk limits affects live portfolio exposure.",
    },
    "daily_loss_limit_pct": {
        "label": "Daily loss limit %",
        "group": "risk",
        "type": "float",
        "min": 0.001,
        "max": 0.5,
        "warning": "Changing risk limits affects live portfolio exposure.",
    },
    "risk_per_trade": {
        "label": "Risk per trade",
        "group": "risk",
        "type": "float",
        "min": 0.001,
        "max": 0.1,
        "warning": "Changing risk limits affects live portfolio exposure.",
    },
    "universe_mode": {
        "label": "Universe mode",
        "group": "universe",
        "type": "select",
        "options": ["watchlist", "market"],
    },
    "market_screener_max": {
        "label": "Market screener max symbols",
        "group": "universe",
        "type": "int",
        "min": 10,
        "max": 200,
    },
    "market_screener_min_price": {
        "label": "Movers min price ($)",
        "group": "universe",
        "type": "float",
        "min": 0,
        "max": 1000,
    },
    "market_screener_actives_top": {
        "label": "Most-actives top N",
        "group": "universe",
        "type": "int",
        "min": 1,
        "max": 100,
    },
    "market_screener_include_actives": {
        "label": "Scan: most-actives (volume)",
        "group": "universe",
        "type": "bool",
    },
    "market_screener_include_actives_by_trades": {
        "label": "Scan: most-actives (trade count)",
        "group": "universe",
        "type": "bool",
    },
    "market_screener_include_movers": {
        "label": "Scan: market movers (gainers/losers)",
        "group": "universe",
        "type": "bool",
    },
    "market_screener_movers_min_pct": {
        "label": "Movers min % change",
        "group": "universe",
        "type": "float",
        "min": 0,
        "max": 50,
    },
    "market_screener_include_watchlist": {
        "label": "Always merge watchlist",
        "group": "universe",
        "type": "bool",
    },
    "market_screener_include_spy_holdings": {
        "label": "Scan: SPY top holdings",
        "group": "universe",
        "type": "bool",
    },
    "market_screener_spy_holdings_top": {
        "label": "SPY holdings top N",
        "group": "universe",
        "type": "int",
        "min": 1,
        "max": 50,
    },
}


def user_settings_path(data_dir: Path) -> Path:
    return data_dir / USER_SETTINGS_FILENAME


def _env_is_set(env_key: str) -> bool:
    raw = os.environ.get(env_key)
    return raw not in (None, "")


def load_user_settings(data_dir: Path) -> dict[str, Any]:
    path = user_settings_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {k: v for k, v in data.items() if k in TUNABLE_KEYS}
    if "watchlist_extra" in data:
        out["watchlist_extra"] = data["watchlist_extra"]
    return out


def save_user_settings(data_dir: Path, settings: dict[str, Any]) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = user_settings_path(data_dir)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except (json.JSONDecodeError, OSError):
            existing = {}
    filtered = {k: settings[k] for k in TUNABLE_KEYS if k in settings}
    merged = {**existing, **filtered}
    if "watchlist_extra" in settings:
        merged["watchlist_extra"] = settings["watchlist_extra"]
    elif "watchlist_extra" in existing and "watchlist_extra" not in settings:
        merged["watchlist_extra"] = existing["watchlist_extra"]
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def reset_user_settings(data_dir: Path) -> None:
    path = user_settings_path(data_dir)
    if path.exists():
        path.unlink()


def _coerce_value(key: str, raw: Any) -> int | float | str | bool:
    meta = SETTING_META[key]
    if meta["type"] == "int":
        return int(raw)
    if meta["type"] == "float":
        return float(raw)
    if meta["type"] == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    if meta["type"] == "select":
        value = str(raw).strip().lower()
        options = [str(o).lower() for o in meta.get("options", [])]
        if value not in options:
            raise ValueError(f"{key}: must be one of {', '.join(options)}")
        return value
    return raw


def validate_setting(key: str, raw: Any) -> int | float | str | bool:
    if key not in TUNABLE_KEYS:
        raise ValueError(f"unknown setting: {key}")
    try:
        value = _coerce_value(key, raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key}: invalid value") from exc
    meta = SETTING_META[key]
    if meta["type"] in ("int", "float"):
        if value < meta["min"] or value > meta["max"]:
            raise ValueError(f"{key}: must be between {meta['min']} and {meta['max']}")
    return value


def validate_patch(patch: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, int | float | str | bool]:
    if not patch:
        raise ValueError("no settings provided")
    current = current or {}
    validated: dict[str, int | float | str | bool] = {}
    for key, raw in patch.items():
        validated[key] = validate_setting(key, raw)
    rsi_low = validated.get("screener_rsi_low", current.get("screener_rsi_low"))
    rsi_high = validated.get("screener_rsi_high", current.get("screener_rsi_high"))
    if rsi_low is not None and rsi_high is not None and rsi_low >= rsi_high:
        raise ValueError("screener_rsi_low must be less than screener_rsi_high")
    return validated


@dataclass
class SettingInfo:
    key: str
    label: str
    group: str
    type: str
    value: int | float | bool | str
    default: int | float | bool | str
    source: Source
    editable: bool
    env_key: str | None = None
    min: float | int | None = None
    max: float | int | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "type": self.type,
            "value": self.value,
            "default": self.default,
            "source": self.source,
            "editable": self.editable,
            "env_key": self.env_key,
            "min": self.min,
            "max": self.max,
            "warning": self.warning,
        }
        meta = SETTING_META.get(self.key, {})
        if meta.get("options"):
            out["options"] = meta["options"]
        return out


def _source_for_key(
    key: str,
    *,
    user: dict[str, Any],
) -> Source:
    env_key = TUNABLE_ENV.get(key)
    if env_key and _env_is_set(env_key):
        return "env"
    if key in user:
        return "user_file"
    return "default"


def build_settings_view(cfg: Any) -> dict[str, Any]:
    """Return tunable settings metadata + read-only system fields for API/UI."""
    from firm.config import FirmConfig

    defaults = FirmConfig()
    user = load_user_settings(cfg.data_dir)
    tunables: dict[str, dict[str, Any]] = {}

    for key in TUNABLE_KEYS:
        meta = SETTING_META[key]
        env_key = TUNABLE_ENV[key]
        default_val = getattr(defaults, key)
        effective_val = getattr(cfg, key)
        source = _source_for_key(key, user=user)
        editable = source != "env"
        info = SettingInfo(
            key=key,
            label=meta["label"],
            group=meta["group"],
            type=meta["type"],
            value=effective_val,
            default=default_val,
            source=source,
            editable=editable,
            env_key=env_key,
            min=meta.get("min"),
            max=meta.get("max"),
            warning=meta.get("warning"),
        )
        tunables[key] = info.to_dict()

    read_only = {
        "trading_mode": {
            "key": "trading_mode",
            "label": "Trading mode",
            "group": "system",
            "type": "string",
            "value": cfg.trading_mode,
            "editable": False,
            "note": "Set via FIRM_TRADING_MODE in environment (paper-only safety).",
        },
        "scheduler_enabled": {
            "key": "scheduler_enabled",
            "label": "Scheduler enabled",
            "group": "scheduler",
            "type": "bool",
            "value": cfg.scheduler_enabled,
            "editable": False,
            "note": "Set via FIRM_SCHEDULER_ENABLED in environment.",
        },
    }

    groups = ["screener", "universe", "fusion", "scheduler", "risk", "system"]
    by_group: dict[str, list[str]] = {g: [] for g in groups}
    for key, item in tunables.items():
        by_group[item["group"]].append(key)
    by_group["system"].extend(read_only.keys())

    from firm.universe.watchlist import load_watchlist_extra, watchlist_metadata

    wl = watchlist_metadata(cfg.data_dir)
    user_extra = load_watchlist_extra(cfg.data_dir)

    return {
        "settings": tunables,
        "read_only": read_only,
        "groups": by_group,
        "user_settings_path": str(user_settings_path(cfg.data_dir)),
        "universe": {
            "mode": cfg.universe_mode,
            "screener_mode": cfg.screener_mode,
            "market_screener_max": cfg.market_screener_max,
            "market_screener_min_price": cfg.market_screener_min_price,
            "market_screener_actives_top": cfg.market_screener_actives_top,
            "market_screener_include_actives": cfg.market_screener_include_actives,
            "market_screener_include_actives_by_trades": cfg.market_screener_include_actives_by_trades,
            "market_screener_include_movers": cfg.market_screener_include_movers,
            "market_screener_movers_min_pct": cfg.market_screener_movers_min_pct,
            "market_screener_include_watchlist": cfg.market_screener_include_watchlist,
            "market_screener_include_spy_holdings": cfg.market_screener_include_spy_holdings,
            "note": (
                "watchlist = static ~50 tickers; market = multi-scan union "
                "(Alpaca actives + movers + watchlist + optional SPY), then stage-2 quant filters. "
                "scoring mode ranks by composite score; strict requires all filters."
            ),
        },
        "watchlist": {
            "count": wl["count"],
            "static_seed_count": wl["static_seed_count"],
            "user_extra": user_extra,
            "expansion": wl["expansion"],
            "note": wl["note"],
        },
    }
