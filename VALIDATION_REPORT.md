# Historical Validation Report

Generated: 2026-10-02T20:27:01.304936+05:30

## Dataset

- Source: `C:\Users\Priyanka\Desktop\ai-trading-bot - Copy\ai-tarding-assistant\data\research\extended_historical_robustness_candles.csv` (locally cached Upstox historical candles; no synthetic data).
- Fixed basket: 14 configured liquid Indian equities across banking/financials, IT, consumer, telecom, auto, pharma, energy, and industrials: AXISBANK, BHARTIARTL, HDFCBANK, ICICIBANK, INFY, ITC, KOTAKBANK, LT, MARUTI, RELIANCE, SBIN, SUNPHARMA, TCS, TITAN.
- Period: 2023-12-01T09:15:00+05:30 to 2025-12-01T15:25:00+05:30.
- Timeframe: 5-minute candles.
- Raw rows: 518,570; usable after invalid-row exclusion: 518,567; invalid rows excluded: 3.
- Duplicate symbol/timestamp rows: 0; zero-volume candles retained: 4.
- Same-day timestamp gaps over five minutes: 28. Frozen signal windows and entry bars crossing such a gap are excluded; positions use observed OHLC bars until session close/exit.
- Missing symbols from the configured basket: none.
- Data quality exclusions (timestamp and reason only): `[{"symbol": "BHARTIARTL", "timestamp": "2024-06-25 09:15:00+05:30", "reason": "invalid_ohlc_or_volume"}, {"symbol": "HDFCBANK", "timestamp": "2024-06-25 09:15:00+05:30", "reason": "invalid_ohlc_or_volume"}, {"symbol": "ICICIBANK", "timestamp": "2024-06-25 09:15:00+05:30", "reason": "invalid_ohlc_or_volume"}]`.

### Candles per symbol

| Symbol | Candles |
|---|---|
| AXISBANK | 37041 |
| BHARTIARTL | 37040 |
| HDFCBANK | 37040 |
| ICICIBANK | 37040 |
| INFY | 37041 |
| ITC | 37037 |
| KOTAKBANK | 37041 |
| LT | 37041 |
| MARUTI | 37041 |
| RELIANCE | 37041 |
| SBIN | 37041 |
| SUNPHARMA | 37041 |
| TCS | 37041 |
| TITAN | 37041 |

## Strategy and method

The strategy rules are unchanged: on a completed candle, close is 1.0% inclusive to 2.0% exclusive below the close three observation candles earlier; RSI(14) is 20 inclusive to 25 exclusive; and the current close is above the immediately previous close. As in the existing rolling-candle detector, a window may include the preceding session. Missing bars inside one session are excluded; the next entry must be the next five-minute candle in the same session. Long-only; no profit target is defined. A 1.0% stop below the entry reference is checked against each subsequent bar's low, with a gap-through stop filled at that bar's open. The time exit is after six entry-forward candles or at session end.

Indicators are causal: EMA, RSI, MACD, and volume moving average use current/past rows only. Entry price, sizing, and stop are formed from the next bar open and fixed risk settings; no later prices affect eligibility or size. Stop/exit checks start at the entry bar after its open. For regime labels, the equal-weight configured-stock basket proxy uses rolling data shifted one full session, so a trade day's regime label uses data available by the previous close.

The validator does not fit parameters. The existing 1% stop, six-bar horizon, 1% risk/trade, 2% daily loss cap, max one open position, max three trades/day, and ₹100,000 starting capital are frozen for this run. Existing research scripts contain prior stop-level sensitivity work; this validation does not repeat a parameter sweep. Calendar-year and regime groupings are descriptive, not training/validation tuning.

## Risk and execution assumptions

- Risk engine: 1% of current simulated capital maximum planned risk per trade; daily loss limit 2%; one open position globally for portfolio; three trades/day; whole-share position sizing and capital exposure capped to account capital.
- Stop: 1.0% below entry; no target.
- Brokerage: Upstox equity intraday ₹20 per executed order or 0.1% of order value, whichever is lower.
- STT: 0.025% sell side; stamp duty: 0.003% buy side; SEBI turnover fee: ₹10/crore per side; GST: 18% on brokerage/exchange/IPFT charges; NSE turnover charges use a conservative fixed 0.00322% per side (the highest published Upstox schedule shown for the period); IPFT estimated at ₹0.10/lakh turnover for this historical interval.
- Slippage: adverse 5 basis points per side, an explicit conservative assumption, not a measured fill spread. Fees use reference turnover; slippage is separately charged on both legs.
- These are estimates; exact contract-note fees, taxes, exchange/plan differences, liquidity, market impact, and stop-order execution are not modeled.
- Published charge schedules: [Upstox brokerage and charges](https://upstox.com/brokerage-charges/) and [NSE statutory charges](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies). The 5 bps/side slippage is an assumption, not a published fee.

## Portfolio results (single shared account, before AI)

| Measure | Result |
|---|---|
| Trades | 46 |
| Win rate (net of estimated costs) | 30.43% |
| Gross P&L before costs | ₹-2,514.17 |
| Estimated costs | ₹7,994.62 |
| Net P&L after costs | ₹-10,508.79 |
| Net return on initial capital | -10.51% |
| Net profit factor | 0.37505236732439134 |
| Closed-equity maximum drawdown | -10.51% |
| Average net P&L/trade | ₹-228.45 |
| Largest net winner | ₹1,633.20 |
| Largest net loser | ₹-1,174.23 |
| Average holding minutes | 22.0 |
| Max consecutive losses / wins | 7 / 2 |
| Maximum capital exposure | ₹99,712.00 (99.71%) |
| Trades per trading day | 0.0926 |
| Trades to recover largest loss at average net winner | 3 |
| Top 1 / top 5 contribution to positive net profits | 25.9% / 73.8% |

**Insufficient sample size for a reliable performance conclusion.**

Portfolio allocation processes same-time signals in the configured basket's fixed symbol order, honoring one global position and account risk caps. Per-stock rows below are standalone counterfactuals with separate ₹100,000 accounts; they must not be summed as one simultaneous portfolio.

## Per-stock standalone results

| Symbol | Trades | Win rate | Gross P&L | Costs | Net P&L | Net PF | Closed DD | Avg net/trade | Largest win | Largest loss | Avg hold min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| RELIANCE | 3 | 66.7% | ₹889.90 | ₹549.54 | ₹340.36 | 1.883681346886743 | -0.38% | ₹113.45 | ₹519.35 | ₹-385.17 | 25.0 |
| HDFCBANK | 4 | 50.0% | ₹-233.00 | ₹726.25 | ₹-959.25 | 0.2033760187266786 | -1.20% | ₹-239.81 | ₹220.14 | ₹-919.32 | 25.0 |
| ICICIBANK | 4 | 25.0% | ₹-437.40 | ₹724.93 | ₹-1,162.33 | 0.1976169848583265 | -1.45% | ₹-290.58 | ₹286.27 | ₹-772.83 | 25.0 |
| INFY | 10 | 40.0% | ₹825.20 | ₹1,806.41 | ₹-981.21 | 0.5803848888656882 | -2.34% | ₹-98.12 | ₹468.59 | ₹-792.31 | 23.5 |
| TCS | 9 | 44.4% | ₹1,381.74 | ₹1,628.04 | ₹-246.30 | 0.886811968583069 | -1.16% | ₹-27.37 | ₹1,762.46 | ₹-1,158.04 | 23.3 |
| SBIN | 1 | 0.0% | ₹-582.80 | ₹182.57 | ₹-765.37 | 0.0 | -0.77% | ₹-765.37 | ₹0.00 | ₹-765.37 | 25.0 |
| BHARTIARTL | 6 | 16.7% | ₹-2,864.74 | ₹1,078.65 | ₹-3,943.39 | 0.03830388547565644 | -4.09% | ₹-657.23 | ₹157.06 | ₹-1,180.02 | 18.3 |
| ITC | 2 | 50.0% | ₹1,741.35 | ₹369.21 | ₹1,372.14 | 6.299428682687365 | -0.25% | ₹686.07 | ₹1,631.07 | ₹-258.92 | 25.0 |
| KOTAKBANK | 2 | 50.0% | ₹965.20 | ₹367.32 | ₹597.88 | 52.01583096296864 | -0.01% | ₹298.94 | ₹609.60 | ₹-11.72 | 25.0 |
| LT | 4 | 0.0% | ₹-2,133.52 | ₹718.76 | ₹-2,852.29 | 0.0 | -2.85% | ₹-713.07 | ₹0.00 | ₹-1,174.23 | 13.8 |
| AXISBANK | 2 | 100.0% | ₹1,096.70 | ₹365.25 | ₹731.45 | Infinity | 0.00% | ₹365.72 | ₹426.86 | ₹0.00 | 25.0 |
| MARUTI | 4 | 0.0% | ₹-602.35 | ₹700.94 | ₹-1,303.29 | 0.0 | -1.30% | ₹-325.82 | ₹0.00 | ₹-811.10 | 25.0 |
| SUNPHARMA | 3 | 0.0% | ₹-1,477.20 | ₹543.79 | ₹-2,021.00 | 0.0 | -2.02% | ₹-673.67 | ₹0.00 | ₹-1,172.28 | 20.0 |
| TITAN | 2 | 0.0% | ₹-909.86 | ₹356.57 | ₹-1,266.43 | 0.0 | -1.27% | ₹-633.22 | ₹0.00 | ₹-1,145.85 | 12.5 |

## Calendar-period results (portfolio)

| Year | Trades | Win rate | Net P&L | Net profit factor |
|---|---|---|---|---|
| 2023 | 2 | 0.0% | ₹-539.38 | 0.0 |
| 2024 | 19 | 26.3% | ₹-5,246.69 | 0.34937001154217573 |
| 2025 | 25 | 36.0% | ₹-4,722.71 | 0.4249057895222742 |

## Market-regime results (portfolio)

The market proxy is the equal-weight daily return of available symbols in the fixed basket, with trend as prior-60-session return (>+5% bullish, <-5% bearish, otherwise sideways); volatility is prior-20-session annualized standard deviation (>=20% high). Labels are lagged by one full session and are descriptive. This is not an official NIFTY index classification.

Regime coverage days: `{"trend": {"bearish": 22, "bullish": 164, "sideways": 250, "warmup": 61}, "volatility": {"high_volatility": 21, "low_volatility": 415, "warmup": 61}}`. Zero-trade categories are included below.

### Trend

| Regime | Trades | Win rate | Net P&L | Net profit factor |
|---|---|---|---|---|
| bearish | 2 | 0.0% | ₹-1,436.32 | 0.0 |
| bullish | 15 | 40.0% | ₹-4,426.69 | 0.24021766713763895 |
| sideways | 27 | 29.6% | ₹-4,106.39 | 0.5444177568772376 |
| warmup | 2 | 0.0% | ₹-539.38 | 0.0 |

### Volatility

| Regime | Trades | Win rate | Net P&L | Net profit factor |
|---|---|---|---|---|
| high_volatility | 0 | 0.0% | ₹0.00 | None |
| low_volatility | 44 | 31.8% | ₹-9,969.40 | 0.38748144633562337 |
| warmup | 2 | 0.0% | ₹-539.38 | 0.0 |

## Signal and trade distribution

- Frozen reversal candidates: 58.
- Risk-accepted quantitative entries: 46.
- Risk-rejected candidates: 12 ({"maximum_open_positions": 12}).
- Data-gap/session candidates excluded: 0 ({}).
- Executed BUY entries after AI: unavailable; these are quantitative/risk-accepted simulated entries before AI.
- AI-rejected candidates: unavailable; AI has no point-in-time historical replay.
- SELL: 0; the frozen strategy is long-only. HOLD cannot be reconstructed as an AI decision historically.
- Most profitable trade concentration and longest win/loss streak are in `summary.json` and `metrics.csv`; longest interval between entries is 120.0 calendar days.

## AI treatment

AI historical replay unavailable/limited. No historical OpenRouter prompts or point-in-time model/provider responses exist, and calling a current model with old candles would introduce temporal leakage. The reported strategy/risk backtest is independent of AI confirmation; actual live signal acceptance may therefore be lower.

## Limitations

- Two years of 5-minute observations across 14 symbols is broad for this local cache but is not sufficient to establish future profitability. The first and final dates are partial coverage; symbols are not guaranteed survivorship-bias-free.
- Three malformed OHLC rows were excluded; four zero-volume candles were retained. Corporate actions, symbol membership changes, delistings, and adjusted price continuity were not independently verified.
- Exit simulation uses OHLC bars; intrabar order of stop crossing versus other prices is unknown. Stop fill, liquidity, queue priority, market impact, and actual spreads are simplified.
- Estimated statutory/broker charges are based on published schedules and may differ from account contract notes. Five bps/side slippage is assumed, not calibrated.
- Closed-trade equity drawdown does not include unrealized intratrade mark-to-market drawdown.
- No AI replay was used. Results show quantitative and risk behavior only.
- No parameter optimization was performed in this run. Historical report groupings are descriptive; no claim is made that the strategy works or will make money live.

## Reproduction and files

Run from `ai-tarding-assistant`:

```powershell
python run_historical_validation.py
```

Optional: `--data PATH`, `--output PATH`, `--capital AMOUNT`, `--stop-pct PERCENT`, `--horizon CANDLES`, `--slippage-bps-per-side BPS`.

Outputs are saved under `backtests\validation`: `summary.json`, `trades.csv`, `metrics.csv`, `signal_counts.csv`, `regime_metrics.csv`, and `dataset_audit.json`.
