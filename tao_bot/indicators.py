"""Indicateurs techniques (implémentés avec pandas, sans dépendance TA-Lib).

Toutes les fonctions ne regardent que le passé : la valeur à l'index ``i``
dépend uniquement des bougies ``<= i`` (aucun biais d'anticipation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile exponentielle."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile simple."""
    return series.rolling(period, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Lissage de Wilder (utilisé par RSI, ATR et ADX)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100)."""
    delta = close.diff()
    avg_gain = _wilder(delta.clip(lower=0), period)
    avg_loss = _wilder(-delta.clip(upper=0), period)
    out = 100 - 100 / (1 + avg_gain / avg_loss)
    # Aucune baisse sur la période -> RSI = 100 ; marché totalement plat -> 50.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range : mesure de volatilité en unités de prix."""
    return _wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index : force de la tendance (0-100)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_ = atr(df, period)
    plus_di = 100 * _wilder(plus_dm, period) / atr_
    minus_di = 100 * _wilder(minus_dm, period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return _wilder(dx.replace([np.inf, -np.inf], np.nan), period)


def donchian_high(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Plus haut des ``period`` dernières bougies (bougie courante incluse)."""
    return df["high"].rolling(period, min_periods=period).max()
