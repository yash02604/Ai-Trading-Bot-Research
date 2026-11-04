# Historical Cost Model Audit v2

Status: **PROVISIONAL COST SENSITIVITY — NOT A VERIFIED HISTORICAL BACKTEST**.

This report reprices the already-saved 46 portfolio trades; it does not rerun the strategy or alter strategy parameters. It is not an independent historical validation. The underlying dataset's provenance remains UNRESOLVED (10 findings), and the saved trades are historical outputs from the prior cost model.

## Evidence and limits

- Instrument and order type assumed from project code: Upstox, NSE cash equities, long-only equity intraday, regular market orders modeled at OHLC references. The exact account tariff and actual fills are not in this repository.
- Verified rate windows used where documented: Upstox NSE intraday transaction rate 0.00322% from 2024-04-01 through 2024-09-30 and 0.00297% from 2024-10-01 through 2026-02-28. From 2026-03-01 the NSE's ₹307/crore total each side is represented as ₹306.99/crore transaction charges plus ₹0.01/crore IPFT to avoid double-counting. Pre-2024-04-01 client pricing is unresolved and appears only as three explicit sensitivity proxies.
- STT: 0.025% of sell turnover. Stamp duty: 0.003% of buy turnover. SEBI: ₹10/crore on both sides. NSE IPFT: ₹10/crore through 2026-02-28 and ₹0.01/crore from 2026-03-01.
- Brokerage uses ₹20 or 0.1% per executed order, whichever is lower, as an Upstox schedule assumption. Account-specific historical tariff/contract-note evidence is missing.
- GST is 18% on brokerage, exchange transaction and IPFT charges in the Upstox schedule. The NSE's 2022 circular also instructs GST on SEBI fees; because applicable client invoicing treatment for this period is not evidenced here, both GST-base scenarios are shown.
- Slippage is shown at 0, 5, and 10 bps per side. These are assumptions, not measured fills. No spread, market impact, auction/impact charges, DP fees, or other account-specific charges are included; equity intraday schedule states no DP fee.

Sources: [Upstox charges](https://upstox.com/brokerage-charges/), [Upstox April 2023 rate notice](https://upstox.com/announcements/updates/nse-transaction-charges-and-stt-from-1st-april-2023/), [NSE Circular FA56129](https://nsearchives.nseindia.com/content/circulars/FA56129.pdf), [NSE Circular FA64232](https://nsearchives.nseindia.com/content/circulars/FA64232.pdf), [NSE Circular FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf), [NSE statutory charge summary](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies), and [NSE GST circular 2/2022](https://archives.nseindia.com/content/circulars/FATAX52993.pdf).

## Saved costs versus scenario repricing

The center scenario is only an illustrative sensitivity: archived-tariff proxy 0.00325% for the unresolved pre-April-2024 period, GST excluding SEBI, and 5 bps/side slippage. It must not be treated as a verified corrected baseline.

- Saved old costs: ₹7,994.62; saved old net P&L: ₹-10,508.79.
- Center-scenario costs: ₹7,985.88; scenario net P&L: ₹-10,500.05; cost delta: ₹-8.74.
- Other combinations and itemized per-trade costs are in `scenario_summary.csv` and `trade_repricing.csv`.

Each scenario changes only modeled cost inputs and resulting net P&L. Gross P&L and trades remain exactly those in the saved trade file. No scenario is designated as the verified historical cost baseline, and none is evidence of profitability.

## Reproduction

`python -m scripts.run_cost_model_audit --trades backtests/validation/trades.csv --output backtests/cost_model_audit_v2`

The run writes dataset, saved-result, cost-model, and runner SHA-256 values to `input_hashes.json`; it refuses to overwrite a non-empty output directory. Existing validation reports are not changed.
