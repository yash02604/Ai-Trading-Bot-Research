# AI Trading Assistant

An experimental, Indian-stock intraday research and paper-trading project. The system keeps quantitative eligibility and risk controls ahead of OpenRouter context analysis. **There is no real-order execution path.** Do not treat historical results as a forecast or as approval for live trading.

## Architecture and workflow

1. Upstox returns quote/candle data through `app.upstox_client` (read-only GET calls).
2. `app.market_analysis` normalizes candles and calculates EMA 9/20, RSI 14, MACD (12/26/9), MACD signal/histogram, and 20-bar volume average.
3. `app.reversal_detector` looks for a three-candle decline and a close above the preceding candle. The researched/frozen setup used for decisions further requires a decline from 1% inclusive to 2% exclusive and RSI from 20 inclusive to 25 exclusive.
4. `app.risk_engine` checks daily loss, open positions, daily trade count, stop placement, per-trade risk, and available capital. A rejected risk check stops the pipeline before AI.
5. OpenRouter may confirm an eligible candidate. It can only return decision support; unavailable, invalid, or non-BUY answers become HOLD.
6. `app.paper_trader` simulates whole-share long entries, stop exits, P&L, trade counts, capital, and history. Broker order APIs are not called.
7. Decisions are appended to `logs/decisions.jsonl`; closed paper trades go to `logs/paper_trades.csv`. Both files are ignored by Git.

## Setup

From the repository's parent directory in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
cd .\ai-tarding-assistant
python -m pip install -r requirements.txt
```

Create a local `.env` based on `.env.example`. `.env` is excluded by `.gitignore`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `UPSTOX_ACCESS_TOKEN` | For Upstox reads | Current Upstox API access token |
| `OPENROUTER_API_KEY` | For live AI context | OpenRouter API key |
| `OPENROUTER_MODEL` | No | Model identifier; defaults to `openrouter/free` |
| `API_TIMEOUT_SECONDS` | No | Positive request timeout; defaults to 10 seconds |
| `PAPER_TRADING` | Yes for paper session | Must be explicitly `true`; missing, invalid, or false blocks the runner. |

Never put credentials in source, test fixtures, logs, or documentation. Access-token renewal/OAuth is not implemented; refresh a token through the official Upstox flow and update the environment.

## Run

Manually start the read-only, persistent V1 paper session (no session starts automatically):

```powershell
python -m app.paper_session
```

### Manual V1 live paper validation

The separate `app.paper_session` runner uses Upstox market-data GET requests only. It has no broker order interface. It requires `PAPER_TRADING=true`, an Upstox read token, and all four index instrument keys below:

| Index | Required variable |
| --- | --- |
| NIFTY 50 | `UPSTOX_NIFTY50_KEY` |
| NIFTY BANK | `UPSTOX_NIFTY_BANK_KEY` |
| NIFTY IT | `UPSTOX_NIFTY_IT_KEY` |
| SENSEX | `UPSTOX_SENSEX_KEY` |

`app/config.py` automatically loads `.env` from this project directory when the configuration module is imported. Existing process environment values take precedence. Copy `.env.example` to `.env`, then fill the four values by copying each index's exact `instrument_key` field from Upstox's [official instrument master documentation](https://upstox.com/developer/api-documentation/instruments/). Upstox documents instrument keys as `<segment>|<trading_symbol>`; use the exact segment and symbol spelling (including spaces) published for the matching index. Do not substitute a guessed symbol or another index. For example, quote a value containing spaces or `|` in `.env` as `"<exact instrument_key from Upstox>"`. Missing/invalid paper mode, token, or any index key stops startup, and missing keys are named in the startup error. The runner does not start automatically.

Run it manually from this directory during market hours:

```powershell
$env:PAPER_TRADING = "true"
python -m app.paper_session
```

It scans once per minute and only evaluates completed stock signal candles. The following five-minute candle open is retained as a historical reference; it is not treated as an executable fill when observed later. Entries use a fresh (at most five seconds old) Upstox full-quote best ask observed while that next candle is forming, and reject when the quote/ask depth is unavailable or insufficient. This is still a paper estimate, not an order execution guarantee. Context is joined at the signal candle and rejects when any required index is absent/stale or at least three of four indices are down. Simulated fills are recorded as paper fills. On restart, the SQLite position is recovered and processed candles are not replayed. Ctrl+C stops the loop while retaining state.

Persistent records are stored in `logs/paper_state.sqlite3` (decisions, unique signal IDs, one open position, trades, audit events, and per-day limits). Daily JSON summaries are written under `logs/paper_reports/`. The existing `logs/decisions.jsonl` and `logs/paper_trades.csv` remain legacy outputs for the original one-shot app. Historical results and `backtests/stage11_research/` are not modified by the runner. Estimated round-trip costs use `app.cost_model`; they are estimates, including the model's 5 bps per-side slippage assumption. Paper results validate system behavior and do not establish profitability.

Read-only quote observations are kept in the separate append-only `execution_observations` SQLite table. Export and summarize them without starting the runner with `python -m scripts.export_execution_observations`; the data schema and timestamp limitations are documented in [docs/execution-observations.md](docs/execution-observations.md). Recalculate historical execution-cost sensitivities in a separate output directory with `python -m scripts.execution_cost_sensitivity`.

Run a deterministic stop-loss paper simulation without broker or AI calls:

```powershell
python -m app.simulator
```

Run the reproducible, multi-stock historical validation across the configured basket:

```powershell
python run_historical_validation.py
```

It writes the cost-adjusted portfolio ledger, standalone per-stock results, signal counts, calendar/regime metrics, dataset audit, and report under `backtests/validation/`, plus a convenient project-root `VALIDATION_REPORT.md`. The analysis uses cached historical candles and does not make API calls.

The single-symbol exploratory runner is also available:

```powershell
python run_historical_backtest.py RELIANCE
```

Options for the multi-stock validator include `--data`, `--output`, `--capital`, `--stop-pct`, `--horizon`, and `--slippage-bps-per-side`. It applies explicit estimated Indian intraday charges and slippage; see `VALIDATION_REPORT.md` for assumptions, gross and net results, and limitations. The single-symbol exploratory runner reports gross results and does not apply that cost model. Both use next-candle entries, bar-based stop checks, and same-session exits.

Run tests:

```powershell
python -m pytest -q
```

## Risk and strategy limits

The legacy deterministic simulator defaults to ₹1,000; the live paper-validation runner uses the frozen historical ₹100,000 initial capital unless `PAPER_INITIAL_CAPITAL` is configured. V1 risk is 1% per trade, a 2% daily loss cap, at most one open position, and at most three trades per day. Position sizing is whole-share long-only and exposure cannot exceed account capital. The runner uses a 1% stop from the observed ask and the six-candle horizon. Paper state does not reconcile broker balances.

AI runs only after a quantitative candidate passes risk checks. AI failure returns HOLD. The AI cannot create a strategy signal, lift a risk rejection, or place an order. The current live monitor (`signal_monitor.py`) is a separate alert-only research path and applies event deduplication; it does not submit orders.

## Current limitations

- Upstox calls use an existing access token; OAuth/token refresh and full instrument-master management are not implemented.
- The historical backtest is long-only and uses simplified bar-based fills. No transaction costs or slippage are included.
- OpenRouter live availability depends on account access, chosen provider/model, and network access.
- Backtest and paper results require broader out-of-sample validation. No real-money trading is enabled or considered ready.

See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the audit checklist and latest validation state.
