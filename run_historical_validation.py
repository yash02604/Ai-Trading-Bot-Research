"""Run reproducible multi-stock, cost-adjusted validation on local candles."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

import pandas as pd

from app.cost_model import CostAssumptions
from app.historical_validation import (
    DEFAULT_DATA,
    add_trade_regimes,
    build_market_regimes,
    calculate_metrics,
    load_historical_data,
    prepare_symbol_frames,
    simulate_book,
)
from strategy_backtest import STOCKS


def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if hasattr(value, "item"):
        return _json_value(value.item())
    if pd.isna(value) if not isinstance(value, (dict, list, tuple)) else False:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _markdown_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _metrics_row(scope, name, metrics):
    row = {"scope": scope, "name": name}
    for key, value in metrics.items():
        row[key] = json.dumps(_json_value(value), ensure_ascii=False) if isinstance(value, (dict, list)) else _json_value(value)
    return row


def build_report(audit, basket, portfolio_metrics, per_symbol, period_metrics, trend_metrics, vol_metrics, regime_day_counts, signal_counts, cost_assumptions, output):
    initial_capital = portfolio_metrics["initial_capital"]
    by_stock = []
    for symbol in STOCKS:
        metric = per_symbol.get(symbol, {})
        by_stock.append([
            symbol, metric.get("total_trades", 0), f"{metric.get('win_rate', 0)*100:.1f}%",
            f"₹{metric.get('gross_pnl', 0):,.2f}", f"₹{metric.get('total_estimated_costs', 0):,.2f}",
            f"₹{metric.get('net_pnl', 0):,.2f}",
            str(metric.get("profit_factor")), f"{metric.get('maximum_drawdown_pct', 0):.2f}%",
            f"₹{metric.get('average_pnl_per_trade', 0):,.2f}",
            f"₹{metric.get('largest_winner', 0):,.2f}", f"₹{metric.get('largest_loser', 0):,.2f}",
            f"{metric.get('average_holding_minutes', 0):.1f}",
        ])
    years = [[year, m["total_trades"], f"{m['win_rate']*100:.1f}%", f"₹{m['net_pnl']:,.2f}", f"{m['profit_factor']}"] for year, m in sorted(period_metrics.items())]
    trend_rows = [[regime, m["total_trades"], f"{m['win_rate']*100:.1f}%", f"₹{m['net_pnl']:,.2f}", str(m["profit_factor"])] for regime, m in sorted(trend_metrics.items())]
    vol_rows = [[regime, m["total_trades"], f"{m['win_rate']*100:.1f}%", f"₹{m['net_pnl']:,.2f}", str(m["profit_factor"])] for regime, m in sorted(vol_metrics.items())]
    sample_note = "**Insufficient sample size for a reliable performance conclusion.**" if portfolio_metrics["total_trades"] < 100 else "The observed sample remains historical and does not establish future performance."
    largest_loss_recovery = portfolio_metrics.get("trades_to_recover_largest_loss_at_average_winner")
    report = f'''# Historical Validation Report

Generated: {datetime.now().astimezone().isoformat()}

## Dataset

- Source: `{audit['source']}` (locally cached Upstox historical candles; no synthetic data).
- Fixed basket: {len(audit['symbols'])} configured liquid Indian equities across banking/financials, IT, consumer, telecom, auto, pharma, energy, and industrials: {', '.join(audit['symbols'])}.
- Period: {audit['start']} to {audit['end']}.
- Timeframe: 5-minute candles.
- Raw rows: {audit['raw_rows']:,}; usable after invalid-row exclusion: {audit['valid_rows']:,}; invalid rows excluded: {audit['invalid_rows_excluded']}.
- Duplicate symbol/timestamp rows: {audit['duplicate_rows']}; zero-volume candles retained: {audit['zero_volume_rows_retained']}.
- Same-day timestamp gaps over five minutes: {audit['same_day_gaps_over_5m']}. Frozen signal windows and entry bars crossing such a gap are excluded; positions use observed OHLC bars until session close/exit.
- Missing symbols from the configured basket: {', '.join(sorted(set(STOCKS) - set(audit['symbols']))) or 'none'}.
- Data quality exclusions (timestamp and reason only): `{json.dumps(audit['invalid_row_details'], ensure_ascii=False)}`.

### Candles per symbol

{_markdown_table(['Symbol','Candles'], [[s, audit['candles_by_symbol'].get(s, 0)] for s in audit['symbols']])}

## Strategy and method

The strategy rules are unchanged: on a completed candle, close is 1.0% inclusive to 2.0% exclusive below the close three observation candles earlier; RSI(14) is 20 inclusive to 25 exclusive; and the current close is above the immediately previous close. As in the existing rolling-candle detector, a window may include the preceding session. Missing bars inside one session are excluded; the next entry must be the next five-minute candle in the same session. Long-only; no profit target is defined. A 1.0% stop below the entry reference is checked against each subsequent bar's low, with a gap-through stop filled at that bar's open. The time exit is after six entry-forward candles or at session end.

Indicators are causal: EMA, RSI, MACD, and volume moving average use current/past rows only. Entry price, sizing, and stop are formed from the next bar open and fixed risk settings; no later prices affect eligibility or size. Stop/exit checks start at the entry bar after its open. For regime labels, the equal-weight configured-stock basket proxy uses rolling data shifted one full session, so a trade day's regime label uses data available by the previous close.

The validator does not fit parameters. The existing 1% stop, six-bar horizon, 1% risk/trade, 2% daily loss cap, max one open position, max three trades/day, and ₹{initial_capital:,.0f} starting capital are frozen for this run. Existing research scripts contain prior stop-level sensitivity work; this validation does not repeat a parameter sweep. Calendar-year and regime groupings are descriptive, not training/validation tuning.

## Risk and execution assumptions

- Risk engine: 1% of current simulated capital maximum planned risk per trade; daily loss limit 2%; one open position globally for portfolio; three trades/day; whole-share position sizing and capital exposure capped to account capital.
- Stop: 1.0% below entry; no target.
- Brokerage: Upstox equity intraday ₹20 per executed order or 0.1% of order value, whichever is lower.
- STT: 0.025% sell side; stamp duty: 0.003% buy side; SEBI turnover fee: ₹10/crore per side. GST defaults to 18% on brokerage/exchange/IPFT charges, following the Upstox schedule; a separate option includes SEBI fees in the GST base because an NSE circular also addresses GST on those fees. NSE transaction and IPFT rates are date-dependent in the calculator. Dates before 2024-04-01 fail closed until client transaction pricing is evidenced. Brokerage and 5 bps/side slippage remain assumptions without account-specific historical contract notes or fill data.
- Slippage: adverse 5 basis points per side, an explicit conservative assumption, not a measured fill spread. Fees use reference turnover; slippage is separately charged on both legs.
- These are estimates; exact contract-note fees, taxes, exchange/plan differences, liquidity, market impact, and stop-order execution are not modeled.
- Published charge schedules: [Upstox brokerage and charges](https://upstox.com/brokerage-charges/) and [NSE statutory charges](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies). The 5 bps/side slippage is an assumption, not a published fee.

## Portfolio results (single shared account, before AI)

{_markdown_table(['Measure','Result'], [
    ['Trades', portfolio_metrics['total_trades']],
    ['Win rate (net of estimated costs)', f"{portfolio_metrics['win_rate']*100:.2f}%"],
    ['Gross P&L before costs', f"₹{portfolio_metrics['gross_pnl']:,.2f}"],
    ['Estimated costs', f"₹{portfolio_metrics['total_estimated_costs']:,.2f}"],
    ['Net P&L after costs', f"₹{portfolio_metrics['net_pnl']:,.2f}"],
    ['Net return on initial capital', f"{portfolio_metrics['net_return_pct']:.2f}%"],
    ['Net profit factor', portfolio_metrics['profit_factor']],
    ['Closed-equity maximum drawdown', f"{portfolio_metrics['maximum_drawdown_pct']:.2f}%"],
    ['Average net P&L/trade', f"₹{portfolio_metrics['average_pnl_per_trade']:,.2f}"],
    ['Largest net winner', f"₹{portfolio_metrics['largest_winner']:,.2f}"],
    ['Largest net loser', f"₹{portfolio_metrics['largest_loser']:,.2f}"],
    ['Average holding minutes', f"{portfolio_metrics['average_holding_minutes']:.1f}"],
    ['Max consecutive losses / wins', f"{portfolio_metrics['maximum_consecutive_losses']} / {portfolio_metrics['maximum_consecutive_wins']}"],
    ['Maximum capital exposure', f"₹{portfolio_metrics['maximum_capital_exposure']:,.2f} ({portfolio_metrics['maximum_capital_exposure_pct']:.2f}%)"],
    ['Trades per trading day', f"{portfolio_metrics['trades_per_trading_day']:.4f}"],
    ['Trades to recover largest loss at average net winner', largest_loss_recovery],
    ['Top 1 / top 5 contribution to positive net profits', f"{portfolio_metrics['top_1_trade_share_of_net_profit_pct']:.1f}% / {portfolio_metrics['top_5_trades_share_of_net_profit_pct']:.1f}%"],
])}

{sample_note}

Portfolio allocation processes same-time signals in the configured basket's fixed symbol order, honoring one global position and account risk caps. Per-stock rows below are standalone counterfactuals with separate ₹{initial_capital:,.0f} accounts; they must not be summed as one simultaneous portfolio.

## Per-stock standalone results

{_markdown_table(['Symbol','Trades','Win rate','Gross P&L','Costs','Net P&L','Net PF','Closed DD','Avg net/trade','Largest win','Largest loss','Avg hold min'], by_stock)}

## Calendar-period results (portfolio)

{_markdown_table(['Year','Trades','Win rate','Net P&L','Net profit factor'], years)}

## Market-regime results (portfolio)

The market proxy is the equal-weight daily return of available symbols in the fixed basket, with trend as prior-60-session return (>+5% bullish, <-5% bearish, otherwise sideways); volatility is prior-20-session annualized standard deviation (>=20% high). Labels are lagged by one full session and are descriptive. This is not an official NIFTY index classification.

Regime coverage days: `{json.dumps(regime_day_counts, sort_keys=True)}`. Zero-trade categories are included below.

### Trend

{_markdown_table(['Regime','Trades','Win rate','Net P&L','Net profit factor'], trend_rows)}

### Volatility

{_markdown_table(['Regime','Trades','Win rate','Net P&L','Net profit factor'], vol_rows)}

## Signal and trade distribution

- Frozen reversal candidates: {signal_counts['frozen_reversal_signals']}.
- Risk-accepted quantitative entries: {signal_counts['risk_accepted_entries']}.
- Risk-rejected candidates: {signal_counts['risk_rejected_signals']} ({json.dumps(signal_counts['risk_rejection_reasons'])}).
- Data-gap/session candidates excluded: {signal_counts['data_rejected_signals']} ({json.dumps(signal_counts['data_rejection_reasons'])}).
- Executed BUY entries after AI: unavailable; these are quantitative/risk-accepted simulated entries before AI.
- AI-rejected candidates: unavailable; AI has no point-in-time historical replay.
- SELL: 0; the frozen strategy is long-only. HOLD cannot be reconstructed as an AI decision historically.
- Most profitable trade concentration and longest win/loss streak are in `summary.json` and `metrics.csv`; longest interval between entries is {portfolio_metrics['longest_gap_between_entries_days']} calendar days.

## AI treatment

AI historical replay unavailable/limited. No historical AI-provider prompts or point-in-time model/provider responses exist, and calling a current model with old candles would introduce temporal leakage. The reported strategy/risk backtest is independent of AI confirmation; actual live signal acceptance may therefore be lower.

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

Outputs are saved under `{output}`: `summary.json`, `trades.csv`, `metrics.csv`, `signal_counts.csv`, `regime_metrics.csv`, and `dataset_audit.json`.
'''
    return report


def run(data_path=DEFAULT_DATA, output_dir=Path("backtests/validation"), capital=100_000.0, stop_pct=1.0, horizon=6, slippage_bps=5.0):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data, audit = load_historical_data(data_path)
    frames = prepare_symbol_frames(data)
    missing = sorted(set(STOCKS) - set(frames))
    if missing:
        raise ValueError(f"Configured symbols have insufficient data: {missing}")
    costs = CostAssumptions(slippage_bps_per_side=slippage_bps)
    regimes = build_market_regimes(data)
    portfolio_trades, portfolio_metrics, portfolio_signals = simulate_book(
        frames, capital, stop_pct, horizon, costs, "portfolio"
    )
    add_trade_regimes(portfolio_trades, regimes)
    per_symbol_metrics, per_symbol_signals, standalone_trades = {}, {}, []
    for symbol, frame in frames.items():
        trades, metrics, signals = simulate_book(
            {symbol: frame}, capital, stop_pct, horizon, costs, f"standalone:{symbol}"
        )
        add_trade_regimes(trades, regimes)
        per_symbol_metrics[symbol] = metrics
        per_symbol_signals[symbol] = signals["per_symbol"].get(symbol, {})
        standalone_trades.extend(trades)
    trading_days = len(set(data.timestamp.dt.date))
    period_metrics = {}
    for year in sorted(data.timestamp.dt.year.unique()):
        selected = [t for t in portfolio_trades if pd.Timestamp(t["entry_timestamp"]).year == year]
        period_metrics[str(year)] = calculate_metrics(selected, capital, trading_days, book=f"portfolio:{year}")
    trend_metrics = {}
    vol_metrics = {}
    for group, result in (("trend_regime", trend_metrics), ("volatility_regime", vol_metrics)):
        if portfolio_trades:
            frame = pd.DataFrame(portfolio_trades)
            for name, subset in frame.groupby(group):
                result[str(name)] = calculate_metrics(subset.to_dict("records"), capital, trading_days, book=f"portfolio:{group}:{name}")
    trend_days = regimes.trend_regime.value_counts().to_dict()
    vol_days = regimes.volatility_regime.value_counts().to_dict()
    regime_day_counts = {
        "trend": {str(k): int(v) for k, v in trend_days.items()},
        "volatility": {str(k): int(v) for k, v in vol_days.items()},
    }
    for name in ("bullish", "bearish", "sideways", "warmup"):
        trend_metrics.setdefault(name, calculate_metrics([], capital, trading_days, book=f"portfolio:trend_regime:{name}"))
    for name in ("high_volatility", "low_volatility", "warmup"):
        vol_metrics.setdefault(name, calculate_metrics([], capital, trading_days, book=f"portfolio:volatility_regime:{name}"))
    all_trades = portfolio_trades + standalone_trades
    all_signal_rows = [{"book": "portfolio", "symbol": "ALL", **{k: v for k, v in portfolio_signals.items() if k != "per_symbol"}}]
    for symbol in frames:
        all_signal_rows.append({"book": "standalone", "symbol": symbol, **per_symbol_signals[symbol]})
    metrics_rows = [_metrics_row("portfolio", "ALL", portfolio_metrics)]
    metrics_rows.extend(_metrics_row("standalone_symbol", s, m) for s, m in per_symbol_metrics.items())
    metrics_rows.extend(_metrics_row("calendar_year", y, m) for y, m in period_metrics.items())
    metrics_rows.extend(_metrics_row("trend_regime", y, m) for y, m in trend_metrics.items())
    metrics_rows.extend(_metrics_row("volatility_regime", y, m) for y, m in vol_metrics.items())
    pd.DataFrame(all_trades).to_csv(output_dir / "trades.csv", index=False)
    pd.DataFrame(metrics_rows).to_csv(output_dir / "metrics.csv", index=False)
    pd.DataFrame(all_signal_rows).to_csv(output_dir / "signal_counts.csv", index=False)
    regime_rows = (
        [_metrics_row("trend", k, v) for k, v in trend_metrics.items()]
        + [_metrics_row("volatility", k, v) for k, v in vol_metrics.items()]
    )
    pd.DataFrame(regime_rows).to_csv(output_dir / "regime_metrics.csv", index=False)
    audit["candles_by_symbol"] = audit["candles_by_symbol"]
    (output_dir / "dataset_audit.json").write_text(json.dumps(_json_value(audit), indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": audit,
        "strategy": {
            "decline_candles": 3, "decline_min_pct_inclusive": 1.0, "decline_max_pct_exclusive": 2.0,
            "rsi_min_inclusive": 20.0, "rsi_max_exclusive": 25.0,
            "reversal_condition": "completed close > previous close",
            "entry": "next consecutive 5-minute candle open", "stop_pct": stop_pct,
            "horizon_entry_forward_candles": horizon, "target": None,
            "parameter_optimization_performed": False,
        },
        "risk": {"initial_capital": capital, "risk_per_trade_pct": 1.0, "daily_loss_limit_pct": 2.0,
                 "max_open_positions": 1, "max_trades_per_day": 3, "position_sizing": "whole shares, risk and capital limited"},
        "cost_assumptions": asdict(costs),
        "portfolio_metrics": portfolio_metrics,
        "per_symbol_standalone_metrics": per_symbol_metrics,
        "calendar_year_metrics": period_metrics,
        "trend_regime_metrics": trend_metrics,
        "volatility_regime_metrics": vol_metrics,
        "regime_day_counts": regime_day_counts,
        "signals": portfolio_signals,
        "ai_historical_replay": "unavailable/limited; quantitative results are independent of AI",
        "real_orders": "disabled; none placed",
    }
    (output_dir / "summary.json").write_text(json.dumps(_json_value(summary), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    report = build_report(audit, data, portfolio_metrics, per_symbol_metrics, period_metrics, trend_metrics, vol_metrics, regime_day_counts, portfolio_signals, costs, output_dir)
    (output_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    # Root-level copy requested in the brief for convenient discovery.
    (Path(__file__).resolve().parent / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    return summary, output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=Path("backtests/validation"))
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--stop-pct", type=float, default=1.0)
    parser.add_argument("--horizon", type=int, default=6)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    args = parser.parse_args()
    summary, output = run(args.data, args.output, args.capital, args.stop_pct, args.horizon, args.slippage_bps_per_side)
    metrics = summary["portfolio_metrics"]
    print(f"Saved validation outputs to {output}")
    print(f"Stocks: {len(summary['dataset']['symbols'])}; candles: {summary['dataset']['valid_rows']}; trades: {metrics['total_trades']}")
    print(f"Gross P&L: Rs {metrics['gross_pnl']:.2f}; estimated costs: Rs {metrics['total_estimated_costs']:.2f}; net P&L: Rs {metrics['net_pnl']:.2f}")
    print(f"Win rate: {metrics['win_rate']*100:.2f}%; profit factor: {metrics['profit_factor']}; closed drawdown: {metrics['maximum_drawdown_pct']:.2f}%")
    print("AI historical replay: unavailable; no broker orders placed")
