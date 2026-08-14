"""
Feature Engine
===============
Generates technical-analysis features from OHLCV data for both the
strategy engine (Layer 1-2 signals) and the ML model (Layer 3 filter).

FIXES APPLIED:
- #19 MODERATE: Vectorized the RSI consecutive-above-80 counter
  (was a slow row-by-row Python loop). Now uses cumulative group logic.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

try:
    from ta.trend import PSARIndicator
    HAS_TA = True
except ImportError:
    HAS_TA = False

logger = logging.getLogger(__name__)


def generate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accept an OHLCV DataFrame and return a copy enriched with all
    technical features needed by the strategy engine and ML model.

    Required input columns: open, high, low, close, volume
    """
    if df.empty or len(df) < 30:
        logger.warning("Not enough rows (%d) for feature generation", len(df))
        return pd.DataFrame()

    feat = df.copy()

    # Ensure numeric
    for col in ("open", "high", "low", "close", "volume"):
        if col in feat.columns:
            feat[col] = pd.to_numeric(feat[col], errors="coerce")

    # ------------------------------------------------------------------
    # Moving averages & regime
    # ------------------------------------------------------------------
    feat["sma_20"] = feat["close"].rolling(20).mean()
    feat["sma_50"] = feat["close"].rolling(50).mean()
    feat["ema_12"] = feat["close"].ewm(span=12, adjust=False).mean()
    feat["ema_21"] = feat["close"].ewm(span=21, adjust=False).mean()
    feat["ema_26"] = feat["close"].ewm(span=26, adjust=False).mean()
    feat["ema_50"] = feat["close"].ewm(span=50, adjust=False).mean()
    feat["ema_200"] = feat["close"].ewm(span=200, adjust=False).mean()

    # EMA-21 / EMA-50 crossover flag
    feat["ema_cross_21_50"] = 0
    cross_mask = feat["ema_21"].notna() & feat["ema_50"].notna()
    feat.loc[cross_mask & (feat["ema_21"] > feat["ema_50"]), "ema_cross_21_50"] = 1
    feat.loc[cross_mask & (feat["ema_21"] <= feat["ema_50"]), "ema_cross_21_50"] = -1

    # Regime flag (close vs EMA-200)
    feat["above_ema200"] = np.where(
        feat["ema_200"].notna(),
        (feat["close"] > feat["ema_200"]).astype(float),
        np.nan,
    )

    # ------------------------------------------------------------------
    # MACD
    # ------------------------------------------------------------------
    feat["macd"] = feat["ema_12"] - feat["ema_26"]
    feat["macd_signal"] = feat["macd"].ewm(span=9, adjust=False).mean()
    feat["macd_histogram"] = feat["macd"] - feat["macd_signal"]

    # ------------------------------------------------------------------
    # RSI (14)
    # ------------------------------------------------------------------
    delta = feat["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    feat["rsi_14"] = 100 - (100 / (1 + rs))

    # FIX #19: Vectorized RSI consecutive bars above 80
    # Old code used a slow row-by-row Python for loop.
    # New approach: use cumulative group counting via a reset-on-zero pattern.
    feat["rsi_above_80"] = (feat["rsi_14"] > 80).astype(int)
    # Create groups that reset when rsi_above_80 == 0
    _not_above = feat["rsi_above_80"] == 0
    _group_id = _not_above.cumsum()
    feat["rsi_consec_above_80"] = feat.groupby(_group_id)["rsi_above_80"].cumsum()
    # When rsi_above_80 == 0, the cumsum within that group gives 0, which is correct.

    # ------------------------------------------------------------------
    # ATR (14) - critical for risk management
    # ------------------------------------------------------------------
    high_low = feat["high"] - feat["low"]
    high_close = (feat["high"] - feat["close"].shift()).abs()
    low_close = (feat["low"] - feat["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    feat["atr_14"] = true_range.rolling(14).mean()

    # ------------------------------------------------------------------
    # Bollinger Bands
    # ------------------------------------------------------------------
    bb_mid = feat["sma_20"]
    bb_std = feat["close"].rolling(20).std()
    feat["bb_upper"] = bb_mid + 2 * bb_std
    feat["bb_lower"] = bb_mid - 2 * bb_std
    bb_width = feat["bb_upper"] - feat["bb_lower"]
    feat["bb_pct"] = np.where(
        bb_width > 0,
        (feat["close"] - feat["bb_lower"]) / bb_width,
        0.5,
    )

    # ------------------------------------------------------------------
    # ADX (14) - for regime strength
    # ------------------------------------------------------------------
    feat["adx"] = _calculate_adx(feat, period=14)
    feat["adx_pos"] = _calculate_di(feat, period=14, positive=True)
    feat["adx_neg"] = _calculate_di(feat, period=14, positive=False)

    # ------------------------------------------------------------------
    # Volatility & returns
    # ------------------------------------------------------------------
    feat["returns_1d"] = feat["close"].pct_change()
    feat["returns_5d"] = feat["close"].pct_change(5)
    feat["returns_10d"] = feat["close"].pct_change(10)
    feat["returns_20d"] = feat["close"].pct_change(20)
    feat["volatility_10d"] = feat["returns_1d"].rolling(10).std()
    feat["volatility_20d"] = feat["returns_1d"].rolling(20).std()
    feat["volume_sma_20"] = feat["volume"].rolling(20).mean()
    feat["volume_ratio"] = np.where(
        feat["volume_sma_20"] > 0,
        feat["volume"] / feat["volume_sma_20"],
        1.0,
    )

    # ------------------------------------------------------------------
    # Parabolic SAR (simplified binary flag)
    # ------------------------------------------------------------------
    if HAS_TA:
        try:
            psar = ta.psar(feat["high"], feat["low"], feat["close"])
            if psar is not None and not psar.empty:
                psar_long = psar.filter(like="PSARl").iloc[:, 0] if psar.filter(like="PSARl").shape[1] > 0 else pd.Series(np.nan, index=feat.index)
                feat["psar_bullish"] = (feat["close"] > psar_long).astype(float)
            else:
                feat["psar_bullish"] = 0.5
        except Exception:
            feat["psar_bullish"] = 0.5
    else:
        feat["psar_bullish"] = 0.5

    # ------------------------------------------------------------------
    # Trend score (composite)
    # ------------------------------------------------------------------
    feat["trend_score"] = _compute_trend_score(feat)

    # ------------------------------------------------------------------
    # Clean up: drop warmup NaN rows only on ML feature columns
    # ------------------------------------------------------------------
    ml_feature_cols = get_ml_feature_columns()
    available_cols = [c for c in ml_feature_cols if c in feat.columns]
    feat = feat.dropna(subset=available_cols)

    return feat


# ======================================================================
# Helper functions
# ======================================================================

def get_ml_feature_columns() -> list[str]:
    """Columns the XGBoost model trains on (subset of all features)."""
    return [
        "sma_20", "sma_50", "ema_12", "ema_21", "ema_26", "ema_50",
        "macd", "macd_signal", "macd_histogram",
        "rsi_14", "atr_14",
        "bb_upper", "bb_lower", "bb_pct",
        "adx", "adx_pos", "adx_neg",
        "returns_1d", "returns_5d", "returns_10d", "returns_20d",
        "volatility_10d", "volatility_20d",
        "volume_ratio",
        "psar_bullish",
        "ema_cross_21_50",
        "above_ema200",
        "trend_score",
    ]


def _compute_trend_score(df: pd.DataFrame) -> pd.Series:
    """Composite 0-1 trend score (simple weighted average of signals)."""
    score = pd.Series(0.0, index=df.index)

    if "macd_histogram" in df.columns:
        score += (df["macd_histogram"] > 0).astype(float) * 0.20
    if "rsi_14" in df.columns:
        score += ((df["rsi_14"] > 30) & (df["rsi_14"] < 70)).astype(float) * 0.15
    if "ema_cross_21_50" in df.columns:
        score += (df["ema_cross_21_50"] == 1).astype(float) * 0.25
    if "above_ema200" in df.columns:
        score += df["above_ema200"].fillna(0) * 0.20
    if "adx" in df.columns:
        score += (df["adx"] >= 20).astype(float) * 0.10
    if "bb_pct" in df.columns:
        score += ((df["bb_pct"] > 0.2) & (df["bb_pct"] < 0.8)).astype(float) * 0.10

    return score.clip(0, 1)


def _calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX from OHLC data."""
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(period).mean()
    return adx


def _calculate_di(df: pd.DataFrame, period: int = 14, positive: bool = True) -> pd.Series:
    """Compute +DI or -DI."""
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()

    if positive:
        dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    else:
        dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    di = 100 * (dm.rolling(period).mean() / atr.replace(0, np.nan))
    return di

