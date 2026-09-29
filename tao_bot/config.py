"""Configuration : fichier YAML + variables d'environnement (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .risk import RiskConfig
from .strategy import StrategyConfig


@dataclass
class ExchangeConfig:
    id: str = "binance"
    symbol: str = "TAO/USDT"
    btc_symbol: str = "BTC/USDT"
    timeframe: str = "4h"
    sandbox: bool = False
    fee_rate: float = 0.001  # 0,1 % par ordre (Binance spot sans réduction)
    slippage: float = 0.0005  # glissement estimé sur les ordres au marché
    min_notional: float = 10.0  # montant minimum d'un ordre, en USDT


@dataclass
class BacktestConfig:
    initial_capital: float = 1000.0


@dataclass
class BotConfig:
    poll_seconds: int = 60
    warmup_bars: int = 500
    state_dir: str = "state"
    paper_start_balance: float = 1000.0


@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    bot: BotConfig = field(default_factory=BotConfig)


def _build(cls: type, data: dict[str, Any] | None, section: str = "racine") -> Any:
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ValueError(f"La section '{section}' doit être un dictionnaire")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Clé(s) inconnue(s) dans '{section}' : {', '.join(sorted(unknown))}")
    defaults = cls()
    kwargs = {}
    for name, value in data.items():
        default = getattr(defaults, name)
        kwargs[name] = _build(type(default), value, name) if is_dataclass(default) else value
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> Config:
    """Charge ``path`` (ou ``config.yaml`` s'il existe) ; valeurs par défaut sinon."""
    if path is None:
        path = Path("config.yaml") if Path("config.yaml").exists() else None
    if path is None:
        return Config()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _build(Config, data)


def load_dotenv(path: str | Path = ".env") -> None:
    """Charge un fichier .env simple (CLE=valeur) sans écraser l'environnement."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
