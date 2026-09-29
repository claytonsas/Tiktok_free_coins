"""Accès à l'exchange (ccxt) et exécution des ordres : papier ou réel."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .data import ohlcv_to_df

log = logging.getLogger(__name__)


@dataclass
class Fill:
    price: float  # prix moyen d'exécution
    amount: float  # TAO réellement achetés (nets de frais) ou vendus
    fee: float  # frais, exprimés en devise de cotation (USDT)


class MarketData:
    """Données publiques + client privé ccxt (si des clés API sont fournies)."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str | None = None,
        secret: str | None = None,
        password: str | None = None,
        sandbox: bool = False,
    ):
        import ccxt

        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            raise ValueError(f"Exchange inconnu de ccxt : {exchange_id!r}")
        params: dict = {"enableRateLimit": True}
        if api_key and secret:
            params.update(apiKey=api_key, secret=secret)
            if password:
                params["password"] = password
        self.exchange = cls(params)
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        return ohlcv_to_df(self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit))

    def last_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        price = ticker.get("last") or ticker.get("close")
        if not price:
            raise RuntimeError(f"Pas de prix disponible pour {symbol}")
        return float(price)


def split_symbol(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/")
    return base, quote.split(":")[0]


class PaperBroker:
    """Simule les ordres au marché avec frais et glissement (aucun argent réel)."""

    def __init__(self, fee_rate: float, slippage: float, quote: float, base: float = 0.0):
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.quote = quote
        self.base = base

    def equity(self, symbol: str, price: float) -> float:
        return self.quote + self.base * price

    def buy(self, symbol: str, amount: float, price: float) -> Fill:
        fill_price = price * (1 + self.slippage)
        amount = min(amount, self.quote / (fill_price * (1 + self.fee_rate)))
        cost = amount * fill_price
        fee = cost * self.fee_rate
        self.quote -= cost + fee
        self.base += amount
        return Fill(fill_price, amount, fee)

    def sell(self, symbol: str, amount: float, price: float) -> Fill:
        fill_price = price * (1 - self.slippage)
        amount = min(amount, self.base)
        proceeds = amount * fill_price
        fee = proceeds * self.fee_rate
        self.base -= amount
        self.quote += proceeds - fee
        return Fill(fill_price, amount, fee)

    def snapshot(self) -> dict:
        return {"quote": self.quote, "base": self.base}


class LiveBroker:
    """Ordres réels au marché via ccxt. Utilisez un sous-compte dédié au bot."""

    def __init__(self, market: MarketData):
        self.ex = market.exchange
        self.ex.load_markets()

    def _balances(self, symbol: str) -> tuple[float, float, float]:
        """(TAO libres, USDT libres, TAO total)."""
        base, quote = split_symbol(symbol)
        bal = self.ex.fetch_balance()
        free, total = bal.get("free", {}), bal.get("total", {})
        return float(free.get(base) or 0), float(free.get(quote) or 0), float(total.get(base) or 0)

    def equity(self, symbol: str, price: float) -> float:
        base, quote = split_symbol(symbol)
        total = self.ex.fetch_balance().get("total", {})
        return float(total.get(quote) or 0) + float(total.get(base) or 0) * price

    def _fee_in_quote(self, order: dict, symbol: str, price: float) -> tuple[float, float]:
        """(frais convertis en USDT, frais prélevés en TAO)."""
        base, quote = split_symbol(symbol)
        fee_quote = fee_base = 0.0
        for fee in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            cost = float(fee.get("cost") or 0)
            if fee.get("currency") == quote:
                fee_quote += cost
            elif fee.get("currency") == base:
                fee_base += cost
                fee_quote += cost * price
        return fee_quote, fee_base

    def buy(self, symbol: str, amount: float, price: float) -> Fill:
        amount = float(self.ex.amount_to_precision(symbol, amount))
        base_before, quote_free, _ = self._balances(symbol)
        cost_max = quote_free * 0.995  # marge pour les frais
        if amount * price > cost_max:
            amount = float(self.ex.amount_to_precision(symbol, cost_max / price))
        # Le prix n'est utilisé que par les exchanges qui exigent un coût pour un achat au marché.
        order = self.ex.create_order(symbol, "market", "buy", amount, price)
        order = self._refresh(order, symbol)
        avg = float(order.get("average") or price)
        filled = float(order.get("filled") or amount)
        fee_quote, fee_base = self._fee_in_quote(order, symbol, avg)
        base_after, _, _ = self._balances(symbol)
        received = base_after - base_before
        net = received if 0 < received <= filled * 1.001 else filled - fee_base
        return Fill(avg, net, fee_quote)

    def sell(self, symbol: str, amount: float, price: float) -> Fill:
        base_free, _, _ = self._balances(symbol)
        amount = float(self.ex.amount_to_precision(symbol, min(amount, base_free)))
        order = self.ex.create_order(symbol, "market", "sell", amount)
        order = self._refresh(order, symbol)
        avg = float(order.get("average") or price)
        filled = float(order.get("filled") or amount)
        fee_quote, _ = self._fee_in_quote(order, symbol, avg)
        return Fill(avg, filled, fee_quote)

    def _refresh(self, order: dict, symbol: str) -> dict:
        if order.get("average") and order.get("filled"):
            return order
        try:
            return self.ex.fetch_order(order["id"], symbol)
        except Exception as exc:  # certains exchanges ne permettent pas fetch_order
            log.debug("fetch_order indisponible : %s", exc)
            return order

    def snapshot(self) -> None:
        return None
