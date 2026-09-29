"""Données de marché : téléchargement via ccxt, CSV, et données synthétiques."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]
_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    """'4h' -> 14400."""
    try:
        return int(timeframe[:-1]) * _UNITS[timeframe[-1]]
    except (KeyError, ValueError):
        raise ValueError(f"Timeframe invalide : {timeframe!r} (ex. 15m, 1h, 4h, 1d)") from None


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(seconds=timeframe_seconds(timeframe))


def ohlcv_to_df(rows: list[list[float]]) -> pd.DataFrame:
    """Convertit la sortie de ``ccxt.fetch_ohlcv`` en DataFrame indexé par date UTC."""
    df = pd.DataFrame(rows, columns=["timestamp", *COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def drop_unclosed(df: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    """Retire la bougie en cours : on ne décide que sur des bougies clôturées."""
    return df[df.index + timeframe_delta(timeframe) <= now]


def fetch_history(
    exchange, symbol: str, timeframe: str, since: pd.Timestamp, until: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Télécharge l'historique complet entre ``since`` et ``until`` (pagination)."""
    until = until or pd.Timestamp.now(tz="UTC")
    step_ms = timeframe_seconds(timeframe) * 1000
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)
    rows: list[list[float]] = []
    while since_ms < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1][0]
        if last + step_ms <= since_ms:
            break  # l'exchange ne renvoie plus rien de nouveau
        since_ms = last + step_ms
        log.debug("%s : %d bougies téléchargées", symbol, len(rows))
    if not rows:
        return pd.DataFrame(columns=COLUMNS, dtype=float)
    df = ohlcv_to_df(rows)
    return df[df.index <= until]


def load_csv(path: str | Path) -> pd.DataFrame:
    """Charge un CSV avec une colonne ``timestamp`` (ms ou ISO) + OHLCV."""
    df = pd.read_csv(path)
    df.columns = [c.lower().strip() for c in df.columns]
    missing = {"timestamp", *COLUMNS} - set(df.columns)
    if missing:
        raise ValueError(f"{path} : colonne(s) manquante(s) {sorted(missing)}")
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        df["timestamp"] = pd.to_datetime(ts, unit="ms", utc=True)
    else:
        df["timestamp"] = pd.to_datetime(ts, utc=True)
    df = df.set_index("timestamp")[COLUMNS].astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


def save_csv(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="timestamp")


def _regime_returns(rng: np.random.Generator, n_bars: int, timeframe: str) -> np.ndarray:
    """Rendements log alternant régimes haussier / baissier / range, queues épaisses."""
    scale = np.sqrt(timeframe_seconds(timeframe) / 14400)  # calibré pour du 4h
    drifts = np.array([0.0015, -0.0015, 0.0]) * scale**2
    vols = np.array([0.022, 0.028, 0.016]) * scale
    regime = 2
    regimes = np.empty(n_bars, dtype=int)
    for i in range(n_bars):
        if rng.random() > 0.995:
            regime = int(rng.integers(0, 3))
        regimes[i] = regime
    shocks = rng.standard_t(df=4, size=n_bars) / np.sqrt(2.0)
    return drifts[regimes] + vols[regimes] * shocks


def _ohlcv_from_returns(
    rng: np.random.Generator, rets: np.ndarray, start_price: float, index: pd.DatetimeIndex
) -> pd.DataFrame:
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rets).mean()
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, wick, len(rets))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, wick, len(rets))))
    volume = 20_000 * np.exp(rng.normal(0, 0.4, len(rets))) * (1 + 25 * np.abs(rets))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )


def generate_synthetic_market(
    n_bars: int = 3000,
    timeframe: str = "4h",
    seed: int = 42,
    start: str = "2023-01-01",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Marché fictif « façon TAO » + BTC corrélé : (tao_df, btc_df).

    Le TAO suit le même cycle que le BTC avec environ deux fois plus de
    volatilité. Sert à tester le bot hors ligne ; ce n'est PAS une prévision.
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n_bars, freq=timeframe_delta(timeframe), tz="UTC", name="timestamp")
    cycle = _regime_returns(rng, n_bars, timeframe)
    noise = np.sqrt(timeframe_seconds(timeframe) / 14400)
    btc_rets = 0.5 * cycle + rng.normal(0, 0.005 * noise, n_bars)
    tao_rets = cycle + rng.normal(0, 0.012 * noise, n_bars)
    return (
        _ohlcv_from_returns(rng, tao_rets, 300.0, index),
        _ohlcv_from_returns(rng, btc_rets, 60_000.0, index),
    )


def generate_synthetic_ohlcv(
    n_bars: int = 3000,
    timeframe: str = "4h",
    seed: int = 42,
    start: str = "2023-01-01",
) -> pd.DataFrame:
    """Série TAO fictive seule (voir ``generate_synthetic_market``)."""
    return generate_synthetic_market(n_bars, timeframe, seed, start)[0]
