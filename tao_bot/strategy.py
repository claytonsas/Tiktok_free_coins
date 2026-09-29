"""Stratégie spécialisée TAO (Bittensor).

Le TAO est un actif très volatil (souvent 5 à 10 % de variation par jour),
qui évolue en longues tendances et reste fortement corrélé au BTC et au
narratif « IA ». La stratégie en découle :

1. **Régime** : on n'achète que si la tendance de fond est haussière
   (prix > EMA200, EMA50 > EMA200) et que le BTC est lui aussi au-dessus
   de son EMA200 (quand le BTC chute, le TAO chute généralement plus fort).
2. **Entrées** (spot, long uniquement) :
   - *breakout* : clôture au-dessus du plus haut des 20 bougies précédentes,
     avec volume > 1,5x la moyenne et ADX >= 20 (tendance réelle) ;
   - *pullback* : dans la tendance haussière, le RSI repasse au-dessus de 40
     après une correction, sur une bougie verte.
3. **Filtres** : pas d'achat si le RSI est en surchauffe ou si la volatilité
   (ATR / prix) est extrême.
4. **Sorties** : stop initial à 2,5 ATR, stop suiveur « chandelier » à 4 ATR
   sous le plus haut, passage au point mort après +1,5 R, et sortie si la
   tendance se casse (EMA21 < EMA50 et clôture < EMA50).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ta
from .models import Position


@dataclass
class StrategyConfig:
    ema_fast: int = 21
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    rsi_pullback: float = 40.0
    rsi_max_entry: float = 80.0
    atr_period: int = 14
    adx_period: int = 14
    adx_min: float = 20.0
    breakout_period: int = 20
    volume_period: int = 20
    volume_mult: float = 1.5
    max_atr_pct: float = 0.12
    stop_atr_mult: float = 2.5
    trail_atr_mult: float = 4.0
    breakeven_r: float = 1.5
    breakeven_buffer_pct: float = 0.003
    use_btc_filter: bool = True
    btc_ema: int = 200


def compute_indicators(
    df: pd.DataFrame, cfg: StrategyConfig, btc_df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Ajoute toutes les colonnes d'indicateurs au DataFrame OHLCV."""
    out = df.copy()
    close = out["close"]
    out["ema_fast"] = ta.ema(close, cfg.ema_fast)
    out["ema_mid"] = ta.ema(close, cfg.ema_mid)
    out["ema_slow"] = ta.ema(close, cfg.ema_slow)
    out["rsi"] = ta.rsi(close, cfg.rsi_period)
    out["atr"] = ta.atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / close
    out["adx"] = ta.adx(out, cfg.adx_period)
    # Plus haut des N bougies *précédentes* : la bougie courante doit le dépasser.
    out["donchian_high"] = ta.donchian_high(out, cfg.breakout_period).shift(1)
    out["vol_sma"] = ta.sma(out["volume"], cfg.volume_period)

    if btc_df is not None and len(btc_df):
        btc_close = btc_df["close"].sort_index()
        btc_ema = ta.ema(btc_close, cfg.btc_ema)
        out["btc_close"] = btc_close.reindex(out.index, method="ffill")
        out["btc_ema"] = btc_ema.reindex(out.index, method="ffill")
    else:
        out["btc_close"] = np.nan
        out["btc_ema"] = np.nan
    return out


def generate_signals(ind: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Signaux évalués à la clôture de chaque bougie."""
    close = ind["close"]
    has_btc = ind["btc_close"].notna() & ind["btc_ema"].notna()
    btc_bull = ind["btc_close"] > ind["btc_ema"]
    # Sans donnée BTC, le filtre est neutre (il ne bloque pas).
    btc_ok = (btc_bull | ~has_btc) if cfg.use_btc_filter else pd.Series(True, index=ind.index)

    regime = (close > ind["ema_slow"]) & (ind["ema_mid"] > ind["ema_slow"]) & btc_ok
    exit_regime = (close < ind["ema_mid"]) & (ind["ema_fast"] < ind["ema_mid"])

    breakout = (
        (close > ind["donchian_high"])
        & (ind["volume"] > cfg.volume_mult * ind["vol_sma"])
        & (ind["adx"] >= cfg.adx_min)
    )
    pullback = (
        (ind["rsi"].shift(1) < cfg.rsi_pullback)
        & (ind["rsi"] >= cfg.rsi_pullback)
        & (close > ind["open"])
    )
    filters = (
        (ind["rsi"] < cfg.rsi_max_entry)
        & (ind["atr_pct"] <= cfg.max_atr_pct)
        & ~exit_regime
    )
    entry = regime & (breakout | pullback) & filters
    reason = np.select([entry & breakout, entry & pullback], ["breakout", "pullback"], default="")

    return pd.DataFrame(
        {
            "regime_bull": regime,
            "btc_ok": btc_ok,
            "breakout": breakout,
            "pullback": pullback,
            "entry": entry,
            "entry_reason": reason,
            "exit_regime": exit_regime,
            "score": market_score(ind),
        },
        index=ind.index,
    )


def market_score(ind: pd.DataFrame) -> pd.Series:
    """Score de 0 à 100 résumant l'état du marché TAO (plus haut = plus haussier)."""
    close = ind["close"]
    rsi_ = ind["rsi"]
    score = pd.Series(0.0, index=ind.index)
    # Tendance (30 pts)
    score += 10 * (close > ind["ema_slow"])
    score += 10 * (ind["ema_mid"] > ind["ema_slow"])
    score += 5 * (close > ind["ema_mid"])
    score += 5 * (ind["ema_fast"] > ind["ema_mid"])
    # Momentum (25 pts) : zone idéale 50-70, acceptable 40-50 ou 70-80
    score += 25 * rsi_.between(50, 70)
    score += 12 * (rsi_.between(40, 50, inclusive="left") | rsi_.between(70, 80, inclusive="right"))
    # Force de tendance (15 pts)
    score += np.where(ind["adx"] >= 25, 15, np.where(ind["adx"] >= 20, 8, 0))
    # Volume (10 pts)
    score += 10 * (ind["volume"] > ind["vol_sma"])
    # Contexte BTC (20 pts, 10 si inconnu)
    has_btc = ind["btc_close"].notna() & ind["btc_ema"].notna()
    score += np.where(has_btc, 20 * (ind["btc_close"] > ind["btc_ema"]), 10)
    return score


def score_label(score: float) -> str:
    if score >= 70:
        return "Haussier fort"
    if score >= 50:
        return "Haussier modéré"
    if score >= 30:
        return "Neutre"
    return "Baissier"


def initial_stop(entry_price: float, atr: float, cfg: StrategyConfig) -> float:
    return entry_price - cfg.stop_atr_mult * atr


def trailing_stop(pos: Position, atr: float, cfg: StrategyConfig) -> float:
    """Nouveau stop : chandelier sous le plus haut + point mort. Ne descend jamais."""
    stop = pos.stop
    if math.isfinite(atr):
        stop = max(stop, pos.highest - cfg.trail_atr_mult * atr)
    risk = pos.risk_per_unit
    if risk > 0 and pos.highest >= pos.entry_price + cfg.breakeven_r * risk:
        stop = max(stop, pos.entry_price * (1 + cfg.breakeven_buffer_pct))
    return stop
