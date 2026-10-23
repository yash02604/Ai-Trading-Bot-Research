# Sharp-reversal exploratory experiment v1

**Status: EXPLORATORY ONLY. This is not independent validation, profitability evidence, or paper/live approval.**

## Method and limitations

The parameter set was frozen in the saved experiment specification before this run. The cached CSVs have unresolved acquisition provenance, adjustment status, point-in-time universe membership, and dataset-specific candle-label semantics. Source labels are provisionally treated as candle starts. Every date partition below has appeared in prior project research; the 2025-12-02–2026-10-01 segment was previously evaluated and is not a fresh holdout.

Only dates marked VERIFIED and REGULAR in the checked-in NSE CM manifest v2 are retained. Special, holiday, absent, or unresolved session dates, out-of-session rows, known anomaly dates, and structurally invalid rows are excluded. Same-session gaps invalidate affected feature/entry/holding windows; no bar is filled. The regime is a lagged 14-stock equal-weight basket proxy, not an official index regime.

A symbol-session is included only when it has all 75 expected regular five-minute labels; any incomplete or zero-volume symbol-session is excluded. Same-session gaps invalidate the affected session and indicators restart after missing regular sessions. Entry is next same-session five-minute candle open after the completed signal. Long-only, no target, stop checked from the entry candle onward; gap-through stop fills at open. Exits occur by stop, horizon, or session end. No trade crosses a date. Position sizing uses the existing whole-share risk engine: 1% current equity risk, 100% capital cap; one open position; 2% day-start-equity loss cap; three trades/day. Daily loss accumulates the absolute net losses without offset from winning trades. Costs reuse the existing cost model, including estimated taxes/fees and 5 bps per-side slippage. These are estimates, not measured execution costs.

## Overall results

| Candidate | Trades | Gross P&L (₹) | Costs (₹) | Net P&L (₹) | Win rate | Net PF | Max DD | Avg net/trade (₹) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | 73 | -891.22 | 12454.53 | -13345.75 | 37.0% | 0.40885953611832876 | -13.64% | -182.82 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | 177 | -10654.26 | 27310.45 | -37964.71 | 26.0% | 0.27994778946277804 | -37.96% | -214.49 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | 269 | 1675.32 | 41777.53 | -40102.21 | 34.6% | 0.4298262136531833 | -40.46% | -149.08 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | 55 | -8656.21 | 9238.17 | -17894.38 | 32.7% | 0.33286220769083924 | -19.36% | -325.35 |

## Chronological partitions

Partition labels describe prior reuse, not fresh holdout status.

| Candidate | Partition | Status | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | development_reused | previously examined; exploratory only | 21 | -2038.97 | 3747.10 | -5786.07 | 23.8% | 0.3274665619552204 | -5.79% | -275.53 |
| frozen_reversal_v1 | walk_forward_reused | previously examined; exploratory only | 26 | -356.93 | 4600.44 | -4957.37 | 38.5% | 0.4221725659410483 | -4.96% | -190.67 |
| frozen_reversal_v1 | previous_oos_reused | Stage 9/10 evaluated; not an untouched holdout; partial retained coverage 2025-12-02–2026-07-31; 43 expected regular dates have no retained candles | 26 | 1658.15 | 4658.18 | -3000.03 | 46.2% | 0.533289867523444 | -3.35% | -115.39 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | development_reused | previously examined; exploratory only | 63 | -4244.29 | 10613.90 | -14858.20 | 25.4% | 0.32337996978685407 | -15.61% | -235.84 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | walk_forward_reused | previously examined; exploratory only | 59 | -1721.76 | 10256.22 | -11977.97 | 25.4% | 0.3170375690277141 | -11.98% | -203.02 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | previous_oos_reused | Stage 9/10 evaluated; not an untouched holdout; partial retained coverage 2025-12-02–2026-07-31; 43 expected regular dates have no retained candles | 55 | -6699.05 | 9353.96 | -16053.01 | 27.3% | 0.21392478651267705 | -16.05% | -291.87 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | development_reused | previously examined; exploratory only | 102 | 4421.32 | 17607.64 | -13186.32 | 40.2% | 0.5714892128430815 | -13.19% | -129.28 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | walk_forward_reused | previously examined; exploratory only | 82 | 1005.76 | 14151.76 | -13146.00 | 32.9% | 0.40269627331931684 | -13.20% | -160.32 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | previous_oos_reused | Stage 9/10 evaluated; not an untouched holdout; partial retained coverage 2025-12-02–2026-07-31; 43 expected regular dates have no retained candles | 85 | -4731.53 | 14034.88 | -18766.41 | 31.8% | 0.28444416741509326 | -19.45% | -220.78 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | development_reused | previously examined; exploratory only | 16 | -2044.12 | 2845.46 | -4889.58 | 37.5% | 0.46754214406668204 | -6.50% | -305.60 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | walk_forward_reused | previously examined; exploratory only | 15 | -3855.29 | 2636.80 | -6492.09 | 20.0% | 0.20080423632416453 | -6.49% | -432.81 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | previous_oos_reused | Stage 9/10 evaluated; not an untouched holdout; partial retained coverage 2025-12-02–2026-07-31; 43 expected regular dates have no retained candles | 24 | -3308.59 | 4189.27 | -7497.86 | 37.5% | 0.31948794146933224 | -8.54% | -312.41 |

## By stock

| Candidate | Stock | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | AXISBANK | 4 | 1230.45 | 675.11 | 555.34 | 75.0% | 3.3583914439266627 | -0.23% | 138.84 |
| frozen_reversal_v1 | BHARTIARTL | 9 | -3130.20 | 1539.87 | -4670.06 | 22.2% | 0.10982497653602986 | -4.82% | -518.90 |
| frozen_reversal_v1 | HDFCBANK | 6 | -129.20 | 1038.08 | -1167.28 | 50.0% | 0.2027326116023039 | -1.17% | -194.55 |
| frozen_reversal_v1 | ICICIBANK | 5 | -1205.45 | 865.21 | -2070.66 | 20.0% | 0.1165234988263997 | -2.07% | -414.13 |
| frozen_reversal_v1 | INFY | 8 | 764.70 | 1379.54 | -614.84 | 37.5% | 0.5652490804475413 | -1.29% | -76.86 |
| frozen_reversal_v1 | ITC | 3 | 2383.05 | 521.78 | 1861.27 | 66.7% | 8.671021007007335 | -0.24% | 620.42 |
| frozen_reversal_v1 | KOTAKBANK | 1 | 785.20 | 182.06 | 603.14 | 100.0% | Infinity | 0.00% | 603.14 |
| frozen_reversal_v1 | LT | 7 | -2518.52 | 1212.37 | -3730.89 | 14.3% | 0.03009768291101325 | -3.73% | -532.98 |
| frozen_reversal_v1 | MARUTI | 6 | -1202.60 | 977.38 | -2179.98 | 0.0% | 0.0 | -2.18% | -363.33 |
| frozen_reversal_v1 | RELIANCE | 2 | 182.30 | 343.73 | -161.43 | 50.0% | 0.5405546839808798 | -0.35% | -80.72 |
| frozen_reversal_v1 | SBIN | 2 | -397.70 | 348.40 | -746.10 | 50.0% | 0.01774603558458233 | -0.76% | -373.05 |
| frozen_reversal_v1 | SUNPHARMA | 4 | 83.40 | 679.65 | -596.25 | 25.0% | 0.32885946811981426 | -0.89% | -149.06 |
| frozen_reversal_v1 | TCS | 12 | 1890.84 | 2027.60 | -136.76 | 41.7% | 0.945153051573401 | -1.16% | -11.40 |
| frozen_reversal_v1 | TITAN | 4 | 372.51 | 663.74 | -291.23 | 75.0% | 0.7377268951106365 | -1.11% | -72.81 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | AXISBANK | 11 | 988.49 | 1684.39 | -695.90 | 45.5% | 0.7055915733050305 | -0.87% | -63.26 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | BHARTIARTL | 14 | -1893.29 | 2214.87 | -4108.16 | 28.6% | 0.14107861906968264 | -4.11% | -293.44 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | HDFCBANK | 15 | -1078.84 | 2298.11 | -3376.95 | 20.0% | 0.128268765403056 | -3.40% | -225.13 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | ICICIBANK | 7 | -1137.80 | 1101.76 | -2239.56 | 0.0% | 0.0 | -2.24% | -319.94 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | INFY | 33 | -324.15 | 5059.08 | -5383.23 | 27.3% | 0.41380009265689843 | -6.01% | -163.13 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | ITC | 14 | -2547.57 | 2226.14 | -4773.72 | 14.3% | 0.13610697600410163 | -5.53% | -340.98 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | KOTAKBANK | 9 | -753.05 | 1420.03 | -2173.07 | 22.2% | 0.24216634197037562 | -2.85% | -241.45 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | LT | 12 | 1198.27 | 1922.68 | -724.42 | 41.7% | 0.7921879563410074 | -2.02% | -60.37 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | MARUTI | 13 | -454.85 | 1897.80 | -2352.65 | 30.8% | 0.30905323406984797 | -2.94% | -180.97 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | RELIANCE | 7 | 76.03 | 1082.37 | -1006.34 | 42.9% | 0.37615509614812903 | -1.14% | -143.76 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | SBIN | 4 | -1733.88 | 659.93 | -2393.81 | 0.0% | 0.0 | -2.39% | -598.45 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | SUNPHARMA | 11 | 102.21 | 1735.11 | -1632.91 | 27.3% | 0.49064895667191877 | -1.99% | -148.45 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | TCS | 12 | -425.32 | 1715.55 | -2140.87 | 41.7% | 0.1767084280042129 | -2.33% | -178.41 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | TITAN | 15 | -2670.49 | 2292.63 | -4963.12 | 6.7% | 0.042653924180584286 | -4.96% | -330.87 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | AXISBANK | 24 | 1749.59 | 3875.87 | -2126.28 | 45.8% | 0.6033495469323915 | -2.75% | -88.60 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | BHARTIARTL | 22 | 1213.53 | 3393.28 | -2179.76 | 36.4% | 0.6316047061655049 | -2.92% | -99.08 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | HDFCBANK | 12 | -2393.26 | 1839.03 | -4232.30 | 16.7% | 0.051631764885411505 | -4.23% | -352.69 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | ICICIBANK | 14 | -1580.04 | 2253.33 | -3833.37 | 21.4% | 0.2523224424246921 | -3.96% | -273.81 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | INFY | 34 | -431.36 | 5189.13 | -5620.50 | 29.4% | 0.30905518747722854 | -5.62% | -165.31 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | ITC | 19 | -693.64 | 3045.85 | -3739.49 | 26.3% | 0.38928821654484613 | -4.66% | -196.82 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | KOTAKBANK | 15 | 227.50 | 2279.98 | -2052.48 | 40.0% | 0.46791390895725954 | -2.50% | -136.83 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | LT | 21 | 2483.55 | 3320.91 | -837.36 | 42.9% | 0.8230830796109466 | -2.26% | -39.87 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | MARUTI | 20 | 1693.56 | 3034.91 | -1341.35 | 40.0% | 0.7043787340850586 | -2.85% | -67.07 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | RELIANCE | 15 | -1338.15 | 2417.31 | -3755.46 | 26.7% | 0.14400855009712296 | -3.76% | -250.36 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | SBIN | 7 | 238.05 | 1137.76 | -899.71 | 42.9% | 0.35502694012343894 | -1.30% | -128.53 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | SUNPHARMA | 17 | 22.04 | 2572.93 | -2550.89 | 41.2% | 0.3862515452838496 | -2.63% | -150.05 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | TCS | 23 | -234.42 | 3432.69 | -3667.12 | 39.1% | 0.29125356583012363 | -3.71% | -159.44 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | TITAN | 26 | 718.38 | 3984.53 | -3266.16 | 30.8% | 0.5312611175646859 | -3.59% | -125.62 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | AXISBANK | 5 | -1131.72 | 821.39 | -1953.11 | 40.0% | 0.2937912077333493 | -2.77% | -390.62 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | BHARTIARTL | 5 | 137.98 | 858.12 | -720.14 | 40.0% | 0.6423736031834734 | -1.51% | -144.03 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | HDFCBANK | 3 | -1043.67 | 532.17 | -1575.84 | 33.3% | 0.10267431600187225 | -1.75% | -525.28 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | ICICIBANK | 3 | 715.80 | 508.19 | 207.61 | 33.3% | 1.513416377925019 | -0.40% | 69.20 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | INFY | 15 | -1354.30 | 2533.78 | -3888.08 | 33.3% | 0.46227269720728925 | -5.66% | -259.21 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | ITC | 1 | -103.95 | 175.88 | -279.83 | 0.0% | 0.0 | -0.28% | -279.83 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | KOTAKBANK | 2 | -844.33 | 323.05 | -1167.39 | 0.0% | 0.0 | -1.17% | -583.69 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | LT | 5 | -1172.19 | 858.72 | -2030.91 | 40.0% | 0.3810430839946761 | -2.03% | -406.18 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | MARUTI | 5 | -1476.83 | 780.70 | -2257.53 | 20.0% | 0.0672859250963149 | -2.26% | -451.51 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | SBIN | 1 | -992.17 | 181.21 | -1173.37 | 0.0% | 0.0 | -1.17% | -1173.37 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | SUNPHARMA | 5 | -551.01 | 836.76 | -1387.78 | 40.0% | 0.3807513210512946 | -1.53% | -277.56 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | TCS | 3 | -429.88 | 492.07 | -921.95 | 33.3% | 0.1989575170620692 | -1.15% | -307.32 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | TITAN | 2 | -409.95 | 336.12 | -746.07 | 50.0% | 0.20466663232836851 | -0.94% | -373.04 |

Stock and regime drawdowns are calculated on each subgroup's isolated trade sequence with the same starting capital; they are descriptive and not additive contributions to the portfolio drawdown.

## By market-regime proxy

| Candidate | Dimension | Regime | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen_reversal_v1 | trend_regime | bearish | 14 | -1224.20 | 2317.87 | -3542.07 | 35.7% | 0.24795876865903363 | -3.95% | -253.01 |
| frozen_reversal_v1 | trend_regime | bullish | 18 | -1779.72 | 3112.27 | -4891.99 | 38.9% | 0.21776615602029567 | -5.01% | -271.78 |
| frozen_reversal_v1 | trend_regime | sideways | 39 | 2287.50 | 6659.80 | -4372.30 | 38.5% | 0.6051409223567011 | -5.08% | -112.11 |
| frozen_reversal_v1 | trend_regime | warmup | 2 | -174.80 | 364.58 | -539.38 | 0.0% | 0.0 | -0.54% | -269.69 |
| frozen_reversal_v1 | volatility_regime | high_volatility | 4 | 1597.65 | 660.49 | 937.16 | 100.0% | Infinity | 0.00% | 234.29 |
| frozen_reversal_v1 | volatility_regime | low_volatility | 67 | -2314.07 | 11429.45 | -13743.52 | 34.3% | 0.37634012201877065 | -14.41% | -205.13 |
| frozen_reversal_v1 | volatility_regime | warmup | 2 | -174.80 | 364.58 | -539.38 | 0.0% | 0.0 | -0.54% | -269.69 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend_regime | bearish | 32 | -4940.35 | 4478.80 | -9419.15 | 18.8% | 0.09215401045989763 | -9.42% | -294.35 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend_regime | bullish | 47 | -2452.00 | 7443.75 | -9895.75 | 29.8% | 0.2996182756689849 | -9.90% | -210.55 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend_regime | sideways | 83 | -933.90 | 12737.86 | -13671.76 | 28.9% | 0.3914220353590123 | -13.67% | -164.72 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | trend_regime | warmup | 15 | -2328.00 | 2650.04 | -4978.04 | 13.3% | 0.13507861107512303 | -4.98% | -331.87 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility_regime | high_volatility | 12 | -2177.41 | 1706.76 | -3884.16 | 25.0% | 0.15018466305287861 | -3.88% | -323.68 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility_regime | low_volatility | 161 | -7186.11 | 24885.75 | -32071.87 | 26.7% | 0.3049863241337728 | -32.40% | -199.20 |
| sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1 | volatility_regime | warmup | 4 | -1290.74 | 717.94 | -2008.68 | 0.0% | 0.0 | -2.01% | -502.17 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend_regime | bearish | 46 | -4754.22 | 6319.11 | -11073.33 | 23.9% | 0.22371409779652868 | -11.07% | -240.72 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend_regime | bullish | 70 | -672.92 | 11254.37 | -11927.29 | 35.7% | 0.37927116334277544 | -12.40% | -170.39 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend_regime | sideways | 126 | 3836.90 | 19387.59 | -15550.69 | 35.7% | 0.47926607289287465 | -16.11% | -123.42 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | trend_regime | warmup | 27 | 3265.57 | 4816.46 | -1550.89 | 44.4% | 0.7781522702305038 | -3.23% | -57.44 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility_regime | high_volatility | 15 | -1597.38 | 2100.21 | -3697.59 | 20.0% | 0.23297150932664973 | -3.70% | -246.51 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility_regime | low_volatility | 246 | 4038.05 | 38244.37 | -34206.33 | 35.4% | 0.4509677699392896 | -35.16% | -139.05 |
| sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1 | volatility_regime | warmup | 8 | -765.35 | 1432.95 | -2198.30 | 37.5% | 0.3151084150531214 | -2.62% | -274.79 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend_regime | bearish | 16 | -2437.51 | 2571.19 | -5008.70 | 31.2% | 0.2877115165397851 | -5.24% | -313.04 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend_regime | bullish | 13 | -454.49 | 2259.05 | -2713.54 | 38.5% | 0.5554817371774826 | -3.86% | -208.73 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend_regime | sideways | 25 | -5623.21 | 4226.67 | -9849.88 | 32.0% | 0.26295758550094095 | -10.75% | -394.00 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | trend_regime | warmup | 1 | -141.00 | 181.26 | -322.26 | 0.0% | 0.0 | -0.32% | -322.26 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | volatility_regime | high_volatility | 6 | -970.16 | 981.86 | -1952.02 | 50.0% | 0.3397594192939718 | -1.95% | -325.34 |
| sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1 | volatility_regime | low_volatility | 49 | -7686.05 | 8256.31 | -15942.36 | 30.6% | 0.33200778406623505 | -17.42% | -325.35 |

## Data and reproducibility

- Retained rows: 686250; excluded rows: 49670; residual same-day gaps: 0.
- Complete symbol-sessions retained: 9150; incomplete symbol-sessions excluded: 605; zero-volume rows retained: 0.
- Requested OOS partition retained coverage: 2025-12-02 through 2026-07-31; 43 expected regular dates have no retained candles. Missing dates are listed in `data_exclusions.json`.
- Input and code SHA-256 values, runtime versions, and source commit `9bbce21c9c460a74bf8b0e86183dc6a15f00a7a4` are in `run_manifest.json`.
- Exclusions and per-reason counts are in `data_exclusions.json`; trade-level simulated fills and costs are in `trades.csv`.
- No AI historical decisions were fabricated or replayed. No provider, broker, or acquisition-pilot call was made.
- All candidates remain `RESEARCH_ONLY`; none is approved or enabled for V2 paper execution.

## Conclusion

No research candidate clears the predeclared exploratory screen. No candidate shows sufficient evidence to recommend further investigation after modeled costs; low trade counts, where applicable, make results inconclusive rather than proof that no edge exists.
All results remain exploratory because the source data and periods are reused and provenance remains unresolved.
