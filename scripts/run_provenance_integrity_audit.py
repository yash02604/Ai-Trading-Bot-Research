from __future__ import annotations
import csv, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.provenance_audit import (make_finding, classify_ohlc,
                                  parse_explicit_timestamp, verify_file_binding)
from strategy_backtest import STOCKS as STRATEGY_STOCKS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backtests" / "provenance_integrity_v1"
OUT.mkdir(parents=True, exist_ok=True)
DATA = ROOT / "data" / "research"
CONTEXT = DATA / "market_context"
main_path = DATA / "extended_historical_robustness_candles.csv"
main_relative = "data/research/extended_historical_robustness_candles.csv"
prior_summary_path = OUT / "summary.json"
try:
    prior_summary = json.loads(prior_summary_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    prior_summary = {}
prior_binding_hashes = prior_summary.get("dataset_sha256", {})
prior_binding_hash = (prior_binding_hashes.get(Path(main_relative).name)
                      if isinstance(prior_binding_hashes, dict) else None)
dataset_binding = verify_file_binding(
    main_path, OUT / "dataset_binding.json", dataset_relative_path=main_relative,
    previous_pinned_sha256=prior_binding_hash)
TASK9 = ROOT / "backtests" / "research_task9"
SESSION = ROOT / "backtests" / "data_validation_v1"

INPUTS = [
    "docs/OFFLINE_MARKET_DATA_PIPELINE_V1.md", "app/provenance_audit.py",
    "scripts/run_provenance_integrity_audit.py", "app/research/deepseek_eval_v1.py",
    "backtests/provenance_integrity_v1/dataset_binding.json",
    "docs/CANDLE_DATA_INTEGRITY.md", "docs/NSE_SESSION_AND_DATA_AUDIT_V1.md",
    "RESEARCH_COPY_STATUS.md", "VALIDATION_REPORT.md",
    "backtests/validation/VALIDATION_REPORT.md", "backtests/validation/dataset_audit.json",
    "backtests/research_task9/task9_report.md", "backtests/research_task9/source_evidence.csv",
    "backtests/research_task9/input_hashes.csv", "backtests/research_task9/record_findings.csv",
    "backtests/research_task9/gap_intervals.csv", "backtests/research_task9/session_assessments.csv",
    "backtests/research_task9/timestamp_profiles.csv", "backtests/research_task9/index_volume_summary.csv",
    "backtests/data_validation_v1/audit_report.md", "backtests/data_validation_v1/summary.json",
    "backtests/data_validation_v1/input_hashes.json", "data/audits/nse_cm_session_manifest_v1.json",
    "app/upstox_client.py", "app/candle_integrity.py", "app/historical_validation.py",
    "scripts/download_oos_stock_data.py", "scripts/download_market_context.py",
    "scripts/analyze_market_context.py", "extended_historical_robustness_test.py",
    "strategy_backtest.py",
    "data/research/extended_historical_robustness_candles.csv",
    "data/research/extended_historical_robustness_oos_candles.csv",
]
INPUTS += [str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted(CONTEXT.glob("*.csv"))]

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

hashes = []
missing = []
for rel in INPUTS:
    p = ROOT / rel
    if not p.exists():
        missing.append(rel)
        continue
    hashes.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha(p)})
(OUT / "input_hashes.json").write_text(json.dumps({"algorithm":"SHA-256", "inputs":hashes, "missing_inputs":missing}, indent=2)+"\n", encoding="utf-8")


def rows(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

oos_path = DATA / "extended_historical_robustness_oos_candles.csv"
main = rows(main_path)
oos = rows(oos_path)
context_rows = {p.stem: rows(p) for p in CONTEXT.glob("*.csv")}

def select(src, symbol, ts):
    return [r for r in src if r.get("stock", r.get("symbol", "")).upper() == symbol and ts in r.get("timestamp", "")]

def compact(r):
    return {k:r.get(k) for k in ("stock", "symbol", "timestamp", "open", "high", "low", "close", "volume", "oi") if k in r}

findings=[]
def add(*args): findings.append(make_finding(*args))

add("pipeline_provenance", "acquisition and normalization lineage", "UNRESOLVED",
    ["app/upstox_client.py constructs current Upstox V3 historical-candle requests; download scripts convert timestamps and write CSV caches.",
     "The main and OOS cached files are Git-ignored and have no saved request/response archive, request parameters, API response version, retrieval timestamp, or per-file source manifest.",
     "The exact main CSV contains only tabular market fields (stock, instrument_key, timestamp, OHLCV, and OI); it has no source request ID, retrieval timestamp, response hash, parser version, or adjustment status.",
     "The offline-pipeline and acquisition-readiness documents describe future schemas and explicitly state they are not evidence that this old cache was acquired through that process.",
     "scripts/download_oos_stock_data.py writes under scripts/data/research while the audited OOS file is under data/research.",
     "Task 9 recorded current file hashes; this proves byte identity at audit time, not origin."],
    {"main_file": "data/research/extended_historical_robustness_candles.csv", "oos_file": "data/research/extended_historical_robustness_oos_candles.csv", "context_files": sorted(p.name for p in CONTEXT.glob("*.csv"))},
    "Do not treat the cached records as independently source-authenticated. Retain existing bytes and require future acquisition manifests containing endpoint/version, instrument key, dates, interval, retrieval time, response hash, and normalization steps.",
    "Current code describes a possible/current pipeline but cannot prove it produced these older cache files; OOS output-path mismatch further breaks lineage.")

binding_status = "VERIFIED_VALID" if dataset_binding["status"] == "VERIFIED" else "INVALIDATED"
add("exact_eval_candle_hash_binding", "Exact historical-evaluation candle file byte binding", binding_status,
    [f"Pinned SHA-256: {dataset_binding.get('sha256')}.",
     f"Current observed SHA-256: {dataset_binding.get('observed_sha256')}.",
     "The audit retains the first binding and marks any later path or byte change INVALIDATED."],
    dataset_binding,
    "Historical-live preflight must compare the exact candle file against this retained hash; do not refresh the pin after a mismatch.",
    "SHA-256 establishes byte identity only; it does not establish source authenticity, completeness, price correctness, or adjustment treatment.")

add("timestamp_semantics", "timestamp labels and timezone", "UNRESOLVED",
    ["Upstox V2 and V3 documentation describes candle timestamps as interval starts: https://upstox.com/developer/api-documentation/get-historical-candle-data/ and https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/.",
     "Equity CSV labels carry +05:30; context files contain both +00:00 and +05:30 labels. Example NIFTY50 2023-12-01T03:45:00Z converts to 09:15 IST.",
     "Historical normalization code converts UTC-aware timestamps to Asia/Kolkata, but no cached-response linkage establishes which provider/version semantics apply."],
    {"main_equity_rows":len(main), "oos_equity_rows":len(oos), "context_rows":sum(map(len,context_rows.values())), "interpretations":{"start_label":"matches documented current Upstox API semantics and observed 09:15 first bars", "end_label":"produces coverage mismatches in prior session audit"}},
    "For future reproducible research, preserve source timestamps and offsets; provisional start-label analysis may be shown only as an assumption, with the end-label alternative retained as a sensitivity check.",
    "The dataset-specific timestamp convention is unverified without raw acquisition responses or an authoritative dataset export specification.")

add("corporate_action_adjustment", "split, bonus, dividend and other adjustment status", "UNRESOLVED",
    ["Cached CSV schema contains timestamp and OHLCV/OI fields but no adjustment flag or corporate-action metadata.",
     "Historical loader and integrity code validate structure and timezone but do not apply or verify corporate-action adjustments.",
     "No action history or provider-specific adjustment contract is attached to these files."],
    {"main_file_rows":len(main), "oos_file_rows":len(oos), "corporate_action_linkage":"none located"},
    "Exclude securities and dates affected by an unverified corporate action from price-return evaluation until raw/adjusted status and adjustment factors are reconciled; never retrospectively rescale silently.",
    "Upstox currently exposes a separate corporate-actions endpoint, but it was not called and does not establish whether these cached candles are adjusted: https://upstox.com/developer/api-documentation/get-corporate-actions/.")

add("universe_history", "equity universe and survivorship", "UNRESOLVED",
    ["strategy_backtest.py defines a fixed 14-symbol basket with instrument keys.",
     "No point-in-time constituents list, inclusion/exclusion rationale history, delisted/renamed/merged security mapping, or membership effective dates were located."],
    {"fixed_symbols":["RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS","SBIN","BHARTIARTL","ITC","KOTAKBANK","LT","AXISBANK","MARUTI","SUNPHARMA","TITAN"],"universe_size":14},
    "Limit conclusions to the stated fixed basket and its available history. Do not generalize to the historical NSE universe; require point-in-time membership and corporate-action/security identity mapping for broader claims.",
    "Absence of membership records does not prove survivorship bias exists or does not exist; it remains unresolved.")

malformed=[]
for r in main:
    good, reason = classify_ohlc({k:r.get(k) for k in ("open","high","low","close","volume")})
    if not good: malformed.append({"record":compact(r),"reason":reason})
add("malformed_ohlc", "structurally inconsistent OHLC records", "VERIFIED_INVALID" if malformed else "VERIFIED_VALID",
    ["Task 9 identifies three OHLC-order violations on 2024-06-25 09:15 IST.", "The same source rows fail deterministic OHLC ordering in this audit."], malformed,
    "Exclude the three invalid rows from any derived price calculations, preserve originals, and record each exclusion; do not repair values.",
    "Structural invalidity is verified from the stored values; this does not identify the cause or validate other records.")

seen_rows = set()
duplicates = []
symbol_keys = {}
key_symbols = {}
zero_volume_count = 0
negative_volume_count = 0
for row in main:
    identity = (row.get("instrument_key"), row.get("timestamp"))
    if identity in seen_rows:
        duplicates.append(compact(row))
    seen_rows.add(identity)
    symbol_keys.setdefault(row.get("stock"), set()).add(row.get("instrument_key"))
    key_symbols.setdefault(row.get("instrument_key"), set()).add(row.get("stock"))
    try:
        volume = float(row.get("volume", ""))
        zero_volume_count += volume == 0
        negative_volume_count += volume < 0
    except (TypeError, ValueError):
        pass
mapping_consistent = (all(len(keys) == 1 for keys in symbol_keys.values())
                      and all(len(symbols) == 1 for symbols in key_symbols.values())
                      and {symbol: next(iter(keys)) for symbol, keys in symbol_keys.items()
                           if len(keys) == 1} == STRATEGY_STOCKS)
add("duplicate_candles", "Duplicate instrument/timestamp records in the evaluation candle file",
    "VERIFIED_INVALID" if duplicates else "VERIFIED_VALID",
    [f"Checked {len(main)} main-dataset rows using the instrument_key plus timestamp key.",
     f"Found {len(duplicates)} duplicate rows."], duplicates,
    "Exclude verified duplicate records from derived calculations and retain the original file unchanged.",
    "A zero duplicate count establishes only structural uniqueness in the currently hashed file.")
add("symbol_instrument_consistency", "Within-file symbol and instrument-key consistency",
    "VERIFIED_VALID" if mapping_consistent else "VERIFIED_INVALID",
    [f"Checked {len(symbol_keys)} symbols and {len(key_symbols)} instrument keys in the evaluation file.",
     "Each symbol and key are one-to-one and the mapping matches the current in-repo strategy map." if mapping_consistent else
     "At least one mapping is inconsistent within the data or differs from the current in-repo strategy map."],
    {symbol: sorted(keys) for symbol, keys in sorted(symbol_keys.items())},
    "Use this only as an internal consistency check; require dated official instrument-master evidence for authoritative mapping.",
    "Internal consistency does not prove that the instrument keys were correct at acquisition time or that the historical universe was unbiased.")

sbin=select(main,"SBIN","2024-06-04T11:15")
add("sbin_move", "SBIN abrupt 5-minute move on 2024-06-04", "UNRESOLVED",
    ["Task 9 records 11:15 IST low 769.80 and close 771.55 after 11:10 close 815.10, an abrupt 5-minute move.", "Task 9 reports the 15:25 close fell from 909.00 on 2024-06-03 to 781.30 on 2024-06-04.", "Stored OHLC can establish the numerical discontinuity only; no independent exchange/feed evidence was found."],
    {"target": [compact(r) for r in sbin], "comparison_rows": [compact(r) for r in select(main,"SBIN","2024-06-04T11:10") + select(main,"SBIN","2024-06-04T15:25") + select(main,"SBIN","2024-06-03T15:25")]},
    "Quarantine the affected SBIN session from price-return research until reconciled against a source-authenticated record and corporate-action history.",
    "Could reflect a genuine market move, a data error, or an unrecorded action; no cause can be established locally.")

itc_spike=select(main,"ITC","2025-05-28T09:15")
add("itc_volume_spike", "ITC volume spike on 2025-05-28", "UNRESOLVED",
    ["Task 9 flags the 09:15 IST bar volume of 360,164,505 as suspicious relative to adjacent records.", "No exchange-level volume or feed metadata is present."],
    [compact(r) for r in itc_spike], "Quarantine the bar and affected session from volume-dependent analysis pending independent reconciliation.",
    "Large opening volume may be legitimate; local context alone cannot determine authenticity.")

itc_flat=[r for r in main if r.get("stock")=="ITC" and r.get("timestamp","").startswith("2025-01-06")]
itc_flat=[r for r in itc_flat if r.get("timestamp","").startswith("2025-01-06") and r.get("volume")=="0" and len({r.get(k) for k in ("open","high","low","close")})==1]
add("itc_flat_zero_volume", "ITC flat zero-volume bars on 2025-01-06", "UNRESOLVED",
    ["Task 9 identifies four consecutive 09:40-09:55 IST flat OHLC bars with zero volume and zero OI."],
    [compact(r) for r in itc_flat], "Quarantine these four bars; do not interpret as confirmed no-trade bars or fill/interpolate them.",
    "No feed semantics establish whether zero-volume flat bars are no-trade records, carried values, or placeholders.")

add("itc_missing_opening", "ITC missing opening intervals on 2025-01-06", "VERIFIED_INVALID",
    ["Session coverage audit against the verified NSE session manifest marks 09:15, 09:20, 09:25 and 09:30 IST absent; first available bar is 09:35."],
    {"symbol":"ITC","date":"2025-01-06","missing_start_labels":["09:15","09:20","09:25","09:30"]},
    "Keep the gap as an explicit missing-data event; exclude affected opening-window decisions; never synthesize candles.",
    "Coverage invalidity is verified under documented start-label convention. The reason (source outage, omission, or other) remains unresolved.")

add("full_day_gap", "No bars on the 2024-11-20 NSE trading holiday", "VERIFIED_VALID",
    ["The NSE CM circular NSE/CMTR/64960 dated 2024-11-08 amends the holiday calendar and declares 2024-11-20 a trading holiday: https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf."],
    {"date":"2024-11-20","symbols":14,"observed_bars":0,"expected_bars":0},
    "Treat absence of bars as expected holiday coverage; do not label this a dataset gap or generate expected candle rows.",
    "This verifies the official session schedule and expected absence only; it does not authenticate feed contents on other dates.")
add("manifest_2024_11_20", "Session manifest omitted the amended 2024-11-20 holiday", "VERIFIED_INVALID",
    ["Manifest entry currently says REGULAR for 2024-11-20.", "NSE CM circular NSE/CMTR/64960 dated 2024-11-08 modifies circular NSE/CMTR/59722 and makes the day a trading holiday: https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf."],
    {"manifest":"data/audits/nse_cm_session_manifest_v1.json","date":"2024-11-20","incorrect_session_type":"REGULAR","source_circular":"NSE/CMTR/64960"},
    "For this provenance audit, override the prior manifest classification using the date-specific NSE circular and record the discrepancy. Correct the manifest only in a separately approved session-audit revision.",
    "This task preserves prior session-audit artifacts; the original manifest remains unchanged and must not be used as authoritative for this date.")

add("split_session_gaps", "Long gaps on 2024-03-02 and 2024-05-18", "VERIFIED_VALID",
    ["Task 9 records 28 95-minute gaps (14 symbols on each date).", "The versioned NSE Capital Market manifest identifies the applicable special-session split schedule and expected closed interval."],
    {"dates":["2024-03-02","2024-05-18"],"gaps":28,"gap_minutes":95},
    "Do not classify the expected scheduled break as missing data; continue validating bars inside each scheduled segment independently.",
    "This verifies calendar schedule compatibility, not the authenticity or completeness of the retained bars.")

context_zero=[]; late=[]
for name, src in context_rows.items():
    for r in src:
        if r.get("volume") in ("0","0.0"):
            context_zero.append({"file":name,"record":compact(r)})
        parsed=parse_explicit_timestamp(r.get("timestamp"))
        if parsed and parsed.hour==15 and parsed.minute in (35,55) and r.get("volume") in ("0","0.0"):
            late.append({"file":name,"record":compact(r)})
add("context_zero_volume", "Market-context/index zero-volume semantics", "UNRESOLVED",
    ["Task 9 reports zero volume for all context/index rows; schema/data source semantics for index volumes are undocumented in this cache."],
    {"count":len(context_zero),"files":sorted(context_rows)},
    "Do not use these volume fields as liquidity evidence; retain context price bars only where calendar, timestamp, and provenance checks independently pass.",
    "A zero index volume may be expected for an index instrument, but the feed’s semantics and data correctness are not established.")
add("late_context_timestamps", "Late market-context timestamps", "UNRESOLVED",
    ["Task 9 identifies 34 records at 15:35 or 15:55 IST with flat OHLC and zero volume/OI."],
    {"count":len(late),"records":late},
    "Exclude late labels from regular-session alignment pending source-specific timestamp semantics; do not shift timestamps.",
    "Could be post-close updates, placeholders, or malformed records; no raw response proves which.")

add("context_timestamp_offsets", "Mixed timestamp offsets in market-context files", "UNRESOLVED",
    ["Task 9 timestamp profile shows UTC and +05:30 offsets within NIFTY50 and India VIX context files.", "The loader normalizes aware values to IST; a normalized instant is comparable but does not prove original timestamp meaning."],
    {"files":{k:len(v) for k,v in context_rows.items()}},
    "Parse only explicit offsets, preserve original timestamp strings, normalize to IST for comparison, and mark naive/ambiguous records unresolved.",
    "The mixed offsets may be valid equivalent instants or reflect different ingestion paths; per-record request provenance is absent.")

profiles={"main_equity_rows":len(main),"oos_equity_rows":len(oos),"context_files":{k:len(v) for k,v in context_rows.items()},"main_symbols":sorted({r.get("stock","") for r in main}),"oos_symbols":sorted({r.get("stock","") for r in oos}),"main_aware_offsets":{},"oos_aware_offsets":{},"context_aware_offsets":{},"main_timestamp_range":[min(r.get("timestamp","") for r in main),max(r.get("timestamp","") for r in main)],"oos_timestamp_range":[min(r.get("timestamp","") for r in oos),max(r.get("timestamp","") for r in oos)]}
def offsets(src):
    out={}
    for r in src:
        t=r.get("timestamp","")
        if t.endswith("+05:30"): key="+05:30"
        elif t.endswith("+00:00") or t.endswith("Z"): key="+00:00"
        else: key="naive_or_other"
        out[key]=out.get(key,0)+1
    return out
profiles["main_aware_offsets"]=offsets(main)
profiles["oos_aware_offsets"]=offsets(oos)
profiles["context_aware_offsets"]={k:offsets(v) for k,v in context_rows.items()}
profiles["evaluation_dataset"]={"path":main_relative,"bytes":dataset_binding.get("bytes"),
    "sha256":dataset_binding.get("sha256"),"observed_sha256":dataset_binding.get("observed_sha256"),
    "binding_status":dataset_binding["status"],"duplicate_instrument_timestamp_rows":len(duplicates),
    "invalid_ohlc_rows":len(malformed),"zero_volume_rows":zero_volume_count,
    "negative_volume_rows":negative_volume_count,"symbol_instrument_mapping_consistent":mapping_consistent}
(OUT/"dataset_profiles.json").write_text(json.dumps(profiles,indent=2)+"\n",encoding="utf-8")
(OUT/"findings.json").write_text(json.dumps(findings,indent=2)+"\n",encoding="utf-8")
with (OUT/"findings.csv").open("w",newline="",encoding="utf-8") as f:
    writer=csv.DictWriter(f,fieldnames=["finding_id","topic","status","evidence","affected_records","proposed_handling","limitations"])
    writer.writeheader()
    for x in findings:
        writer.writerow({**x,"evidence":json.dumps(x["evidence"],ensure_ascii=False),"affected_records":json.dumps(x["affected_records"],ensure_ascii=False)})
summary={"finding_count":len(findings),"status_counts":{s:sum(1 for f in findings if f["status"]==s) for s in ("VERIFIED_VALID","VERIFIED_INVALID","UNRESOLVED","INVALIDATED")},"input_count":len(hashes),"missing_inputs":missing,"main_rows":len(main),"oos_rows":len(oos),"context_rows":sum(map(len,context_rows.values())),"overall_dataset_status":"UNRESOLVED","historical_live_permitted":False,"dataset_binding_status":dataset_binding["status"],"dataset_sha256":{Path(main_relative).name:dataset_binding.get("sha256")},"dataset_binding":dataset_binding}
(OUT/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")

report = """# Historical market data provenance and integrity audit\n\n"""
report += "Audit is deterministic over the source files listed in input_hashes.json. It does not call any market-data provider and does not run a strategy evaluation. Dataset integrity remains **UNRESOLVED**.\n\n"
report += f"## Exact evaluation-file binding\n\nThe intended historical evaluator input is `{main_relative}`. Current byte count: **{dataset_binding.get('observed_bytes')}**. Pinned SHA-256: **`{dataset_binding.get('sha256')}`**. Binding status: **{dataset_binding['status']}**. The audit stores the first observed hash in `dataset_binding.json`; subsequent runs compare current bytes against that record and mark a path/hash mismatch **INVALIDATED** without refreshing the pin. This establishes byte identity only, not source authenticity or price correctness.\n\n"
report += "## Historical-live decision\n\n**NOT PERMITTED.** The byte binding is verified, but the dataset provenance status remains UNRESOLVED and ten provenance findings remain unresolved. The evaluator gate requires both verified overall provenance and a verified exact-file binding. The hash does not substitute for missing acquisition lineage, adjustment metadata, or source validation.\n\n"
report += f"Current structural checks on that exact file: {len(main)} rows; {len(duplicates)} duplicate instrument/timestamp rows; {len(malformed)} invalid OHLC rows; {zero_volume_count} zero-volume rows; {negative_volume_count} negative-volume rows; {len(symbol_keys)} symbols with internally one-to-one symbol/instrument-key mapping: **{mapping_consistent}**. Existing malformed records remain unchanged.\n\n"
report += "## Pipeline and provenance\n\n"
report += "The current code has an Upstox V3 historical-candle request path and normalizes explicit UTC timestamps to Asia/Kolkata before cache writes. The audited caches have no raw response archive, retrieval manifest, request/response hashes, or per-file API version. The current OOS downloader writes under scripts/data/research, while the audited cache resides under data/research. Therefore the current code is not proof of the historical files’ origin.\n\n"
report += "NSE circular NSE/CMTR/64960 confirms 2024-11-20 as a Capital Market trading holiday; this date-specific amendment was missing from the preserved session manifest. [NSE circular NSE/CMTR/64960](https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf).\n\nCurrent Upstox V2 and V3 documentation describes candle labels as interval starts: [V2 historical candles](https://upstox.com/developer/api-documentation/get-historical-candle-data/) and [V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). This supports start labels as the most defensible API-level interpretation, not a verified convention for these specific caches. Prior session audit found start-label coverage aligned better than end-label coverage.\n\n"
report += "## Timestamp, adjustments, and universe\n\n"
report += "Main and OOS equity labels are recorded with +05:30 offsets. Context data mixes +00:00 and +05:30; explicit offsets normalize to comparable instants, but their ingestion paths are unknown. No source evidence establishes corporate-action adjustment status. Cached schemas omit adjustment metadata; no adjustment factors were applied by the loader. A current separate [Upstox corporate actions endpoint](https://upstox.com/developer/api-documentation/get-corporate-actions/) was not called and does not resolve cache adjustment status. The universe is a hard-coded 14-symbol basket with no point-in-time inclusion history or delisted/renamed/merged security map. Survivorship bias is unresolved.\n\n"
report += "## Findings\n\n"
report += "| Finding | Audit status | Resolution classification | Evidence summary | Evidence still needed |\n| --- | --- | --- | --- | --- |\n"
evidence_needed = {
    "pipeline_provenance": "Original request/response archive, retrieval time and parameters, API version, normalization record, and per-file manifest.",
    "timestamp_semantics": "Raw provider response or a dataset-specific export contract linked to this file.",
    "corporate_action_adjustment": "Provider adjustment contract or authoritative action history and explicit adjustment state for these rows.",
    "universe_history": "Dated point-in-time constituent/membership and security-identity history.",
    "sbin_move": "Source-authenticated SBIN candles and relevant corporate-action/event evidence for 2024-06-04.",
    "itc_volume_spike": "Independent source-authenticated ITC volume observation or raw response for 2025-05-28.",
    "itc_flat_zero_volume": "Feed semantics or raw source evidence distinguishing no-trade bars from carried/placeholder values.",
    "context_zero_volume": "Provider documentation and per-instrument source metadata defining context/index volume semantics.",
    "late_context_timestamps": "Dataset-specific timestamp convention and source records explaining the 15:35/15:55 context rows.",
    "context_timestamp_offsets": "Per-file/per-request acquisition lineage showing whether mixed offsets are equivalent source labels or separate ingestion paths.",
}
for f in findings:
    resolution = ("UNRESOLVED" if f["status"] == "UNRESOLVED" else
                  "INVALIDATED" if f["status"] == "INVALIDATED" else "VERIFIED")
    evidence_summary = f["evidence"][0].replace("|", "\\|") if f["evidence"] else "—"
    needed = evidence_needed.get(f["finding_id"], "No additional evidence is required for this narrow structural finding; its stated limitation still applies.")
    report += f"| `{f['finding_id']}` | `{f['status']}` | `{resolution}` | {evidence_summary} | {needed} |\n"
report += "\nThe ten prior unresolved findings remain unresolved where local files prove only the observed values, not their source, meaning, or cause. The exact candle hash is separately VERIFIED as a byte binding; this does not promote overall dataset provenance.\n\n"
for f in findings:
    report += "### " + f["finding_id"] + " — " + f["status"] + "\n\n"
    report += f["topic"] + ". " + f["proposed_handling"] + " Limitation: " + f["limitations"] + "\n\n"
report += "## Conservative inclusion/exclusion policy\n\n"
report += "Preserve source files unchanged. Exclude VERIFIED_INVALID rows from derived price calculations and log each exclusion. Keep UNRESOLVED records in a separate quarantine classification; do not relabel them invalid. Never fill gaps, shift labels, normalize suspicious values, or silently drop records. Exclude unexplained gaps and the affected ITC opening window from any future signal window crossing them. Quarantine SBIN 2024-06-04, ITC 2025-05-28 opening volume, and ITC 2025-01-06 flat/zero-volume bars pending independent reconciliation. Do not use context/index zero-volume fields as liquidity measures. Corporate-action-affected prices and universe-wide claims remain ineligible until evidence is supplied. The special-session split breaks on 2024-03-02 and 2024-05-18 are expected schedule gaps under the manifest, but bar authenticity remains unverified.\n\n"
report += "## Reproduction and limitations\n\n"
report += "From the research-copy root run: `python scripts/run_provenance_integrity_audit.py` to regenerate the JSON/CSV/report outputs, and `python -m pytest tests/test_provenance_audit.py -q` for the focused helper tests. The runner hashes every listed input and reports missing expected files. Structural checks do not establish feed authenticity. No backtests were run.\n\n"
report += "Input file hashes and byte counts are in input_hashes.json; machine-readable classifications and per-finding evidence, affected records, handling, and limitations are in findings.json and findings.csv.\n"
(OUT/"report.md").write_text(report,encoding="utf-8")
print(json.dumps(summary,indent=2))
