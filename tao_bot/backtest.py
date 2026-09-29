"""Backtest bougie par bougie, sans biais d'anticipation.

Règles d'exécution (identiques à celles du bot) :
- les signaux sont calculés à la clôture de la bougie ``i`` ;
- les ordres correspondants sont exécutés à l'ouverture de la bougie ``i+1`` ;
- le stop est vérifié en cours de bougie avec le plus bas ; si le marché
  ouvre sous le stop (gap), la sortie se fait au prix d'ouverture ;
- frais et glissement sont appliqués à chaque ordre.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .models import Position, Trade, close_trade, stop_reason
from .risk import RiskManager, position_size
from .strategy import compute_indicators, generate_signals, initial_stop, trailing_stop

SECONDS_PER_YEAR = 365.25 * 86400


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    metrics: dict
    indicators: pd.DataFrame
    signals: pd.DataFrame

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.to_dict() for t in self.trades])


def stop_fill_price(open_: float, stop: float) -> float:
    """Prix de sortie sur stop : le stop, ou l'ouverture si elle est déjà en dessous."""
    return min(open_, stop)


def run_backtest(
    df: pd.DataFrame,
    cfg: Config,
    btc_df: pd.DataFrame | None = None,
    initial_capital: float | None = None,
) -> BacktestResult:
    s, ex = cfg.strategy, cfg.exchange
    capital = float(initial_capital if initial_capital is not None else cfg.backtest.initial_capital)
    ind = compute_indicators(df, s, btc_df)
    sig = generate_signals(ind, s)

    index = ind.index
    o, h, l, c, atr = (ind[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close", "atr"))
    entry_sig = sig["entry"].to_numpy(dtype=bool)
    entry_reason = sig["entry_reason"].to_numpy()
    exit_sig = sig["exit_regime"].to_numpy(dtype=bool)

    risk = RiskManager(cfg.risk)
    cash = capital
    pos: Position | None = None
    pending: tuple[str, str] | None = None  # ("enter"|"exit", raison)
    trades: list[Trade] = []
    equity = np.empty(len(ind))
    in_market = np.zeros(len(ind), dtype=bool)
    total_fees = 0.0

    def sell(price: float, when: str, reason: str) -> None:
        nonlocal cash, pos, total_fees
        notional = pos.amount * price
        fee = notional * ex.fee_rate
        trade = close_trade(pos, price, pos.amount, fee, when, reason)
        trades.append(trade)
        cash += notional - fee
        total_fees += fee
        risk.on_trade_closed(trade.pnl)
        pos = None

    for i in range(len(ind)):
        when = index[i].isoformat()

        # 1) Ordres décidés à la clôture précédente, exécutés à l'ouverture.
        if pending is not None:
            action, reason = pending
            pending = None
            if action == "enter" and pos is None:
                fill = o[i] * (1 + ex.slippage)
                stop = initial_stop(fill, atr[i - 1], s)
                amount = position_size(cash, fill, stop, cfg.risk)
                amount = min(amount, cash / (fill * (1 + ex.fee_rate)))
                notional = amount * fill
                if notional >= ex.min_notional:
                    fee = notional * ex.fee_rate
                    cash -= notional + fee
                    total_fees += fee
                    pos = Position(
                        entry_price=fill,
                        amount=amount,
                        stop=stop,
                        initial_stop=stop,
                        highest=fill,
                        entry_time=when,
                        reason=reason,
                        entry_fee=fee,
                    )
            elif action == "exit" and pos is not None:
                sell(o[i] * (1 - ex.slippage), when, reason)

        # 2) Stop touché pendant la bougie.
        if pos is not None and l[i] <= pos.stop:
            sell(stop_fill_price(o[i], pos.stop) * (1 - ex.slippage), when, stop_reason(pos))

        # 3) Clôture : stop suiveur, valorisation, contrôle du risque.
        if pos is not None:
            in_market[i] = True
            pos.highest = max(pos.highest, h[i])
            pos.stop = trailing_stop(pos, atr[i], s)
        equity[i] = cash + (pos.amount * c[i] if pos is not None else 0.0)
        risk.on_bar(index[i], equity[i])

        # 4) Décisions pour la bougie suivante.
        if pos is not None:
            if risk.state.kill_switch:
                pending = ("exit", "kill_switch")
            elif exit_sig[i]:
                pending = ("exit", "tendance_cassee")
        elif entry_sig[i] and risk.can_open(equity[i])[0]:
            pending = ("enter", str(entry_reason[i]))
        risk.end_bar()

    # Position encore ouverte : clôturée au dernier prix pour les statistiques.
    if pos is not None and len(ind):
        sell(c[-1] * (1 - ex.slippage), index[-1].isoformat(), "fin_backtest")
        equity[-1] = cash

    equity_s = pd.Series(equity, index=index, name="equity")
    metrics = compute_metrics(equity_s, trades, capital, ind["close"], in_market, total_fees)
    metrics["kill_switch"] = risk.state.kill_switch
    return BacktestResult(trades=trades, equity=equity_s, metrics=metrics, indicators=ind, signals=sig)


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade],
    initial_capital: float,
    closes: pd.Series,
    in_market: np.ndarray,
    total_fees: float,
) -> dict:
    final = float(equity.iloc[-1]) if len(equity) else initial_capital
    drawdown = equity / equity.cummax() - 1
    rets = equity.pct_change().dropna()

    step = equity.index.to_series().diff().dt.total_seconds().median() if len(equity) > 1 else np.nan
    periods_per_year = SECONDS_PER_YEAR / step if step and step > 0 else np.nan
    std = rets.std()
    downside = rets[rets < 0].std()
    sharpe = rets.mean() / std * math.sqrt(periods_per_year) if std and std > 0 else 0.0
    sortino = rets.mean() / downside * math.sqrt(periods_per_year) if downside and downside > 0 else 0.0

    years = (equity.index[-1] - equity.index[0]).total_seconds() / SECONDS_PER_YEAR if len(equity) > 1 else 0
    cagr = (final / initial_capital) ** (1 / years) - 1 if years > 0 and final > 0 else float("nan")
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    gross_loss = -sum(losses)
    return {
        "capital_initial": initial_capital,
        "capital_final": final,
        "rendement_total": final / initial_capital - 1,
        "cagr": cagr,
        "buy_and_hold": float(closes.iloc[-1] / closes.iloc[0] - 1) if len(closes) else 0.0,
        "drawdown_max": max_dd,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": cagr / abs(max_dd) if max_dd < 0 and math.isfinite(cagr) else float("nan"),
        "nb_trades": len(trades),
        "taux_reussite": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": sum(wins) / gross_loss if gross_loss > 0 else float("inf"),
        "gain_moyen": float(np.mean(wins)) if wins else 0.0,
        "perte_moyenne": float(np.mean(losses)) if losses else 0.0,
        "r_moyen": float(np.mean([t.r_multiple for t in trades])) if trades else 0.0,
        "exposition": float(in_market.mean()) if len(in_market) else 0.0,
        "frais_totaux": total_fees,
    }


def format_report(metrics: dict, title: str = "Résultats du backtest") -> str:
    def pct(x: float) -> str:
        return f"{100 * x:+.2f} %" if math.isfinite(x) else "n/a"

    m = metrics
    lines = [
        f"=== {title} ===",
        f"Capital initial      : {m['capital_initial']:.2f}",
        f"Capital final        : {m['capital_final']:.2f}",
        f"Rendement total      : {pct(m['rendement_total'])}",
        f"Rendement annualisé  : {pct(m['cagr'])}",
        f"Buy & hold TAO       : {pct(m['buy_and_hold'])}",
        f"Drawdown max         : {pct(m['drawdown_max'])}",
        f"Sharpe / Sortino     : {m['sharpe']:.2f} / {m['sortino']:.2f}",
        f"Calmar               : {m['calmar']:.2f}",
        f"Nombre de trades     : {m['nb_trades']}",
        f"Taux de réussite     : {100 * m['taux_reussite']:.1f} %",
        f"Profit factor        : {m['profit_factor']:.2f}",
        f"Gain / perte moyens  : {m['gain_moyen']:.2f} / {m['perte_moyenne']:.2f}",
        f"R moyen par trade    : {m['r_moyen']:.2f}",
        f"Temps en position    : {100 * m['exposition']:.1f} %",
        f"Frais payés          : {m['frais_totaux']:.2f}",
    ]
    if m.get("kill_switch"):
        lines.append("ATTENTION : le kill switch (drawdown max) s'est déclenché.")
    return "\n".join(lines)
