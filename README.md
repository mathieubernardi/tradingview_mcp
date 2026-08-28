# MCP TradingView Server

Serveur MCP (Model Context Protocol) en Python qui expose les données de marché
**TradingView** à un client MCP — Claude Desktop, claude.ai ou tout autre client
compatible. Les données sont récupérées via `tvdatafeed` (OHLCV) et
`tradingview-ta` (screener).

## Outils exposés

| Outil MCP | Description |
|---|---|
| `tv_get_historical_data` | Bougies OHLCV historiques (actions, crypto, forex, futures) |
| `tv_get_quote` | Dernière bougie + variation % par rapport à la précédente |
| `tv_get_indicators` | Indicateurs techniques, contexte multi-timeframe et score composite |
| `tv_screener` | Filtrage par marché, volume et RSI sur un univers prédéfini |

### `tv_get_indicators` en détail

Indicateurs calculés : **RSI**, **MACD**, **Bollinger Bands** (avec détection de
squeeze), **EMA**, **SMA**, **ATR** (avec niveaux de stop long/short),
**Stochastique**, **ADX** (+DI/−DI), **OBV**, moyenne et ratio de volume 20
périodes, et détection de divergences RSI.

Trois options désactivées par défaut :

| Paramètre | Effet |
|---|---|
| `include_weekly: true` | Ajoute un contexte hebdomadaire : `weekly_rsi`, `weekly_adx`, `weekly_trend` |
| `benchmark_symbol: "CW8"` | Calcule la force relative à 1 mois et 3 mois contre ce benchmark |
| `include_score: true` | Calcule un score technique composite dans `[-1, +1]` et ses sous-scores |

Intervalles acceptés : `1`, `3`, `5`, `15`, `30`, `45` (minutes), `1H`, `2H`,
`3H`, `4H`, `1D`, `1W`, `1M`.

### Portée du screener

`tvdatafeed` n'expose pas d'API de screener. `tv_screener` parcourt donc un
**univers prédéfini en dur** dans `MARKET_SYMBOLS` (`src/mcp_tradingview/client.py`)
et calcule les indicateurs sur chaque symbole via `tradingview-ta` :

| Marché | Univers couvert |
|---|---|
| `france` | 29 symboles Euronext Paris — CAC 40, quelques midcaps tech, ETF éligibles PEA |
| `america` | 20 blue chips Nasdaq / NYSE |
| `crypto` | 8 paires majeures Binance |
| `forex` | 6 paires majeures |

L'énumération `ScreenerMarket` accepte aussi `futures`, `india`, `brazil`,
`australia`, `canada`, `europe` et `hongkong`, mais **aucun univers n'est défini
pour ces marchés** : l'appel retourne une liste vide. Ajouter une entrée dans
`MARKET_SYMBOLS` suffit à les activer.

## Architecture

```
Client MCP (Claude Desktop, claude.ai…)
    │
    ├── stdio ──► process local
    └── SSE   ──► serveur HTTP (Starlette + uvicorn)
              │
              ├──► tvdatafeed    : bougies OHLCV
              └──► tradingview-ta : screener
                        │
                        └──► cache TTL en mémoire
```

## Prérequis

- Python ≥ 3.11
- Un compte TradingView est optionnel — l'accès anonyme fonctionne, avec des
  limites de débit plus strictes.

## Installation

```bash
git clone https://github.com/mathieubernardi/tradingview_mcp.git
cd tradingview_mcp

pip install -e ".[dev]"

cp .env.example .env   # puis éditer .env
```

## Configuration

Toutes les variables sont préfixées `TV_` et peuvent être passées soit par un
fichier `.env`, soit par l'environnement.

| Variable | Défaut | Description |
|---|---|---|
| `TV_TV_USERNAME` | *(vide)* | Identifiant TradingView (optionnel) |
| `TV_TV_PASSWORD` | *(vide)* | Mot de passe TradingView (optionnel) |
| `TV_TRANSPORT` | `stdio` | `stdio` ou `sse` |
| `TV_HOST` | `127.0.0.1` | Hôte du serveur SSE |
| `TV_PORT` | `8765` | Port du serveur SSE (1024–65535) |
| `TV_DEFAULT_EXCHANGE` | `EURONEXT` | Place par défaut si l'appel n'en précise pas |
| `TV_DEFAULT_N_BARS` | `500` | Nombre de bougies par défaut |
| `TV_MAX_N_BARS` | `5000` | Plafond du nombre de bougies |
| `TV_CACHE_TTL_SECONDS` | `60` | Durée de vie du cache mémoire (`0` = désactivé) |
| `TV_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR` |

Le double préfixe de `TV_TV_USERNAME` / `TV_TV_PASSWORD` est voulu : le préfixe
d'environnement `TV_` s'ajoute aux champs `tv_username` / `tv_password`.

## Lancer le serveur

Deux points d'entrée équivalents : le script console `mcp-tradingview` et le
module `python -m mcp_tradingview`. Le second ne dépend pas du `PATH`, ce qui le
rend plus fiable sous Windows.

```bash
# Linux / macOS — stdio
TV_TRANSPORT=stdio python -m mcp_tradingview

# Linux / macOS — SSE
TV_TRANSPORT=sse python -m mcp_tradingview
```

```powershell
# Windows PowerShell — stdio
$env:TV_TRANSPORT = "stdio"; python -m mcp_tradingview

# Windows PowerShell — SSE
$env:TV_TRANSPORT = "sse"; python -m mcp_tradingview
```

## Intégration Claude Desktop (stdio)

Ajouter dans `claude_desktop_config.json` — `~/Library/Application Support/Claude/`
sous macOS, `%APPDATA%\Claude\` sous Windows :

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "python",
      "args": ["-m", "mcp_tradingview"],
      "env": {
        "TV_TRANSPORT": "stdio",
        "TV_CACHE_TTL_SECONDS": "60"
      }
    }
  }
}
```

Si l'environnement Python n'est pas celui par défaut, remplacer `"python"` par le
chemin absolu de l'interpréteur du virtualenv. Pour utiliser un compte
TradingView, ajouter `TV_TV_USERNAME` et `TV_TV_PASSWORD` dans le bloc `env`.

En mode stdio, la session TradingView est établie paresseusement au premier appel
d'outil : le handshake MCP répond immédiatement et le client ne subit pas de
timeout au démarrage.

## Intégration SSE

```bash
TV_TRANSPORT=sse TV_PORT=8765 python -m mcp_tradingview
```

| Endpoint | Rôle |
|---|---|
| `GET /sse` | Flux SSE à déclarer côté client MCP |
| `POST /messages/` | Canal de retour des messages client |
| `GET /health` | Sonde de vivacité, retourne `ok` |

## Développement

```bash
pytest tests/ -v          # tests
ruff check src/ tests/    # lint
mypy src/                 # type checking (strict)
```

## Limites connues

- `tvdatafeed` est un client non officiel qui s'appuie sur les endpoints web de
  TradingView : il peut cesser de fonctionner sans préavis.
- `tv_screener` ne balaye pas un marché entier, seulement l'univers prédéfini
  décrit plus haut.
- Sans compte Premium TradingView, les données peuvent accuser un retard de
  plusieurs minutes.
- L'accès anonyme subit des limites de débit sensiblement plus strictes qu'un
  compte connecté.
- Les erreurs d'un outil sont renvoyées au client sous forme de JSON
  `{"error": ..., "tool": ..., "arguments": ...}` plutôt que remontées comme
  erreurs de protocole.

## Structure du projet

```
tradingview_mcp/
├── src/mcp_tradingview/
│   ├── __init__.py      # métadonnées du paquet (__version__)
│   ├── __main__.py      # entrée `python -m mcp_tradingview`
│   ├── config.py        # settings (pydantic-settings, préfixe TV_)
│   ├── models.py        # schémas Pydantic d'entrée / sortie
│   ├── client.py        # client TradingView, indicateurs, cache TTL
│   ├── tools.py         # définition JSON Schema + dispatch des outils
│   └── server.py        # serveur MCP (stdio + SSE) et entrée console
├── tests/
│   └── test_server.py
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```
