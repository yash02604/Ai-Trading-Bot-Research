# Stage 11 Break-Even Analysis

Descriptive arithmetic from the existing Stage 9 accepted trades. This does not retest or change that strategy.

| Metric | Value | Unit |
| --- | ---: | --- |
| stage9_trade_count | 19.0000 | trades |
| average_cost_per_trade | 177.2354 | INR/trade |
| average_gross_win | 356.1182 | INR/winning gross trade |
| average_gross_loss_abs | 489.0150 | INR/losing gross trade |
| gross_win_rate | 57.8947 | percent of trades |
| gross_pnl_per_trade | 0.2726 | INR/trade |
| net_pnl_per_trade | -176.9628 | INR/trade |
| break_even_win_rate_including_average_cost | 78.8338 | percent, using average gross win/loss |
| gross_pnl_per_trade_needed_to_cover_costs | 177.2354 | INR/trade |
| average_gross_move_needed_to_cover_costs | 177.2354 | INR/trade, currency P&L proxy |
| average_capital_exposure | 95,716.0105 | INR/trade |
| average_cost_as_pct_of_exposure | 0.1852 | percent of average deployed capital |
| observed_gross_pnl | 5.1800 | INR |
| observed_costs | 3,367.4725 | INR |
| observed_net_pnl | -3,362.2925 | INR |

The break-even win rate uses the observed average gross win and absolute gross loss, plus average cost per trade. It assumes those conditional averages remain fixed and is not a forecast.

The minimum gross P&L per trade to cover costs is the average cost per trade. It is a currency-P&L threshold, not a price-return threshold; position sizes differ across trades.
