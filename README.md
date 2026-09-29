# Trading — Bot crypto spécialisé TAO (Bittensor)

Bot de trading automatique en Python, conçu spécifiquement pour le **TAO**
(le jeton du réseau d'IA décentralisée Bittensor). Il fonctionne sur tous les
exchanges supportés par [ccxt](https://github.com/ccxt/ccxt) (Binance par défaut).

- 📊 **Analyse** : état actuel du marché TAO, score sur 100, signal et plan de trade
- 🧪 **Backtest** : test de la stratégie sur l'historique, frais et glissement inclus
- 📝 **Paper trading** : le bot tourne en temps réel avec de l'argent fictif
- 💰 **Trading réel** : ordres au marché sur votre compte (avec confirmation)
- 🛡️ **Gestion du risque** : taille de position selon la volatilité, stop suiveur, kill switch
- 📱 **Alertes Telegram** (optionnel) à chaque achat, vente ou incident

> ⚠️ **Avertissement** : ce projet est un outil, pas un conseil financier. Aucun
> bot ne garantit des gains ; le TAO peut perdre plus de 50 % de sa valeur en
> quelques semaines. N'investissez que ce que vous pouvez vous permettre de
> perdre, et testez toujours en mode papier avant le mode réel.

---

## Pourquoi une stratégie spéciale TAO ?

| Caractéristique du TAO | Réponse du bot |
|---|---|
| Très volatil (souvent 5 à 10 %/jour) | Stops et taille de position calculés avec l'**ATR** : plus c'est volatil, plus la position est petite |
| Évolue en longues tendances | Stratégie de **suivi de tendance** (EMA 21/50/200, ADX) avec **stop suiveur** pour laisser courir les gains |
| Très corrélé au BTC | **Filtre BTC** : pas d'achat quand le BTC est sous son EMA200 |
| Mèches et fausses cassures fréquentes | Breakout validé seulement avec **volume** > 1,5x la moyenne et ADX ≥ 20 |
| Chutes brutales (−70 % et plus entre deux cycles) | **Spot uniquement, sans levier**, kill switch à −25 % |

## La stratégie en détail

Calculée à la clôture de chaque bougie (4h par défaut) :

1. **Régime haussier obligatoire** : prix > EMA200 et EMA50 > EMA200, et BTC > EMA200.
2. **Deux types d'entrée** :
   - **Breakout** : clôture au-dessus du plus haut des 20 bougies précédentes, avec
     volume > 1,5x la moyenne et ADX ≥ 20 ;
   - **Pullback** : en tendance haussière, le RSI(14) repasse au-dessus de 40 après
     une correction, sur une bougie verte.
3. **Filtres anti-piège** : pas d'achat si le RSI ≥ 80 (surchauffe) ou si l'ATR
   dépasse 12 % du prix (volatilité extrême).
4. **Sorties** :
   - stop initial à **2,5 ATR** sous le prix d'achat ;
   - stop suiveur « chandelier » à **4 ATR** sous le plus haut atteint ;
   - stop remonté au **point mort** dès que le gain atteint 1,5 fois le risque ;
   - sortie si la tendance se casse (EMA21 < EMA50 et clôture < EMA50).

## Gestion du risque

- **1 % du capital risqué par trade** : si le stop initial est touché, vous perdez ~1 %.
- **Kill switch** : si le capital baisse de 25 % depuis son plus haut, le bot vend
  tout et ne prend plus de position jusqu'à réarmement manuel.
- **Perte journalière max** : plus de nouvelle entrée après −5 % dans la journée.
- **Pause** de 3 bougies après un trade perdant (évite de s'acharner).

Tout est réglable dans `config.yaml`.

---

## Installation

Python 3.10 ou plus récent.

```bash
git clone https://github.com/claytonsas/Tiktok_free_coins.git trading
cd trading
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

## Utilisation

### 1. Analyser le marché TAO maintenant

```bash
python -m tao_bot analyse --capital 1000
```

Exemple de sortie :

```
=== Analyse TAO/USDT (binance, 4h) — bougie du 2026-09-29 16:00 UTC ===
Prix           : 412.30 USDT
Tendance       : HAUSSIÈRE (prix > EMA200 et EMA50 > EMA200)
RSI(14)        : 58.2
ADX(14)        : 27.4 (tendance forte)
Filtre BTC     : OK (BTC ... > EMA200 ...)
Score marché   : 72/100 → Haussier fort
Signal         : AUCUN — attendre
Plan si achat  : stop 381.50 (-7.5 %), taille 0.3247 TAO ≈ 133.87 USDT pour 1 000 USDT de capital (1.0 % de risque)
```

(Valeurs d'illustration.)

### 2. Backtester la stratégie

```bash
python -m tao_bot backtest --days 730                  # télécharge 2 ans de TAO et BTC
python -m tao_bot backtest --days 730 --export results # + trades.csv et equity.csv
python -m tao_bot download --days 730                  # sauvegarde l'historique en CSV
python -m tao_bot backtest --csv data/TAO_USDT_4h.csv --btc-csv data/BTC_USDT_4h.csv
python -m tao_bot backtest --synthetic                 # données fictives, sans internet
```

Le rapport affiche le rendement, le drawdown max, le Sharpe, le taux de réussite,
le profit factor et la comparaison avec un simple achat-conservation (buy & hold).
Les ordres sont exécutés à l'ouverture de la bougie suivante le signal (pas de
triche sur le futur) avec frais et glissement.

### 3. Paper trading (argent fictif, prix réels)

```bash
python -m tao_bot paper
```

Le bot démarre avec 1 000 USDT fictifs, vérifie le prix chaque minute et
enregistre ses trades dans `state/paper_trades.csv`. Vous pouvez l'arrêter
(Ctrl+C) et le relancer : la position et le solde sont conservés.
Laissez-le tourner plusieurs semaines avant de passer au réel.

### 4. Trading réel

1. Créez une clé API sur votre exchange avec **uniquement le droit de trading spot**
   (jamais le droit de retrait). Idéalement, utilisez un **sous-compte dédié au bot**.
2. Copiez `.env.example` en `.env` et remplissez `EXCHANGE_API_KEY` et `EXCHANGE_API_SECRET`.
3. Lancez :

```bash
python -m tao_bot live
```

Le bot demande de taper `OUI` pour confirmer. Il doit tourner **en continu** pour
surveiller le stop (sur un VPS ou un Raspberry Pi par exemple). Après un kill
switch, relancez avec `--reset-kill-switch` une fois la situation analysée.

### Alertes Telegram (optionnel)

1. Créez un bot avec [@BotFather](https://t.me/BotFather) et récupérez le token.
2. Envoyez un message à votre bot, puis récupérez votre `chat_id`
   (via `https://api.telegram.org/bot<TOKEN>/getUpdates`).
3. Renseignez `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`.

---

## Structure du projet

```
tao_bot/
  indicators.py   EMA, RSI, ATR, ADX, Donchian
  strategy.py     stratégie TAO : régime, entrées, stops, score
  risk.py         taille de position, kill switch, perte journalière, pause
  backtest.py     moteur de backtest et statistiques
  bot.py          boucle temps réel (papier ou réel) + sauvegarde de l'état
  exchange.py     connexion ccxt, broker papier, broker réel
  analysis.py     rapport d'analyse du marché
  notifier.py     alertes Telegram
  data.py         téléchargement, CSV, données synthétiques
  cli.py          commandes : analyse, backtest, download, paper, live
tests/            tests automatiques (pytest)
config.example.yaml
```

## Tests

```bash
python -m pytest
```

Les tests vérifient notamment l'absence de biais d'anticipation (un signal passé
ne change jamais quand de nouvelles bougies arrivent), la cohérence des gains et
des frais, le respect du risque par trade, le kill switch, la reprise après
redémarrage et le déclenchement du stop.

## Conseils

- Commencez par `backtest --days 730` pour voir le comportement sur un cycle complet,
  puis `paper` pendant plusieurs semaines.
- `risk_per_trade: 0.02` double les gains **et** les pertes : restez à 1 % au début.
- Méfiez-vous de la sur-optimisation : des réglages parfaits sur le passé
  fonctionnent rarement aussi bien dans le futur.
