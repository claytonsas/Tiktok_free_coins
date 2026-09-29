import numpy as np
import pandas as pd

from tao_bot.data import generate_synthetic_market
from tao_bot.models import Position
from tao_bot.strategy import (
    StrategyConfig,
    compute_indicators,
    generate_signals,
    initial_stop,
    trailing_stop,
)


def _signals(df, btc, cfg):
    return generate_signals(compute_indicators(df, cfg, btc), cfg)


def test_no_lookahead_bias():
    """Un signal passé ne doit jamais changer quand de nouvelles bougies arrivent."""
    cfg = StrategyConfig()
    df, btc = generate_synthetic_market(1200, seed=3)
    full = _signals(df, btc, cfg)
    cut = 900
    partial = _signals(df.iloc[:cut], btc.iloc[:cut], cfg)
    cols = ["entry", "entry_reason", "exit_regime", "regime_bull", "score"]
    pd.testing.assert_frame_equal(full[cols].iloc[:cut], partial[cols])


def test_entries_only_in_bull_regime_and_have_reason():
    cfg = StrategyConfig()
    df, btc = generate_synthetic_market(3000, seed=2)
    sig = _signals(df, btc, cfg)
    entries = sig[sig["entry"]]
    assert len(entries) > 0
    assert entries["regime_bull"].all()
    assert not entries["exit_regime"].any()
    assert set(entries["entry_reason"]) <= {"breakout", "pullback"}
    assert (sig.loc[~sig["entry"], "entry_reason"] == "").all()


def test_btc_filter_blocks_entries_when_btc_bearish():
    cfg = StrategyConfig()
    df, btc = generate_synthetic_market(2000, seed=5)
    falling = 60_000 * np.exp(-0.002 * np.arange(len(btc)))
    bearish_btc = pd.DataFrame(
        {"open": falling, "high": falling, "low": falling, "close": falling, "volume": 1.0}, index=btc.index
    )
    sig = _signals(df, bearish_btc, cfg)
    assert not sig["entry"].iloc[300:].any()
    cfg_off = StrategyConfig(use_btc_filter=False)
    assert _signals(df, bearish_btc, cfg_off)["entry"].iloc[300:].any()


def test_score_range():
    cfg = StrategyConfig()
    df, btc = generate_synthetic_market(800, seed=4)
    score = _signals(df, btc, cfg)["score"]
    assert score.between(0, 100).all()


def _pos(entry=100.0, stop=90.0, highest=100.0):
    return Position(entry, 1.0, stop, stop, highest, "2024-01-01T00:00:00+00:00")


def test_trailing_stop_only_moves_up():
    cfg = StrategyConfig(trail_atr_mult=3.0, breakeven_r=100)
    pos = _pos(highest=130.0)
    stop = trailing_stop(pos, atr=5.0, cfg=cfg)
    assert stop == 115.0
    pos.stop = stop
    pos.highest = 131.0
    assert trailing_stop(pos, atr=20.0, cfg=cfg) == 115.0  # ATR plus large : le stop ne recule pas
    assert trailing_stop(pos, atr=float("nan"), cfg=cfg) == 115.0


def test_breakeven_after_r_multiple():
    cfg = StrategyConfig(trail_atr_mult=100, breakeven_r=1.5, breakeven_buffer_pct=0.003)
    pos = _pos(entry=100.0, stop=90.0, highest=114.0)
    assert trailing_stop(pos, atr=5.0, cfg=cfg) == 90.0
    pos.highest = 115.0
    assert trailing_stop(pos, atr=5.0, cfg=cfg) == 100.0 * 1.003


def test_initial_stop():
    assert initial_stop(100.0, 4.0, StrategyConfig(stop_atr_mult=2.5)) == 90.0
