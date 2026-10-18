# Candle data integrity policy

`app.candle_integrity` is the shared structural boundary used by indicator
preparation, historical loading, backtesting, and the supported candle merge
paths.

## Hard errors and warnings

Hard errors include missing required candle fields, timestamps that are invalid,
timezone-naive, or not aligned to five-minute boundaries, nonnumeric/nonfinite
OHLCV values, nonpositive OHLC prices, invalid OHLC relationships, negative
volume, and duplicate instrument/timestamp keys. These records cannot pass into
indicators or the legacy backtester. Historical validation retains its existing
explicit-exclusion behavior and records the row/reason in its returned dataset
audit; duplicate keys fail the load instead of being selected or dropped.

Zero-volume bars are valid and retained. They receive a warning. Large volume,
large but structurally valid price changes, and unusual timestamps are not
classified as bad data. Timestamps outside the ordinary 09:15–15:25 candle-label
window and same-date gaps are warnings for calendar/source review only. No
approximate NSE/BSE holiday or special-session calendar is embedded here.

## Merge behavior

`merge_candle_sources` compares OHLCV for each identity key. Identical overlap is
reported under `duplicate_coverage` with the key count, contributing source-file
names, and up to 20 sample keys, then kept once. Any differing OHLCV (or a
conflicting stock label for the same instrument key) raises `CandleDataError`
with each conflicting identity, source names, and differing fields. No winner is
chosen.

The OOS downloader merges only its configured `scripts/data/research` output with
the newly downloaded rows. It does not automatically combine that file with the
separate `data/research` OOS dataset. Combining those datasets requires an
explicit caller-supplied source list and overlap review. The context downloader
likewise merges each individual index output only with that index's new batch.

The standalone collector merge returns a de-duplicated result in canonical
Asia/Kolkata chronological order. Research CSV upserts use conflict-safe merging
only for candle tables; event-result persistence retains its existing behavior.

## Exchange sessions

Parsing/normalization and exchange-session validity are separate. A verified
NSE/BSE session calendar must carry exchange, segment, date, regular and special
session intervals, timezone, source circular/reference, and version. Special
sessions and Muhurat trading cannot be inferred from weekday/holiday heuristics.
Until that source is selected and maintained, session-window warnings are
review candidates and gaps are not auto-filled or dropped.
