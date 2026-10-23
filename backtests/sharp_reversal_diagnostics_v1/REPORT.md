# Sharp-reversal experiment diagnostics

Read-only analysis of the recorded v1.1 experiment artifacts. These findings do not establish profitability, independent validation, or readiness for paper/live execution.

- Source commit: `a1730dcd2b5684821be8dce7e0b1d7d550e8bc51`
- Trade ledger rows: 574
- Drawdown: recomputed on closed-trade net equity, strategy-specific, starting capital INR 100,000.
- Holding time fields are differences between recorded source labels. Since label semantics remain unresolved, these are not verified wall-clock holding durations.

## Consistent strategy comparison

| Strategy | Trades | Gross ₹ | Costs ₹ | Net ₹ | Avg net ₹ | Net win rate | Net PF | Closed DD | Cost / turnover | Top-5 negative-stock loss share | Gross loss trades | Net loss trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | 73 | -891.22 | 12454.53 | -13345.75 | -182.82 | 37.0% | 0.408859536118329 | -13.64% | 0.094% | 84.4% | 38 | 46 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 177 | -10654.26 | 27310.45 | -37964.71 | -214.49 | 26.0% | 0.2799477894627789 | -37.96% | 0.098% | 59.5% | 103 | 131 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 269 | 1675.32 | 41777.53 | -40102.21 | -149.08 | 34.6% | 0.4298262136531827 | -40.46% | 0.098% | 52.8% | 128 | 176 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 55 | -8656.21 | 9238.17 | -17894.38 | -325.35 | 32.7% | 0.33286220769083913 | -19.36% | 0.094% | 64.7% | 34 | 37 |

## Cost decomposition

Components below are sums of recorded trade-ledger fields; they explain the entire gross-to-net difference if the reconciliation check passes.

| Strategy | Component | Cost ₹ | Share of costs |
|---|---|---:|---:|
| frozen_reversal_v1 | slippage | 6631.34 | 53.2% |
| frozen_reversal_v1 | brokerage | 2920.00 | 23.4% |
| frozen_reversal_v1 | stt | 1657.72 | 13.3% |
| frozen_reversal_v1 | gst | 604.86 | 4.9% |
| frozen_reversal_v1 | exchange | 427.06 | 3.4% |
| frozen_reversal_v1 | stamp_duty | 198.95 | 1.6% |
| frozen_reversal_v1 | ipft | 13.26 | 0.1% |
| frozen_reversal_v1 | sebi | 1.33 | 0.0% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | slippage | 13953.99 | 51.1% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | brokerage | 7080.00 | 25.9% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | stt | 3487.17 | 12.8% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | gst | 1441.18 | 5.3% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | exchange | 898.64 | 3.3% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | stamp_duty | 418.78 | 1.5% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | ipft | 27.91 | 0.1% |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | sebi | 2.79 | 0.0% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | slippage | 21405.55 | 51.2% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | brokerage | 10760.00 | 25.8% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | stt | 5351.60 | 12.8% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | gst | 2192.64 | 5.2% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | exchange | 1378.52 | 3.3% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | stamp_duty | 642.14 | 1.5% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | ipft | 42.81 | 0.1% |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | sebi | 4.28 | 0.0% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | slippage | 4889.85 | 52.9% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | brokerage | 2200.00 | 23.8% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | stt | 1221.38 | 13.2% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | gst | 454.44 | 4.9% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | exchange | 314.91 | 3.4% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | stamp_duty | 146.83 | 1.6% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | ipft | 9.78 | 0.1% |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | sebi | 0.98 | 0.0% |

## Recorded exit and timing dimensions

For each strategy, the following tables summarize the ledger by entry-time label, holding-label difference, and exit reason. The full dimension tables, including stock and regime, are in `findings.json`.

### Entry-time label

| Strategy | Label | Trades | Gross ₹ | Costs ₹ | Net ₹ | Avg net ₹ |
|---|---|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | 09:25 | 38 | 402.25 | 6436.72 | -6034.47 | -158.80 |
| frozen_reversal_v1 | 09:30 | 24 | -3247.35 | 4117.59 | -7364.94 | -306.87 |
| frozen_reversal_v1 | 09:35 | 1 | -913.51 | 170.58 | -1084.09 | -1084.09 |
| frozen_reversal_v1 | 09:40 | 1 | 441.00 | 164.11 | 276.89 | 276.89 |
| frozen_reversal_v1 | 09:50 | 1 | 86.40 | 169.69 | -83.29 | -83.29 |
| frozen_reversal_v1 | 10:25 | 1 | 185.60 | 167.29 | 18.31 | 18.31 |
| frozen_reversal_v1 | 10:45 | 1 | -87.00 | 174.23 | -261.23 | -261.23 |
| frozen_reversal_v1 | 11:15 | 1 | 785.20 | 182.06 | 603.14 | 603.14 |
| frozen_reversal_v1 | 12:20 | 1 | -992.92 | 181.31 | -1174.23 | -1174.23 |
| frozen_reversal_v1 | 12:30 | 1 | 1778.70 | 181.61 | 1597.09 | 1597.09 |
| frozen_reversal_v1 | 14:45 | 1 | 455.80 | 163.64 | 292.16 | 292.16 |
| frozen_reversal_v1 | 15:15 | 2 | 214.60 | 345.69 | -131.09 | -65.54 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:25 | 65 | -1983.65 | 9984.26 | -11967.91 | -184.12 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:30 | 60 | -5035.63 | 9211.11 | -14246.73 | -237.45 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:35 | 8 | -1096.45 | 1213.30 | -2309.76 | -288.72 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:40 | 3 | 555.80 | 434.17 | 121.63 | 40.54 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:45 | 5 | 665.26 | 744.50 | -79.24 | -15.85 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 09:50 | 1 | -465.29 | 131.12 | -596.40 | -596.40 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:00 | 6 | -1548.00 | 918.98 | -2466.98 | -411.16 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:05 | 1 | -674.70 | 168.88 | -843.58 | -843.58 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:10 | 1 | -671.49 | 168.30 | -839.79 | -839.79 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:25 | 3 | 1982.70 | 484.07 | 1498.63 | 499.54 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:45 | 1 | -78.00 | 161.09 | -239.09 | -239.09 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10:55 | 2 | -1196.35 | 343.60 | -1539.95 | -769.97 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:05 | 1 | -671.70 | 168.34 | -840.04 | -840.04 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:10 | 1 | 65.00 | 176.35 | -111.35 | -111.35 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:15 | 1 | 712.40 | 169.56 | 542.84 | 542.84 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:20 | 1 | -548.89 | 146.19 | -695.08 | -695.08 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:25 | 1 | 398.40 | 145.69 | 252.71 | 252.71 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 11:50 | 1 | -219.20 | 180.22 | -399.42 | -399.42 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 12:25 | 2 | 1866.00 | 335.10 | 1530.90 | 765.45 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 12:55 | 2 | -799.45 | 327.41 | -1126.85 | -563.43 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 13:10 | 1 | -720.10 | 177.07 | -897.17 | -897.17 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 13:25 | 1 | -502.14 | 137.76 | -639.90 | -639.90 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 13:40 | 1 | -573.45 | 150.62 | -724.07 | -724.07 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 14:05 | 1 | 516.00 | 146.57 | 369.43 | 369.43 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 14:35 | 1 | -360.50 | 168.12 | -528.62 | -528.62 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 14:40 | 1 | 198.90 | 152.33 | 46.57 | 46.57 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 14:55 | 1 | -130.95 | 177.75 | -308.70 | -308.70 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 15:00 | 1 | -122.50 | 134.76 | -257.26 | -257.26 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 15:15 | 2 | -359.09 | 303.36 | -662.44 | -331.22 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 15:20 | 1 | 142.80 | 149.87 | -7.07 | -7.07 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:25 | 50 | -174.38 | 7771.44 | -7945.82 | -158.92 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:30 | 57 | -2260.13 | 8717.73 | -10977.86 | -192.59 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:35 | 52 | 2783.04 | 7892.78 | -5109.74 | -98.26 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:40 | 11 | 695.25 | 1610.01 | -914.76 | -83.16 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:45 | 6 | 199.99 | 929.85 | -729.86 | -121.64 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:50 | 5 | 514.12 | 709.70 | -195.57 | -39.11 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 09:55 | 5 | 1103.10 | 838.68 | 264.42 | 52.88 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:00 | 8 | -1619.26 | 1253.92 | -2873.19 | -359.15 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:05 | 2 | -54.60 | 346.06 | -400.66 | -200.33 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:10 | 2 | -571.20 | 300.23 | -871.43 | -435.71 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:15 | 1 | 142.60 | 179.71 | -37.11 | -37.11 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:20 | 1 | -174.00 | 145.48 | -319.48 | -319.48 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:25 | 3 | 997.10 | 479.19 | 517.91 | 172.64 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:30 | 2 | -590.80 | 306.82 | -897.62 | -448.81 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:40 | 4 | -710.70 | 586.56 | -1297.26 | -324.31 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:50 | 1 | -943.44 | 174.63 | -1118.07 | -1118.07 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10:55 | 8 | -412.17 | 1315.98 | -1728.15 | -216.02 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:05 | 1 | -935.11 | 173.50 | -1108.61 | -1108.61 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:10 | 2 | 101.50 | 351.97 | -250.47 | -125.24 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:15 | 3 | 55.95 | 485.75 | -429.80 | -143.27 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:20 | 1 | -739.28 | 147.05 | -886.34 | -886.34 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:25 | 1 | 390.10 | 143.64 | 246.46 | 246.46 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:40 | 1 | -972.29 | 178.52 | -1150.81 | -1150.81 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:50 | 2 | 136.30 | 308.08 | -171.78 | -85.89 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 11:55 | 1 | -727.65 | 145.48 | -873.13 | -873.13 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:05 | 2 | 680.10 | 308.19 | 371.91 | 185.95 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:15 | 1 | 107.90 | 123.33 | -15.43 | -15.43 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:20 | 1 | 139.20 | 158.79 | -19.59 | -19.59 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:25 | 2 | 1940.20 | 344.25 | 1595.95 | 797.98 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:35 | 1 | 211.05 | 175.15 | 35.90 | 35.90 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 12:55 | 2 | -1047.13 | 335.19 | -1382.32 | -691.16 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:00 | 2 | -276.50 | 327.29 | -603.79 | -301.90 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:20 | 2 | 794.55 | 334.75 | 459.80 | 229.90 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:25 | 1 | -669.52 | 137.63 | -807.15 | -807.15 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:40 | 1 | -985.06 | 180.25 | -1165.31 | -1165.31 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:45 | 1 | 665.00 | 169.18 | 495.82 | 495.82 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:50 | 1 | 374.40 | 168.14 | 206.26 | 206.26 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 13:55 | 1 | 237.15 | 180.06 | 57.09 | 57.09 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:00 | 1 | 495.00 | 180.99 | 314.01 | 314.01 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:15 | 3 | 1219.45 | 468.84 | 750.61 | 250.20 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:30 | 1 | 256.20 | 132.05 | 124.15 | 124.15 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:35 | 1 | -165.30 | 179.41 | -344.71 | -344.71 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:40 | 2 | 116.25 | 332.03 | -215.78 | -107.89 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:45 | 2 | 1581.00 | 322.77 | 1258.23 | 629.12 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:50 | 2 | 72.40 | 344.04 | -271.64 | -135.82 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 14:55 | 1 | -130.95 | 177.75 | -308.70 | -308.70 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:00 | 2 | 128.85 | 306.59 | -177.74 | -88.87 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:05 | 1 | 500.40 | 136.69 | 363.71 | 363.71 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:10 | 1 | 657.90 | 140.24 | 517.66 | 517.66 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:15 | 2 | -485.71 | 303.03 | -788.73 | -394.37 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:20 | 1 | -189.00 | 164.69 | -353.69 | -353.69 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15:25 | 1 | -786.55 | 153.44 | -939.99 | -939.99 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 09:25 | 27 | -4893.05 | 4528.49 | -9421.54 | -348.95 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 09:30 | 20 | -3322.62 | 3335.97 | -6658.59 | -332.93 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 09:35 | 1 | 361.20 | 180.89 | 180.31 | 180.31 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 11:05 | 1 | -974.62 | 178.84 | -1153.45 | -1153.45 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 12:05 | 1 | -971.60 | 178.43 | -1150.03 | -1150.03 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 12:25 | 2 | 1988.80 | 353.72 | 1635.08 | 817.54 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 13:40 | 1 | -873.83 | 165.22 | -1039.05 | -1039.05 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 14:40 | 1 | 299.30 | 157.71 | 141.59 | 141.59 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 15:15 | 1 | -269.80 | 158.90 | -428.70 | -428.70 |

### Largest negative stock aggregates

| Strategy | Stock | Trades | Gross ₹ | Costs ₹ | Net ₹ |
|---|---|---:|---:|---:|---:|
| frozen_reversal_v1 | BHARTIARTL | 9 | -3130.20 | 1539.87 | -4670.06 |
| frozen_reversal_v1 | LT | 7 | -2518.52 | 1212.37 | -3730.89 |
| frozen_reversal_v1 | MARUTI | 6 | -1202.60 | 977.38 | -2179.98 |
| frozen_reversal_v1 | ICICIBANK | 5 | -1205.45 | 865.21 | -2070.66 |
| frozen_reversal_v1 | HDFCBANK | 6 | -129.20 | 1038.08 | -1167.28 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | INFY | 33 | -324.15 | 5059.08 | -5383.23 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | TITAN | 15 | -2670.49 | 2292.63 | -4963.12 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | ITC | 14 | -2547.57 | 2226.14 | -4773.72 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | BHARTIARTL | 14 | -1893.29 | 2214.87 | -4108.16 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | HDFCBANK | 15 | -1078.84 | 2298.11 | -3376.95 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | INFY | 34 | -431.36 | 5189.13 | -5620.50 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | HDFCBANK | 12 | -2393.26 | 1839.03 | -4232.30 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | ICICIBANK | 14 | -1580.04 | 2253.33 | -3833.37 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | RELIANCE | 15 | -1338.15 | 2417.31 | -3755.46 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | ITC | 19 | -693.63 | 3045.85 | -3739.49 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | INFY | 15 | -1354.30 | 2533.78 | -3888.08 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | MARUTI | 5 | -1476.83 | 780.70 | -2257.53 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | LT | 5 | -1172.19 | 858.72 | -2030.91 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | AXISBANK | 5 | -1131.72 | 821.39 | -1953.11 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | HDFCBANK | 3 | -1043.67 | 532.17 | -1575.84 |

### Net by regime label

| Strategy | Dimension | Regime | Trades | Gross ₹ | Costs ₹ | Net ₹ |
|---|---|---|---:|---:|---:|---:|
| frozen_reversal_v1 | trend | bearish | 14 | -1224.20 | 2317.87 | -3542.07 |
| frozen_reversal_v1 | trend | bullish | 18 | -1779.72 | 3112.27 | -4891.99 |
| frozen_reversal_v1 | trend | sideways | 39 | 2287.50 | 6659.80 | -4372.30 |
| frozen_reversal_v1 | trend | warmup | 2 | -174.80 | 364.58 | -539.38 |
| frozen_reversal_v1 | volatility | high_volatility | 4 | 1597.65 | 660.49 | 937.16 |
| frozen_reversal_v1 | volatility | low_volatility | 67 | -2314.07 | 11429.45 | -13743.52 |
| frozen_reversal_v1 | volatility | warmup | 2 | -174.80 | 364.58 | -539.38 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend | bearish | 32 | -4940.35 | 4478.80 | -9419.15 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend | bullish | 47 | -2452.00 | 7443.75 | -9895.75 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend | sideways | 83 | -933.90 | 12737.86 | -13671.76 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend | warmup | 15 | -2328.00 | 2650.04 | -4978.04 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility | high_volatility | 12 | -2177.41 | 1706.76 | -3884.16 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility | low_volatility | 161 | -7186.11 | 24885.75 | -32071.87 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility | warmup | 4 | -1290.74 | 717.94 | -2008.68 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend | bearish | 46 | -4754.22 | 6319.11 | -11073.33 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend | bullish | 70 | -672.92 | 11254.37 | -11927.29 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend | sideways | 126 | 3836.89 | 19387.59 | -15550.69 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend | warmup | 27 | 3265.57 | 4816.46 | -1550.89 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility | high_volatility | 15 | -1597.38 | 2100.21 | -3697.59 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility | low_volatility | 246 | 4038.05 | 38244.37 | -34206.33 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility | warmup | 8 | -765.34 | 1432.95 | -2198.30 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend | bearish | 16 | -2437.51 | 2571.19 | -5008.70 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend | bullish | 13 | -454.49 | 2259.05 | -2713.54 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend | sideways | 25 | -5623.21 | 4226.67 | -9849.88 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend | warmup | 1 | -141.00 | 181.26 | -322.26 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | volatility | high_volatility | 6 | -970.15 | 981.86 | -1952.02 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | volatility | low_volatility | 49 | -7686.05 | 8256.31 | -15942.36 |

### Holding-label difference

| Strategy | Label difference min | Trades | Gross ₹ | Costs ₹ | Net ₹ |
|---|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | 0 | 3 | -2878.54 | 530.39 | -3408.94 |
| frozen_reversal_v1 | 10 | 4 | -1677.57 | 695.66 | -2373.22 |
| frozen_reversal_v1 | 25 | 65 | 4624.15 | 11051.71 | -6427.56 |
| frozen_reversal_v1 | 5 | 1 | -959.26 | 176.76 | -1136.02 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 0 | 3 | -1835.23 | 472.58 | -2307.81 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 10 | 6 | -2840.28 | 939.64 | -3779.92 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 15 | 3 | -1728.24 | 453.29 | -2181.53 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 20 | 3 | -2063.84 | 513.82 | -2577.66 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 25 | 147 | 5505.99 | 22707.32 | -17201.33 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 5 | 15 | -7692.66 | 2223.80 | -9916.45 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 0 | 2 | -1721.66 | 326.94 | -2048.59 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 10 | 5 | -2712.60 | 745.40 | -3458.01 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 15 | 7 | -3553.43 | 992.25 | -4545.68 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 20 | 8 | -5578.86 | 1288.19 | -6867.06 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 25 | 244 | 17009.59 | 37952.43 | -20942.84 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 5 | 3 | -1767.72 | 472.32 | -2240.04 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 0 | 2 | -1946.22 | 357.27 | -2303.48 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 10 | 2 | -1091.23 | 317.04 | -1408.28 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 15 | 3 | -2634.05 | 497.37 | -3131.42 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 20 | 1 | -835.13 | 160.00 | -995.13 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 30 | 2 | -1800.94 | 337.65 | -2138.59 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 35 | 2 | -1757.09 | 331.72 | -2088.82 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 45 | 1 | 299.30 | 157.71 | 141.59 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 5 | 3 | -2747.63 | 512.71 | -3260.35 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 50 | 1 | -845.39 | 161.38 | -1006.78 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 55 | 38 | 4702.19 | 6405.32 | -1703.13 |

### Exit reason

| Strategy | Reason | Trades | Gross ₹ | Costs ₹ | Net ₹ |
|---|---|---:|---:|---:|---:|
| frozen_reversal_v1 | horizon_exit | 65 | 4624.15 | 11051.71 | -6427.56 |
| frozen_reversal_v1 | session_exit | 2 | 214.60 | 345.69 | -131.09 |
| frozen_reversal_v1 | stop_exit | 6 | -5729.97 | 1057.12 | -6787.09 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | horizon_exit | 140 | 9750.70 | 21611.38 | -11860.68 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | session_exit | 2 | 264.40 | 319.33 | -54.93 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | stop_exit | 35 | -20669.36 | 5379.74 | -26049.09 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | horizon_exit | 243 | 17674.45 | 37815.43 | -20140.98 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | session_exit | 4 | 1094.70 | 614.91 | 479.79 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | stop_exit | 22 | -17093.83 | 3347.19 | -20441.03 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | horizon_exit | 37 | 5694.35 | 6224.11 | -529.76 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | session_exit | 2 | 29.50 | 316.60 | -287.10 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | stop_exit | 16 | -14380.06 | 2697.46 | -17077.51 |

## Reconciliation and risk reconstruction

- Row-level checks found 0 accounting/timestamp/stop/duplicate-key errors across the recorded ledger. Detailed nonzero counts: `{}`.
- All-observed, per-stock, and per-regime reported metrics were independently reconciled against the trade ledger; material deltas: 0. See `findings.json` for exact per-field deltas.
- Partition trade counts can be checked against all-observed fills. Partition cash metrics cannot: the experiment reruns each partition from the starting capital, changing path-dependent quantities and costs, while `trades.csv` contains only all-observed fills. The partition metric cash differences are retained in `findings.json` as non-comparable, not labeled accounting errors.
- Daily risk, position sizing, and one-position-at-a-time checks were reconstructed from accepted fills and the experiment's stated starting capital/rules. This cannot establish how every rejected signal was handled because rejected signals are not a trade ledger dimension.
- `frozen_reversal_v1` reconstructed risk violations: `{}`.
- `sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1` reconstructed risk violations: `{}`.
- `sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1` reconstructed risk violations: `{}`.
- `sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1` reconstructed risk violations: `{}`.

## Findings

- **Verified:** The four strategy counts sum to the 574 trade rows; recomputed net P&L, cost totals, average net trade, win rate, net profit factor, closed-equity drawdown, and stock/regime totals are derived consistently from the ledger. (Exact aggregate reconciliation is in `findings.json`.)
- **Verified:** Recorded entry labels are exactly five minutes after signal labels for all ledger trades. This supports next-label entry sequencing, not actual source timestamp semantics or executable fill availability.
- **Verified:** Recorded `total_cost` equals the sum of itemized components and `net_pnl = gross_pnl - total_cost` within INR 0.011 for all rows, if no row-level errors are reported.
- **Verified:** Brokerage and slippage dominate modeled costs; slippage is the largest aggregate component for every strategy. It is a modeling assumption, not observed fill slippage.
- **Verified:** The original chronological partition report reinitializes capital independently per partition, so those cash metrics are not directly additive and cannot be reconciled to the single all-observed trade ledger; there is no partition-specific trade ledger to inspect.
- **Suspected:** With the experiment's provisional START-label assumption, the ledger's `exit_timestamp` is a bar label, not the actual time an intrabar stop or candle-close exit could execute. Under that assumption, holding-label differences understate elapsed duration by one five-minute interval for close-at-bar-end exits and cannot time intrabar stops.
- **Suspected:** Entries at the same candle's open and stops checked against that candle's low are plausible, but the OHLC record cannot order low versus any other intrabar event. Stop-only policy removes target ambiguity, yet stop fill timing and gaps within the bar remain unobservable.
- **Verified in implementation:** `simulate` enforces one open position with a separate `last_exit` comparison, but calls the shared risk engine without setting `RiskState.open_positions` (default zero). Current accepted ledger rows reconstruct without overlaps; the reusable risk gate itself would not enforce this limit unless the caller supplies open-position state.
- **Unassessable:** Underlying feed authenticity, adjustment/corporate-action treatment, true timestamp convention, measured costs/slippage, point-in-time universe, and genuinely untouched performance remain unavailable from these artifacts.
- **Unassessable:** The basket regime is a proxy and is represented by labels rather than daily constituent values in the trade ledger; its computation can be code-reviewed but cannot be independently recomputed from the experiment output alone.

## Priority fixes before another strategy experiment

1. Establish source-backed candle timestamp semantics and execution timestamps; explicitly model bar-open entry to bar-close/stop execution rather than treating labels as fills in time.
2. Obtain traceable, adjustment-aware historical data and a reviewed point-in-time universe; the present diagnostics cannot resolve those foundational uncertainties.
3. Validate transaction-cost/slippage assumptions against an authorized source or conservative sensitivity analysis; estimated slippage alone is the largest cost line.
4. Add a persisted signal/rejection ledger and reconstruct event-level daily risk decisions, because the accepted-trade ledger cannot audit rejected or skipped signals.

## Source artifact SHA-256

- `REPORT.md`: `8585a2349c6d3a827f4e5562dc81d5b8b17d0888a09560d955779f9a2f6e8544`
- `experiment_spec.json`: `1db09e8193d98b93a70582695f7374f260edd10c16a191248d6551338b4eee9d`
- `metrics_by_partition.csv`: `64d88db30457d09de1bba8249f412a38211b7129d5330266c40e107b3309ee7a`
- `metrics_by_regime.csv`: `8b2bb4a6c7509a8f802a8115ceeacc78a49654bf837ba66f0780aeaa29bb9396`
- `metrics_by_symbol.csv`: `41ed3ba9d43f6a0440712d90d17901b111536e85e349c158538fcbfa1a6a2656`
- `run_manifest.json`: `e4e39843b5aa3b7b434ca1706c01884ddf10a4fb399529f2e9a2e4648b369900`
- `trades.csv`: `1ad90590d44fbdaf55ab7ef4be45831a0bd6a3f450dee5ab480b25724f031ede`

No experiment outputs, strategies, manifests, datasets, or acquisition configuration were modified.
