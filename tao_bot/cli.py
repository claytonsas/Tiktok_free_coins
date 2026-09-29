"""Interface en ligne de commande : ``python -m tao_bot <commande>``."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from .config import Config, load_config, load_dotenv
from .data import fetch_history, generate_synthetic_market, load_csv, save_csv, timeframe_delta

log = logging.getLogger("tao_bot")


def _setup_logging(verbose: bool, logfile: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _market(cfg: Config, private: bool = False):
    from .exchange import MarketData

    return MarketData(
        cfg.exchange.id,
        api_key=os.getenv("EXCHANGE_API_KEY") if private else None,
        secret=os.getenv("EXCHANGE_API_SECRET") if private else None,
        password=os.getenv("EXCHANGE_API_PASSWORD") if private else None,
        sandbox=cfg.exchange.sandbox,
    )


def _history(cfg: Config, symbol: str, days: int) -> pd.DataFrame:
    ex = cfg.exchange
    warmup = timeframe_delta(ex.timeframe) * cfg.bot.warmup_bars
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days) - warmup
    log.info("Téléchargement %s %s depuis %s...", symbol, ex.timeframe, since.date())
    return fetch_history(_market(cfg).exchange, symbol, ex.timeframe, since)


# ------------------------------------------------------------------ commandes
def cmd_analyse(cfg: Config, args: argparse.Namespace) -> None:
    from .analysis import build_analysis
    from .data import drop_unclosed
    from .strategy import compute_indicators, generate_signals

    ex = cfg.exchange
    market = _market(cfg)
    now = pd.Timestamp.now(tz="UTC")
    df = drop_unclosed(market.fetch_ohlcv(ex.symbol, ex.timeframe, cfg.bot.warmup_bars), ex.timeframe, now)
    btc = drop_unclosed(market.fetch_ohlcv(ex.btc_symbol, ex.timeframe, cfg.bot.warmup_bars), ex.timeframe, now)
    ind = compute_indicators(df, cfg.strategy, btc)
    print(build_analysis(ind, generate_signals(ind, cfg.strategy), cfg, args.capital))


def cmd_backtest(cfg: Config, args: argparse.Namespace) -> None:
    from .backtest import format_report, run_backtest

    btc = None
    if args.synthetic:
        df, btc = generate_synthetic_market(args.bars, cfg.exchange.timeframe, seed=args.seed)
        title = f"Backtest sur données SYNTHÉTIQUES (graine {args.seed}) — pas le vrai TAO"
    elif args.csv:
        df = load_csv(args.csv)
        btc = load_csv(args.btc_csv) if args.btc_csv else None
        if btc is None and cfg.strategy.use_btc_filter:
            log.warning("Pas de --btc-csv : le filtre BTC sera neutre pendant ce backtest.")
        title = f"Backtest {args.csv}"
    else:
        df = _history(cfg, cfg.exchange.symbol, args.days)
        if cfg.strategy.use_btc_filter:
            btc = _history(cfg, cfg.exchange.btc_symbol, args.days)
        title = f"Backtest {cfg.exchange.symbol} {cfg.exchange.timeframe} — {args.days} derniers jours"

    if len(df) < cfg.strategy.ema_slow + 50:
        sys.exit(f"Pas assez de données ({len(df)} bougies) : il en faut au moins {cfg.strategy.ema_slow + 50}.")

    result = run_backtest(df, cfg, btc, initial_capital=args.capital)
    print(f"Période : {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d} ({len(df)} bougies)")
    print(format_report(result.metrics, title))

    if result.trades:
        print("\nDerniers trades :")
        cols = ["entry_time", "exit_time", "entry_price", "exit_price", "pnl", "r_multiple", "entry_reason", "exit_reason"]
        print(result.trades_df()[cols].tail(10).round(2).to_string(index=False))

    if args.export:
        out = Path(args.export)
        out.mkdir(parents=True, exist_ok=True)
        result.trades_df().to_csv(out / "trades.csv", index=False)
        result.equity.to_csv(out / "equity.csv", index_label="timestamp")
        print(f"\nRésultats exportés dans {out}/ (trades.csv, equity.csv)")


def cmd_download(cfg: Config, args: argparse.Namespace) -> None:
    out = Path(args.out)
    tf = cfg.exchange.timeframe
    for symbol in (cfg.exchange.symbol, cfg.exchange.btc_symbol):
        df = _history(cfg, symbol, args.days)
        path = out / f"{symbol.replace('/', '_')}_{tf}.csv"
        save_csv(df, path)
        print(f"{symbol} : {len(df)} bougies -> {path}")


def cmd_run(cfg: Config, args: argparse.Namespace, live: bool) -> None:
    from .bot import BotState, TradingBot
    from .exchange import LiveBroker, PaperBroker
    from .notifier import Notifier

    mode = "live" if live else "paper"
    state_dir = Path(cfg.bot.state_dir)
    _setup_logging(args.verbose, state_dir / f"{mode}.log")
    state_path = state_dir / f"{mode}_state.json"
    state = BotState.load(state_path)

    if live:
        if not (os.getenv("EXCHANGE_API_KEY") and os.getenv("EXCHANGE_API_SECRET")):
            sys.exit("Clés API manquantes : définissez EXCHANGE_API_KEY et EXCHANGE_API_SECRET (fichier .env).")
        print(
            "⚠️  MODE RÉEL : le bot va passer de vrais ordres avec votre argent sur "
            f"{cfg.exchange.id} ({cfg.exchange.symbol}).\n"
            "   Vous pouvez perdre tout ou partie du capital. Testez d'abord en mode papier."
        )
        if not args.yes and input("Tapez OUI pour continuer : ").strip() != "OUI":
            sys.exit("Annulé.")
        market = _market(cfg, private=True)
        broker = LiveBroker(market)
    else:
        market = _market(cfg)
        paper = state.paper or {"quote": cfg.bot.paper_start_balance, "base": 0.0}
        broker = PaperBroker(cfg.exchange.fee_rate, cfg.exchange.slippage, paper["quote"], paper["base"])

    bot = TradingBot(
        cfg, market, broker, state, state_path, state_dir / f"{mode}_trades.csv", Notifier(), mode
    )
    if args.reset_kill_switch:
        price = market.last_price(cfg.exchange.symbol)
        bot.risk.reset_kill_switch(broker.equity(cfg.exchange.symbol, price))
        log.info("Kill switch réinitialisé.")
    bot.run(once=args.once)


# ----------------------------------------------------------------- parseur
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tao_bot",
        description="Bot de trading crypto spécialisé TAO (Bittensor).",
    )
    parser.add_argument("-c", "--config", help="fichier de configuration YAML (défaut : config.yaml s'il existe)")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyse", help="analyse actuelle du marché TAO + signal")
    p.add_argument("--capital", type=float, default=1000.0, help="capital pour calculer la taille de position")

    p = sub.add_parser("backtest", help="teste la stratégie sur l'historique")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--days", type=int, default=365, help="jours d'historique à télécharger (défaut 365)")
    src.add_argument("--csv", help="fichier CSV TAO (timestamp,open,high,low,close,volume)")
    src.add_argument("--synthetic", action="store_true", help="données fictives, sans connexion")
    p.add_argument("--btc-csv", help="fichier CSV BTC pour le filtre de tendance (avec --csv)")
    p.add_argument("--capital", type=float, help="capital initial (défaut : config)")
    p.add_argument("--bars", type=int, default=3000, help="nombre de bougies synthétiques")
    p.add_argument("--seed", type=int, default=42, help="graine des données synthétiques")
    p.add_argument("--export", help="dossier où écrire trades.csv et equity.csv")

    p = sub.add_parser("download", help="télécharge l'historique TAO et BTC en CSV")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--out", default="data")

    for name, help_ in (("paper", "trading papier en temps réel (argent fictif)"), ("live", "trading RÉEL")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--once", action="store_true", help="un seul cycle puis arrêt")
        p.add_argument("--reset-kill-switch", action="store_true", help="réarme le bot après un kill switch")
        if name == "live":
            p.add_argument("--yes", action="store_true", help="ne pas demander de confirmation")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    load_dotenv()
    cfg = load_config(args.config)
    try:
        if args.command == "analyse":
            cmd_analyse(cfg, args)
        elif args.command == "backtest":
            cmd_backtest(cfg, args)
        elif args.command == "download":
            cmd_download(cfg, args)
        else:
            cmd_run(cfg, args, live=args.command == "live")
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    except Exception as exc:
        if type(exc).__module__.startswith("ccxt"):
            sys.exit(f"Erreur de l'exchange ({cfg.exchange.id}) : {exc}")
        raise
