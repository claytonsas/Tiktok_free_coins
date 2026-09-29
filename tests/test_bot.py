import pandas as pd
import pytest

from tao_bot.analysis import build_analysis
from tao_bot.bot import BotState, TradingBot
from tao_bot.config import Config
from tao_bot.data import generate_synthetic_market, timeframe_delta
from tao_bot.exchange import PaperBroker
from tao_bot.strategy import compute_indicators, generate_signals


class FakeMarket:
    """Rejoue un historique bougie par bougie, comme le ferait l'exchange."""

    def __init__(self, df, btc):
        self.df, self.btc = df, btc
        self.cursor = 0  # index de la bougie en cours (non clôturée)

    def fetch_ohlcv(self, symbol, timeframe, limit=500):
        data = self.btc if symbol.startswith("BTC") else self.df
        return data.iloc[max(0, self.cursor + 1 - limit) : self.cursor + 1]

    def last_price(self, symbol):
        return float(self.df["open"].iloc[self.cursor])

    def now(self):
        return self.df.index[self.cursor] + pd.Timedelta(minutes=1)


def _bot(tmp_path, market, cfg=None, state=None):
    cfg = cfg or Config()
    state = state or BotState()
    paper = state.paper or {"quote": 1000.0, "base": 0.0}
    broker = PaperBroker(cfg.exchange.fee_rate, cfg.exchange.slippage, paper["quote"], paper["base"])
    return TradingBot(cfg, market, broker, state, tmp_path / "state.json", tmp_path / "trades.csv")


def test_paper_bot_trades_and_accounts_correctly(tmp_path):
    df, btc = generate_synthetic_market(1500, seed=2)
    market = FakeMarket(df, btc)
    bot = _bot(tmp_path, market)
    for cursor in range(250, len(df)):
        market.cursor = cursor
        bot.step(now=market.now())

    trades = pd.read_csv(tmp_path / "trades.csv")
    assert len(trades) > 0
    price = market.last_price("TAO/USDT")
    equity = bot.broker.equity("TAO/USDT", price)
    if bot.state.position is None:
        # Sans position ouverte, le capital = 1000 + somme des PnL enregistrés.
        assert equity == pytest.approx(1000 + trades["pnl"].sum(), rel=1e-9)
    # Le bot ne décide que sur des bougies clôturées.
    assert pd.Timestamp(bot.state.last_candle) + timeframe_delta("4h") <= market.now()


def test_state_survives_restart(tmp_path):
    df, btc = generate_synthetic_market(1500, seed=2)
    market = FakeMarket(df, btc)
    bot = _bot(tmp_path, market)
    for cursor in range(250, 1500):
        market.cursor = cursor
        bot.step(now=market.now())
        if bot.state.position is not None:
            break
    assert bot.state.position is not None

    reloaded = BotState.load(tmp_path / "state.json")
    assert reloaded.position == bot.state.position
    assert reloaded.last_candle == bot.state.last_candle
    assert reloaded.paper == pytest.approx(bot.broker.snapshot())
    bot2 = _bot(tmp_path, market, state=reloaded)
    assert bot2.broker.base == pytest.approx(bot.broker.base)


def test_same_candle_is_processed_once(tmp_path):
    df, btc = generate_synthetic_market(600, seed=4)
    market = FakeMarket(df, btc)
    bot = _bot(tmp_path, market)
    market.cursor = 400
    bot.step(now=market.now())
    cooldown_before = bot.risk.state.to_dict()
    last = bot.state.last_candle
    bot.step(now=market.now() + pd.Timedelta(minutes=5))
    assert bot.state.last_candle == last
    assert bot.risk.state.to_dict() == cooldown_before


def test_stop_is_checked_on_live_price(tmp_path):
    df, btc = generate_synthetic_market(1500, seed=2)
    market = FakeMarket(df, btc)
    bot = _bot(tmp_path, market)
    for cursor in range(250, 1500):
        market.cursor = cursor
        bot.step(now=market.now())
        if bot.state.position is not None:
            break
    pos = bot.state.position
    crash = df.copy()
    crash.iloc[market.cursor, crash.columns.get_loc("open")] = pos.stop * 0.9
    market.df = crash
    bot.step(now=market.now() + pd.Timedelta(minutes=1))
    assert bot.state.position is None
    assert pd.read_csv(tmp_path / "trades.csv")["exit_reason"].iloc[-1] == "stop_initial"


def test_analysis_report():
    df, btc = generate_synthetic_market(600, seed=7)
    cfg = Config()
    ind = compute_indicators(df, cfg.strategy, btc)
    report = build_analysis(ind, generate_signals(ind, cfg.strategy), cfg, 1000)
    assert "Analyse TAO/USDT" in report
    assert "Score marché" in report
    assert "Signal" in report
