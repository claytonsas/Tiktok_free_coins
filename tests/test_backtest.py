import math

import pytest

from tao_bot.backtest import format_report, run_backtest, stop_fill_price
from tao_bot.config import Config
from tao_bot.data import generate_synthetic_market


@pytest.fixture(scope="module")
def market():
    return generate_synthetic_market(3000, seed=2)


def test_backtest_runs_and_pnl_is_consistent(market):
    df, btc = market
    cfg = Config()
    res = run_backtest(df, cfg, btc, initial_capital=1000)
    m = res.metrics
    assert m["nb_trades"] > 0
    assert len(res.equity) == len(df)
    assert all(math.isfinite(v) for v in res.equity)
    # Toutes les positions sont clôturées à la fin : somme des PnL = variation du capital.
    assert sum(t.pnl for t in res.trades) == pytest.approx(m["capital_final"] - 1000, rel=1e-9)
    assert m["frais_totaux"] > 0
    assert -1 <= m["drawdown_max"] <= 0
    assert "Rendement total" in format_report(m)


def test_trades_are_chronological_and_never_overlap(market):
    df, btc = market
    res = run_backtest(df, Config(), btc)
    for t in res.trades:
        assert t.entry_time <= t.exit_time
    for prev, nxt in zip(res.trades, res.trades[1:]):
        assert prev.exit_time <= nxt.entry_time


def test_no_fees_means_no_fees(market):
    df, btc = market
    cfg = Config()
    cfg.exchange.fee_rate = 0.0
    cfg.exchange.slippage = 0.0
    res = run_backtest(df, cfg, btc)
    assert res.metrics["frais_totaux"] == 0
    assert all(t.fees == 0 for t in res.trades)


def test_risk_per_trade_is_respected(market):
    df, btc = market
    cfg = Config()
    res = run_backtest(df, cfg, btc)
    # Hors gap, une perte au stop initial coûte environ 1 R (frais et glissement en plus).
    for t in res.trades:
        if t.exit_reason == "stop_initial":
            assert -1.3 < t.r_multiple < -0.9


def test_stop_fill_price_handles_gaps():
    assert stop_fill_price(open_=100.0, stop=95.0) == 95.0  # stop touché en cours de bougie
    assert stop_fill_price(open_=90.0, stop=95.0) == 90.0  # ouverture sous le stop


def test_short_history_does_not_trade():
    df, btc = generate_synthetic_market(150, seed=1)
    res = run_backtest(df, Config(), btc)
    assert res.metrics["nb_trades"] == 0
    assert res.metrics["capital_final"] == pytest.approx(Config().backtest.initial_capital)
