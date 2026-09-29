"""Rapport d'analyse lisible de la situation actuelle du TAO."""
from __future__ import annotations

import math

import pandas as pd

from .config import Config
from .risk import position_size
from .strategy import initial_stop, score_label


def _fmt(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}".replace(",", " ") if math.isfinite(x) else "n/a"


def build_analysis(ind: pd.DataFrame, sig: pd.DataFrame, cfg: Config, capital: float) -> str:
    s, ex = cfg.strategy, cfg.exchange
    row, srow = ind.iloc[-1], sig.iloc[-1]
    quote = ex.symbol.split("/")[1]
    close = float(row["close"])

    if srow["regime_bull"]:
        trend = "HAUSSIÈRE (prix > EMA200 et EMA50 > EMA200)"
    elif close > row["ema_slow"]:
        trend = "INDÉCISE (prix > EMA200 mais EMA50 < EMA200)"
    else:
        trend = "BAISSIÈRE (prix < EMA200)"

    adx = float(row["adx"])
    adx_txt = "tendance forte" if adx >= 25 else "tendance faible" if adx >= s.adx_min else "pas de tendance"
    vol_ratio = row["volume"] / row["vol_sma"] if row["vol_sma"] else float("nan")

    if pd.notna(row["btc_close"]) and pd.notna(row["btc_ema"]):
        btc_bull = row["btc_close"] > row["btc_ema"]
        btc_txt = (
            f"{'OK' if btc_bull else 'DÉFAVORABLE'} "
            f"(BTC {_fmt(row['btc_close'], 0)} {'>' if btc_bull else '<'} EMA{s.btc_ema} {_fmt(row['btc_ema'], 0)})"
        )
    else:
        btc_txt = "indisponible (filtre neutre)"

    if srow["entry"]:
        signal = f"ACHAT ({srow['entry_reason']})"
    elif srow["exit_regime"]:
        signal = "SORTIE / rester à l'écart (tendance cassée)"
    else:
        signal = "AUCUN — attendre"

    lines = [
        f"=== Analyse {ex.symbol} ({ex.id}, {ex.timeframe}) — bougie du {ind.index[-1]:%Y-%m-%d %H:%M} UTC ===",
        f"Prix           : {_fmt(close)} {quote}",
        f"Tendance       : {trend}",
        f"EMA {s.ema_fast}/{s.ema_mid}/{s.ema_slow} : {_fmt(row['ema_fast'])} / {_fmt(row['ema_mid'])} / {_fmt(row['ema_slow'])}",
        f"RSI({s.rsi_period})        : {_fmt(row['rsi'], 1)}",
        f"ADX({s.adx_period})        : {_fmt(adx, 1)} ({adx_txt})",
        f"ATR({s.atr_period})        : {_fmt(row['atr'])} ({100 * row['atr_pct']:.1f} % du prix par bougie)",
        f"Volume         : {_fmt(vol_ratio, 2)}x la moyenne {s.volume_period} bougies",
        f"Filtre BTC     : {btc_txt}",
        f"Score marché   : {int(srow['score'])}/100 → {score_label(srow['score'])}",
        f"Signal         : {signal}",
    ]
    stop = initial_stop(close, float(row["atr"]), s)
    amount = position_size(capital, close, stop, cfg.risk)
    if amount > 0:
        lines.append(
            f"Plan si achat  : stop {_fmt(stop)} ({100 * (stop / close - 1):.1f} %), "
            f"taille {amount:.4f} TAO ≈ {_fmt(amount * close)} {quote} "
            f"pour {_fmt(capital, 0)} {quote} de capital ({100 * cfg.risk.risk_per_trade:.1f} % de risque)"
        )
    lines.append("Ceci n'est pas un conseil financier.")
    return "\n".join(lines)
