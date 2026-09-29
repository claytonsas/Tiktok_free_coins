"""Gestion du risque : taille de position, drawdown max, perte journalière, pause."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01  # 1 % du capital perdu si le stop initial est touché
    max_position_pct: float = 0.95  # part max du capital investie dans une position
    max_drawdown: float = 0.25  # kill switch : on coupe tout à -25 % depuis le plus haut
    max_daily_loss: float = 0.05  # plus de nouvelle entrée après -5 % dans la journée
    cooldown_bars: int = 3  # bougies sans nouvelle entrée après un trade perdant


@dataclass
class RiskState:
    peak_equity: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    kill_switch: bool = False
    cooldown_remaining: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RiskState":
        return cls(**data)


def position_size(equity: float, entry: float, stop: float, cfg: RiskConfig) -> float:
    """Quantité à acheter pour ne risquer que ``risk_per_trade`` du capital.

    Plus le TAO est volatil (stop ATR large), plus la position est petite.
    """
    if equity <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        return 0.0
    amount = equity * cfg.risk_per_trade / (entry - stop)
    max_amount = equity * cfg.max_position_pct / entry
    return min(amount, max_amount)


class RiskManager:
    def __init__(self, cfg: RiskConfig, state: RiskState | None = None):
        self.cfg = cfg
        self.state = state or RiskState()

    def on_bar(self, ts: pd.Timestamp, equity: float) -> None:
        """À appeler à chaque clôture de bougie avec le capital valorisé."""
        st = self.state
        day = pd.Timestamp(ts).strftime("%Y-%m-%d")
        if day != st.day:
            st.day = day
            st.day_start_equity = equity
        st.peak_equity = max(st.peak_equity, equity)
        if (
            not st.kill_switch
            and st.peak_equity > 0
            and equity <= st.peak_equity * (1 - self.cfg.max_drawdown)
        ):
            st.kill_switch = True
            log.warning(
                "KILL SWITCH : drawdown de %.1f %% (max autorisé %.0f %%)",
                100 * self.drawdown(equity),
                100 * self.cfg.max_drawdown,
            )

    def end_bar(self) -> None:
        """À appeler après les décisions de la bougie : décompte de la pause."""
        if self.state.cooldown_remaining > 0:
            self.state.cooldown_remaining -= 1

    def drawdown(self, equity: float) -> float:
        peak = self.state.peak_equity
        return 1 - equity / peak if peak > 0 else 0.0

    def can_open(self, equity: float) -> tuple[bool, str]:
        st = self.state
        if st.kill_switch:
            return False, "kill switch actif (drawdown max atteint)"
        if st.cooldown_remaining > 0:
            return False, f"pause après une perte ({st.cooldown_remaining} bougie(s) restante(s))"
        if st.day_start_equity > 0 and equity <= st.day_start_equity * (1 - self.cfg.max_daily_loss):
            return False, "perte journalière max atteinte"
        return True, ""

    def on_trade_closed(self, pnl: float) -> None:
        if pnl < 0:
            self.state.cooldown_remaining = self.cfg.cooldown_bars

    def reset_kill_switch(self, equity: float) -> None:
        self.state.kill_switch = False
        self.state.peak_equity = equity
