import numpy as np
import pandas as pd

from tao_bot import indicators as ta
from tao_bot.data import generate_synthetic_ohlcv


def test_ema_matches_pandas():
    s = pd.Series(np.arange(1, 51, dtype=float))
    expected = s.ewm(span=10, adjust=False).mean()
    out = ta.ema(s, 10)
    assert out.iloc[:9].isna().all()
    np.testing.assert_allclose(out.iloc[9:], expected.iloc[9:])


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ta.rsi(up, 14).dropna().eq(100).all()
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ta.rsi(down, 14).dropna().eq(0).all()
    flat = pd.Series(np.full(40, 5.0))
    assert ta.rsi(flat, 14).dropna().eq(50).all()
    df = generate_synthetic_ohlcv(500, seed=1)
    r = ta.rsi(df["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_adx_donchian():
    df = generate_synthetic_ohlcv(500, seed=2)
    atr = ta.atr(df).dropna()
    assert (atr > 0).all()
    adx = ta.adx(df).dropna()
    assert ((adx >= 0) & (adx <= 100)).all()
    dh = ta.donchian_high(df, 20)
    assert dh.iloc[25] == df["high"].iloc[6:26].max()


def test_true_range_uses_previous_close():
    df = pd.DataFrame({"high": [10.0, 12.0], "low": [9.0, 11.0], "close": [9.5, 11.5]})
    # Gap haussier : TR = high - close précédente = 12 - 9.5
    assert ta.true_range(df).iloc[1] == 2.5
