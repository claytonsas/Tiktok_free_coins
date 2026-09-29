"""Structures de données partagées par le backtest et le bot."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Position:
    entry_price: float
    amount: float  # quantité de TAO détenue
    stop: float  # stop actuel (monte avec le stop suiveur, ne descend jamais)
    initial_stop: float
    highest: float  # plus haut atteint depuis l'entrée
    entry_time: str  # ISO 8601 UTC
    reason: str = ""
    entry_fee: float = 0.0  # frais d'achat, en devise de cotation (USDT)

    @property
    def risk_per_unit(self) -> float:
        return self.entry_price - self.initial_stop

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        return cls(**data)


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    amount: float
    pnl: float  # net de frais, en USDT
    pnl_pct: float
    r_multiple: float  # gain exprimé en multiple du risque initial
    fees: float
    entry_reason: str
    exit_reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def close_trade(
    pos: Position,
    exit_price: float,
    exit_amount: float,
    exit_fee: float,
    exit_time: str,
    exit_reason: str,
) -> Trade:
    """Construit le ``Trade`` correspondant à la vente de ``exit_amount`` TAO."""
    share = exit_amount / pos.amount if pos.amount > 0 else 1.0
    entry_fee = pos.entry_fee * share
    cost = pos.entry_price * exit_amount
    pnl = exit_price * exit_amount - exit_fee - cost - entry_fee
    risk = pos.risk_per_unit * exit_amount
    return Trade(
        entry_time=pos.entry_time,
        exit_time=exit_time,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        amount=exit_amount,
        pnl=pnl,
        pnl_pct=pnl / (cost + entry_fee) if cost > 0 else 0.0,
        r_multiple=pnl / risk if risk > 0 else 0.0,
        fees=entry_fee + exit_fee,
        entry_reason=pos.reason,
        exit_reason=exit_reason,
    )


def stop_reason(pos: Position) -> str:
    """Nom lisible du stop qui vient d'être touché."""
    if pos.stop >= pos.entry_price:
        return "stop_en_profit"
    if pos.stop > pos.initial_stop:
        return "stop_suiveur"
    return "stop_initial"
