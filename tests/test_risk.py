import pandas as pd
import pytest

from tao_bot.risk import RiskConfig, RiskManager, position_size


def test_position_size_risks_fixed_fraction():
    cfg = RiskConfig(risk_per_trade=0.01, max_position_pct=1.0)
    amount = position_size(10_000, entry=100.0, stop=90.0, cfg=cfg)
    assert amount == pytest.approx(10.0)  # perte au stop = 10 x 10 = 100 = 1 %


def test_position_size_is_capped_and_rejects_bad_stops():
    cfg = RiskConfig(risk_per_trade=0.05, max_position_pct=0.5)
    assert position_size(1000, 100.0, 99.0, cfg) == pytest.approx(5.0)  # plafond 50 %
    assert position_size(1000, 100.0, 100.0, cfg) == 0
    assert position_size(1000, 100.0, -5.0, cfg) == 0
    assert position_size(0, 100.0, 90.0, cfg) == 0


def test_kill_switch_on_max_drawdown():
    rm = RiskManager(RiskConfig(max_drawdown=0.2))
    t = pd.Timestamp("2024-01-01", tz="UTC")
    rm.on_bar(t, 1000)
    rm.on_bar(t, 1200)
    rm.on_bar(t, 961)
    assert not rm.state.kill_switch
    rm.on_bar(t, 960)
    assert rm.state.kill_switch
    assert rm.can_open(2000)[0] is False
    rm.reset_kill_switch(960)
    assert rm.can_open(960)[0] is True


def test_daily_loss_limit_resets_next_day():
    rm = RiskManager(RiskConfig(max_daily_loss=0.05, max_drawdown=0.9))
    rm.on_bar(pd.Timestamp("2024-01-01 00:00", tz="UTC"), 1000)
    rm.on_bar(pd.Timestamp("2024-01-01 08:00", tz="UTC"), 940)
    assert rm.can_open(940)[0] is False
    rm.on_bar(pd.Timestamp("2024-01-02 00:00", tz="UTC"), 940)
    assert rm.can_open(940)[0] is True


def test_cooldown_after_loss():
    rm = RiskManager(RiskConfig(cooldown_bars=2))
    rm.on_trade_closed(+10)
    assert rm.can_open(1000)[0]
    rm.on_trade_closed(-10)
    assert not rm.can_open(1000)[0]
    rm.end_bar()
    assert not rm.can_open(1000)[0]
    rm.end_bar()
    assert rm.can_open(1000)[0]
