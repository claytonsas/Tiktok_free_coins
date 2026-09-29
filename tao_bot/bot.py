"""Boucle de trading en temps réel (mode papier ou réel).

À chaque passage (toutes les ``poll_seconds``) :
1. le stop est vérifié sur le dernier prix ;
2. si une nouvelle bougie vient de clôturer : mise à jour du stop suiveur,
   contrôle du risque, puis sortie ou entrée selon la stratégie.
L'état (position, stop, compteurs de risque, solde papier) est sauvegardé
dans un fichier JSON : le bot peut être arrêté et relancé sans rien perdre.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import Config
from .data import drop_unclosed, timeframe_delta
from .exchange import Fill
from .models import Position, Trade, close_trade, stop_reason
from .notifier import Notifier
from .risk import RiskManager, RiskState, position_size
from .strategy import compute_indicators, generate_signals, initial_stop, trailing_stop

log = logging.getLogger(__name__)


@dataclass
class BotState:
    position: Position | None = None
    risk: RiskState = field(default_factory=RiskState)
    last_candle: str | None = None
    paper: dict | None = None

    @classmethod
    def load(cls, path: Path) -> "BotState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        pos = data.get("position")
        return cls(
            position=Position.from_dict(pos) if pos else None,
            risk=RiskState.from_dict(data.get("risk", {})),
            last_candle=data.get("last_candle"),
            paper=data.get("paper"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "position": self.position.to_dict() if self.position else None,
            "risk": self.risk.to_dict(),
            "last_candle": self.last_candle,
            "paper": self.paper,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)  # écriture atomique


class TradingBot:
    def __init__(
        self,
        cfg: Config,
        market,
        broker,
        state: BotState,
        state_path: Path,
        trades_path: Path,
        notifier: Notifier | None = None,
        mode: str = "paper",
    ):
        self.cfg = cfg
        self.market = market
        self.broker = broker
        self.state = state
        self.state_path = state_path
        self.trades_path = trades_path
        self.notifier = notifier or Notifier(token="", chat_id="")
        self.mode = mode
        self.risk = RiskManager(cfg.risk, state.risk)
        self._last_error = ""

    # ------------------------------------------------------------------ boucle
    def run(self, once: bool = False) -> None:
        ex = self.cfg.exchange
        self.notifier.send(
            f"🤖 Bot TAO démarré — mode {self.mode.upper()} — {ex.id} {ex.symbol} {ex.timeframe}"
        )
        while True:
            try:
                self.step()
                self._last_error = ""
            except Exception as exc:  # erreur réseau, API... : on log et on réessaie
                log.exception("Erreur pendant le cycle")
                msg = f"⚠️ Erreur bot TAO : {exc}"
                if msg != self._last_error:
                    self.notifier.send(msg)
                    self._last_error = msg
            if once:
                return
            time.sleep(self.cfg.bot.poll_seconds)

    def step(self, now: pd.Timestamp | None = None) -> None:
        now = now or pd.Timestamp.now(tz="UTC")
        ex = self.cfg.exchange
        df = drop_unclosed(
            self.market.fetch_ohlcv(ex.symbol, ex.timeframe, limit=self.cfg.bot.warmup_bars),
            ex.timeframe,
            now,
        )
        price = self.market.last_price(ex.symbol)

        pos = self.state.position
        if pos is not None and price <= pos.stop:
            self._close(price, stop_reason(pos), now)

        if not df.empty and self.state.last_candle != df.index[-1].isoformat():
            self._on_new_candle(df, price, now)
            self.state.last_candle = df.index[-1].isoformat()
        self._save()

    # ------------------------------------------------------- nouvelle bougie
    def _on_new_candle(self, df: pd.DataFrame, price: float, now: pd.Timestamp) -> None:
        s, ex = self.cfg.strategy, self.cfg.exchange
        btc = None
        if s.use_btc_filter:
            try:
                btc = self.market.fetch_ohlcv(ex.btc_symbol, ex.timeframe, limit=self.cfg.bot.warmup_bars)
                btc = drop_unclosed(btc, ex.timeframe, now)
            except Exception as exc:
                log.warning("Données BTC indisponibles, filtre BTC ignoré : %s", exc)
        ind = compute_indicators(df, s, btc)
        sig = generate_signals(ind, s)
        row, srow = ind.iloc[-1], sig.iloc[-1]
        ts = ind.index[-1]
        log.info(
            "Bougie %s | clôture %.2f | RSI %.1f | ADX %.1f | score %d/100",
            ts, row["close"], row["rsi"], row["adx"], srow["score"],
        )

        pos = self.state.position
        if pos is not None:
            entry_ts = pd.Timestamp(pos.entry_time)
            since_entry = df.index + timeframe_delta(ex.timeframe) > entry_ts
            if since_entry.any():
                pos.highest = max(pos.highest, float(df.loc[since_entry, "high"].max()))
            new_stop = trailing_stop(pos, float(row["atr"]), s)
            if new_stop > pos.stop:
                log.info("Stop relevé : %.2f -> %.2f", pos.stop, new_stop)
                pos.stop = new_stop

        equity = self.broker.equity(ex.symbol, price)
        was_killed = self.risk.state.kill_switch
        self.risk.on_bar(ts, equity)
        if self.risk.state.kill_switch and not was_killed:
            self.notifier.send(
                f"🛑 KILL SWITCH : drawdown {100 * self.risk.drawdown(equity):.1f} %. "
                "Plus aucune nouvelle position (relancer avec --reset-kill-switch)."
            )

        if pos is not None:
            if self.risk.state.kill_switch:
                self._close(price, "kill_switch", now)
            elif bool(srow["exit_regime"]):
                self._close(price, "tendance_cassee", now)
            elif price <= pos.stop:
                self._close(price, stop_reason(pos), now)
        elif bool(srow["entry"]):
            ok, why = self.risk.can_open(equity)
            if ok:
                self._open(price, float(row["atr"]), str(srow["entry_reason"]), equity, now)
            else:
                log.info("Signal d'achat ignoré : %s", why)
        self.risk.end_bar()

    # ---------------------------------------------------------------- ordres
    def _open(self, price: float, atr: float, reason: str, equity: float, now: pd.Timestamp) -> None:
        s, ex = self.cfg.strategy, self.cfg.exchange
        stop = initial_stop(price, atr, s)
        amount = position_size(equity, price, stop, self.cfg.risk)
        if amount * price < ex.min_notional:
            log.info("Position trop petite (%.2f < minimum %.2f) : ordre annulé", amount * price, ex.min_notional)
            return
        fill: Fill = self.broker.buy(ex.symbol, amount, price)
        if fill.amount <= 0:
            log.warning("Achat non exécuté")
            return
        stop = initial_stop(fill.price, atr, s)
        self.state.position = Position(
            entry_price=fill.price,
            amount=fill.amount,
            stop=stop,
            initial_stop=stop,
            highest=fill.price,
            entry_time=now.isoformat(),
            reason=reason,
            entry_fee=fill.fee,
        )
        self.notifier.send(
            f"🟢 ACHAT {fill.amount:.4f} TAO à {fill.price:.2f} ({reason})\n"
            f"Stop : {stop:.2f} ({100 * (stop / fill.price - 1):.1f} %) — "
            f"risque {100 * self.cfg.risk.risk_per_trade:.1f} % du capital"
        )
        self._save()

    def _close(self, price: float, reason: str, now: pd.Timestamp) -> None:
        pos = self.state.position
        if pos is None:
            return
        fill = self.broker.sell(self.cfg.exchange.symbol, pos.amount, price)
        trade = close_trade(pos, fill.price, fill.amount, fill.fee, now.isoformat(), reason)
        self.state.position = None
        self.risk.on_trade_closed(trade.pnl)
        self._log_trade(trade)
        emoji = "✅" if trade.pnl > 0 else "🔴"
        self.notifier.send(
            f"{emoji} VENTE {fill.amount:.4f} TAO à {fill.price:.2f} ({reason})\n"
            f"Résultat : {trade.pnl:+.2f} ({100 * trade.pnl_pct:+.2f} %, {trade.r_multiple:+.2f} R)"
        )
        self._save()

    # ------------------------------------------------------------ persistance
    def _save(self) -> None:
        self.state.risk = self.risk.state
        self.state.paper = self.broker.snapshot()
        self.state.save(self.state_path)

    def _log_trade(self, trade: Trade) -> None:
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        row = trade.to_dict()
        new_file = not self.trades_path.exists()
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row))
            if new_file:
                writer.writeheader()
            writer.writerow(row)
