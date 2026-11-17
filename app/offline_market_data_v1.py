"""Offline, versioned schemas and validators for reproducible market-data research.

This module has no network client and never reads credentials. Validation is
structural/session validation; it does not authenticate provider data.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

PIPELINE_SCHEMA_VERSION = 1
SESSION_MANIFEST_SCHEMA_VERSION = 2
SESSION_MANIFEST_VERSION = "nse-cm-2023-12-01_2026-10-01-v2"
CANDLE_SCHEMA_VERSION = 1
CLASSIFICATIONS = {"VERIFIED_VALID", "VERIFIED_INVALID", "UNRESOLVED"}
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SECRET_KEY = re.compile(r"(?:token|secret|authorization|password|credential|api[_-]?key|client[_-]?secret)", re.I)
BEARER = re.compile(r"\bBearer\s+\S+", re.I)


def sha256_bytes(content: bytes) -> str:
    return sha256(content).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256_bytes(payload)




def write_acquisition_manifest_immutable(directory: str | Path, record: dict[str, Any]) -> Path:
    """Store a validated acquisition record once; conflicting reuse of its ID fails."""
    validate_acquisition_manifest(record)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = sha256_bytes(record["acquisition_request_id"].encode("utf-8")) + ".json"
    target = directory / name
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + bytes([10])
    if target.exists():
        if target.read_bytes() == payload:
            return target
        raise FileExistsError("acquisition request ID already has a different immutable record")
    with target.open("xb") as stream:
        stream.write(payload)
    return target
def parse_explicit_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError as exc:
        raise ValueError("timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must contain an explicit UTC offset")
    return parsed


def timestamp_offset(value: str) -> str:
    parse_explicit_timestamp(value)
    match = re.search(r"(Z|z|[+-][0-9]{2}:?[0-9]{2})$", value)
    if not match:
        raise ValueError("timestamp offset is missing")
    return "Z" if match.group(1).upper() == "Z" else match.group(1)


def normalize_timestamp(value: str) -> tuple[str, str, str]:
    parsed = parse_explicit_timestamp(value)
    utc_value = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    ist_value = parsed.astimezone(IST).isoformat()
    return value, utc_value, ist_value


def sanitize_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme.lower() != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError("endpoint must be an HTTPS URL without user info")
    # Query parameters are excluded entirely; even innocently named query values
    # are not needed because all request parameters have their own redacted map.
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def sanitize_parameters(parameters: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(parameters, dict):
        raise ValueError("request parameters must be an object")
    clean: dict[str, Any] = {}
    redacted: list[str] = []
    for key, value in parameters.items():
        if SECRET_KEY.search(str(key)):
            redacted.append(str(key))
            continue
        if isinstance(value, str) and BEARER.search(value):
            redacted.append(str(key))
            continue
        if isinstance(value, dict):
            nested, nested_redacted = sanitize_parameters(value)
            clean[str(key)] = nested
            redacted.extend(f"{key}.{name}" for name in nested_redacted)
        elif isinstance(value, list):
            safe_items = []
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    nested, nested_redacted = sanitize_parameters(item)
                    safe_items.append(nested)
                    redacted.extend(f"{key}[{index}].{name}" for name in nested_redacted)
                elif isinstance(item, str) and BEARER.search(item):
                    safe_items.append("[REDACTED]")
                    redacted.append(f"{key}[{index}]")
                else:
                    safe_items.append(item)
            clean[str(key)] = safe_items
        else:
            clean[str(key)] = value
    return clean, sorted(set(redacted))


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(SECRET_KEY.search(str(key)) or _contains_secret_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    if isinstance(value, str):
        return bool(BEARER.search(value))
    return False


def validate_acquisition_manifest(record: dict[str, Any]) -> None:
    required = {
        "schema_version", "acquisition_request_id", "provider_id", "api_version",
        "endpoint", "request_parameters", "retrieved_at_utc", "instrument",
        "requested_date_range", "interval", "response", "retry_metadata",
        "parser_version", "normalizer_version", "session_manifest_version",
        "raw_response_sha256", "normalized_output_sha256", "redacted_parameter_names", "raw_response_retention",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"acquisition manifest missing fields: {sorted(missing)}")
    if _contains_secret_key(record):
        raise ValueError("secret-bearing acquisition metadata is forbidden")
    extra = set(record) - required
    if extra:
        raise ValueError(f"unexpected acquisition manifest fields: {sorted(extra)}")
    if type(record["schema_version"]) is not int or record["schema_version"] != PIPELINE_SCHEMA_VERSION:
        raise ValueError("unsupported acquisition manifest schema_version")
    if not isinstance(record["acquisition_request_id"], str) or not record["acquisition_request_id"].strip():
        raise ValueError("acquisition_request_id is required")
    if not isinstance(record["provider_id"], str) or not record["provider_id"].strip():
        raise ValueError("provider_id is required")
    if not isinstance(record["api_version"], str) or not record["api_version"].strip():
        raise ValueError("api_version is required")
    endpoint = record["endpoint"]
    if not isinstance(endpoint, str) or sanitize_endpoint(endpoint) != endpoint:
        raise ValueError("endpoint must be sanitized HTTPS without query or user information")
    retrieved = parse_explicit_timestamp(record["retrieved_at_utc"])
    if retrieved.utcoffset() != timedelta(0):
        raise ValueError("retrieved_at_utc must use UTC offset")
    instrument = record["instrument"]
    if not isinstance(instrument, dict) or not instrument.get("instrument_key"):
        raise ValueError("instrument.instrument_key is required")
    if not isinstance(record["request_parameters"], dict):
        raise ValueError("request_parameters must be an object")
    date_range = record["requested_date_range"]
    if not isinstance(date_range, dict) or set(date_range) != {"from_date", "to_date"}:
        raise ValueError("requested_date_range requires from_date and to_date")
    first, last = date.fromisoformat(date_range["from_date"]), date.fromisoformat(date_range["to_date"])
    if first > last:
        raise ValueError("requested date range is reversed")
    interval = record["interval"]
    if not isinstance(interval, dict) or set(interval) != {"value", "unit"} or type(interval.get("value")) is not int or interval["value"] <= 0 or not isinstance(interval.get("unit"), str) or not interval.get("unit"):
        raise ValueError("interval requires positive integer value and unit")
    response = record["response"]
    if not isinstance(response, dict) or not {"status", "http_status"} <= set(response) or set(response) - {"status", "http_status", "request_id"} or response.get("status") not in {"SUCCESS", "FAILED"}:
        raise ValueError("response.status must be SUCCESS or FAILED")
    if response["http_status"] is not None and (type(response["http_status"]) is not int or not 100 <= response["http_status"] <= 599):
        raise ValueError("response.http_status must be null or a valid HTTP status")
    if response.get("request_id") is not None and not isinstance(response["request_id"], str):
        raise ValueError("response.request_id must be a string or null")
    if not isinstance(record["retry_metadata"], dict):
        raise ValueError("retry_metadata must be an object")
    if not isinstance(record["redacted_parameter_names"], list) or not all(isinstance(x, str) for x in record["redacted_parameter_names"]):
        raise ValueError("redacted_parameter_names must be a string array")
    for key in ("parser_version", "normalizer_version", "session_manifest_version"):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError(f"{key} must be a non-empty string")
    for field in ("raw_response_sha256", "normalized_output_sha256"):
        value = record[field]
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise ValueError(f"{field} must be a lowercase SHA-256 or null")
    retention = record["raw_response_retention"]
    if not isinstance(retention, dict) or set(retention) != {"enabled", "provider_terms_reviewed", "policy_version"} or not isinstance(retention.get("enabled"), bool) or not isinstance(retention.get("provider_terms_reviewed"), bool) or not isinstance(retention.get("policy_version"), str) or not retention.get("policy_version"):
        raise ValueError("raw_response_retention requires enabled, provider_terms_reviewed, and policy_version")
    if retention["enabled"] and not retention["provider_terms_reviewed"]:
        raise ValueError("raw response retention requires prior provider-terms review")
    if response["status"] == "SUCCESS" and (record["raw_response_sha256"] is None or record["normalized_output_sha256"] is None):
        raise ValueError("successful acquisition requires raw and normalized hashes")


def make_acquisition_manifest(*, acquisition_request_id: str, provider_id: str, api_version: str,
                              endpoint: str, request_parameters: dict[str, Any], retrieved_at_utc: str,
                              instrument: dict[str, Any], requested_date_range: dict[str, str],
                              interval: dict[str, Any], response: dict[str, Any],
                              retry_metadata: dict[str, Any], parser_version: str,
                              normalizer_version: str, session_manifest_version: str,
                              raw_response_sha256: str | None,
                              normalized_output_sha256: str | None, raw_response_retention: dict[str, Any]) -> dict[str, Any]:
    safe_parameters, redacted_names = sanitize_parameters(request_parameters)
    result = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "acquisition_request_id": acquisition_request_id,
        "provider_id": provider_id,
        "api_version": api_version,
        "endpoint": sanitize_endpoint(endpoint),
        "request_parameters": safe_parameters,
        "retrieved_at_utc": retrieved_at_utc,
        "instrument": instrument,
        "requested_date_range": requested_date_range,
        "interval": interval,
        "response": response,
        "retry_metadata": retry_metadata,
        "parser_version": parser_version,
        "normalizer_version": normalizer_version,
        "session_manifest_version": session_manifest_version,
        "raw_response_sha256": raw_response_sha256,
        "normalized_output_sha256": normalized_output_sha256,
        "redacted_parameter_names": redacted_names,
        "raw_response_retention": raw_response_retention,
    }
    validate_acquisition_manifest(result)
    return result


def normalize_v3_candle(candle: list[Any] | tuple[Any, ...], *, acquisition_request_id: str,
                        source_row_index: int, instrument: dict[str, Any],
                        interval_minutes: int, adjustment_status: str = "UNRESOLVED") -> dict[str, Any]:
    if len(candle) not in (6, 7):
        raise ValueError("V3 candle must contain timestamp, OHLC, volume, and optional OI")
    raw_timestamp, timestamp_utc, timestamp_ist = normalize_timestamp(candle[0])
    return {
        "schema_version": CANDLE_SCHEMA_VERSION,
        "source_request_id": acquisition_request_id,
        "source_row_index": source_row_index,
        "instrument": instrument,
        "timestamp_original": raw_timestamp,
        "timestamp_offset": timestamp_offset(raw_timestamp),
        "timestamp_utc": timestamp_utc,
        "timestamp_ist": timestamp_ist,
        "interval_minutes": interval_minutes,
        "interval_label": "START",
        "open": candle[1],
        "high": candle[2],
        "low": candle[3],
        "close": candle[4],
        "provider_volume": candle[5],
        "open_interest": candle[6] if len(candle) == 7 else None,
        "adjustment_status": adjustment_status,
        "validation_classification": "UNRESOLVED",
        "validation_reasons": ["not_validated"],
    }


def validate_candle_schema(record: dict[str, Any]) -> None:
    required = {
        "schema_version", "source_request_id", "source_row_index", "instrument",
        "timestamp_original", "timestamp_offset", "timestamp_utc", "timestamp_ist",
        "interval_minutes", "interval_label", "open", "high", "low", "close",
        "provider_volume", "open_interest", "adjustment_status",
        "validation_classification", "validation_reasons",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"candle missing fields: {sorted(missing)}")
    if type(record["schema_version"]) is not int or record["schema_version"] != CANDLE_SCHEMA_VERSION:
        raise ValueError("unsupported candle schema_version")
    if not isinstance(record["source_request_id"], str) or not record["source_request_id"]:
        raise ValueError("source_request_id is required")
    if type(record["source_row_index"]) is not int or record["source_row_index"] < 0:
        raise ValueError("source_row_index must be a nonnegative integer")
    if not isinstance(record["instrument"], dict) or not record["instrument"].get("instrument_key"):
        raise ValueError("instrument.instrument_key is required")
    if record["interval_label"] != "START":
        raise ValueError("only explicit START interval labels are supported")
    if type(record["interval_minutes"]) is not int or record["interval_minutes"] <= 0:
        raise ValueError("interval_minutes must be positive")
    if not all(isinstance(record[key], str) for key in ("timestamp_original", "timestamp_offset", "timestamp_utc", "timestamp_ist")):
        raise ValueError("candle timestamps and offset must be strings")
    for key in ("open", "high", "low", "close", "provider_volume"):
        if type(record[key]) not in (int, float):
            raise ValueError(f"{key} must be a JSON number")
    if record["open_interest"] is not None and type(record["open_interest"]) not in (int, float):
        raise ValueError("open_interest must be a JSON number or null")
    original, utc_value, ist_value = normalize_timestamp(record["timestamp_original"])
    if record["timestamp_offset"] != timestamp_offset(original):
        raise ValueError("timestamp_offset does not match original timestamp")
    if record["timestamp_utc"] != utc_value or record["timestamp_ist"] != ist_value:
        raise ValueError("normalized timestamps do not match original timestamp")
    if record["adjustment_status"] not in {"ADJUSTED", "UNADJUSTED", "UNRESOLVED"}:
        raise ValueError("invalid adjustment_status")
    if record["validation_classification"] not in CLASSIFICATIONS:
        raise ValueError("invalid validation_classification")
    if not isinstance(record["validation_reasons"], list) or not all(isinstance(x, str) for x in record["validation_reasons"]):
        raise ValueError("validation_reasons must be a list")


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _trusted_session(entry: dict[str, Any], manifest: dict[str, Any]) -> bool:
    source_map = {item.get("source_id"): item for item in manifest.get("sources", []) if isinstance(item, dict)}
    ids = entry.get("source_ids")
    if entry.get("verification_status") != "VERIFIED" or not isinstance(ids, list) or not ids:
        return False
    for source_id in ids:
        source = source_map.get(source_id)
        if not source or not source.get("url"):
            return False
        bounds = source.get("effective_dates", "").split("/")
        if len(bounds) != 2 or not (bounds[0] <= entry.get("date", "") <= bounds[1]):
            return False
    return True


def validate_session_manifest(manifest: dict[str, Any]) -> None:
    required = {"schema_version", "manifest_version", "exchange", "segment", "timezone", "coverage", "candle_interval_minutes", "sources", "sessions"}
    missing = required - set(manifest)
    if missing:
        raise ValueError(f"session manifest missing fields: {sorted(missing)}")
    if manifest["schema_version"] != SESSION_MANIFEST_SCHEMA_VERSION or manifest["manifest_version"] != SESSION_MANIFEST_VERSION:
        raise ValueError("unsupported session manifest version")
    if (manifest["exchange"], manifest["segment"], manifest["timezone"]) != ("NSE", "CM", "Asia/Kolkata"):
        raise ValueError("unexpected exchange, segment, or timezone")
    if manifest["candle_interval_minutes"] != 5:
        raise ValueError("session manifest interval must be five minutes")
    if not isinstance(manifest["sources"], list) or not manifest["sources"]:
        raise ValueError("session manifest requires registered sources")
    source_ids = [s.get("source_id") for s in manifest["sources"] if isinstance(s, dict)]
    if len(source_ids) != len(set(source_ids)) or None in source_ids:
        raise ValueError("source identifiers must be unique and non-empty")
    correction_sources = [s for s in manifest["sources"] if s.get("source_id") == "NSE_2024_NOV20"]
    if len(correction_sources) != 1 or correction_sources[0].get("document_id") != "NSE/CMTR/64960" or correction_sources[0].get("issue_date") != "2024-11-08" or correction_sources[0].get("effective_date") != "2024-11-20" or correction_sources[0].get("effective_dates") != "2024-11-20/2024-11-20" or correction_sources[0].get("segment") != "CM" or correction_sources[0].get("verification_status") != "VERIFIED" or correction_sources[0].get("url") != "https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf" or not correction_sources[0].get("verification_metadata", {}).get("primary_source"):
        raise ValueError("verified NSE/CMTR/64960 correction source is required")
    seen_dates: set[str] = set()
    for entry in manifest["sessions"]:
        day = date.fromisoformat(entry["date"])
        if entry["date"] in seen_dates:
            raise ValueError("duplicate session date")
        seen_dates.add(entry["date"])
        if entry.get("segment") != "CM" or entry.get("timezone") != "Asia/Kolkata":
            raise ValueError("session segment/timezone mismatch")
        if entry.get("session_type") not in {"REGULAR", "SPECIAL", "HOLIDAY", "WEEKEND_CLOSED", "UNRESOLVED"}:
            raise ValueError("unknown session_type")
        intervals = entry.get("normal_market_intervals")
        if not isinstance(intervals, list):
            raise ValueError("normal_market_intervals must be a list")
        for interval in intervals:
            start, end = time.fromisoformat(interval["start"]), time.fromisoformat(interval["end"])
            if start >= end:
                raise ValueError("session interval must have positive duration")
        if entry.get("session_type") == "HOLIDAY" and intervals:
            raise ValueError("holiday cannot contain normal-market intervals")
        if entry.get("session_type") in {"REGULAR", "SPECIAL"} and not intervals:
            raise ValueError("open session must contain intervals")
    nov20 = [item for item in manifest["sessions"] if item.get("date") == "2024-11-20"]
    if len(nov20) != 1 or nov20[0].get("session_type") != "HOLIDAY" or nov20[0].get("normal_market_intervals") != [] or nov20[0].get("verification_status") != "VERIFIED" or "NSE_2024_NOV20" not in nov20[0].get("source_ids", []):
        raise ValueError("2024-11-20 must be a verified NSE CM holiday with no open intervals")
    if seen_dates:
        first = date.fromisoformat(manifest["coverage"]["start"])
        last = date.fromisoformat(manifest["coverage"]["end"])
        if min(map(date.fromisoformat, seen_dates)) < first or max(map(date.fromisoformat, seen_dates)) > last:
            raise ValueError("session records exceed manifest coverage")


def load_session_manifest_v2(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_session_manifest(manifest)
    return manifest


def _expected_start_times(entry: dict[str, Any], interval_minutes: int) -> set[str]:
    result: set[str] = set()
    for item in entry.get("normal_market_intervals", []):
        start, end = time.fromisoformat(item["start"]), time.fromisoformat(item["end"])
        current = datetime.combine(date(2000, 1, 1), start)
        stop = datetime.combine(date(2000, 1, 1), end)
        while current + timedelta(minutes=interval_minutes) <= stop:
            result.add(current.strftime("%H:%M"))
            current += timedelta(minutes=interval_minutes)
    return result


def validate_candle_records(records: list[dict[str, Any]], manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate normalized records and return (row classifications, coverage reports)."""
    validate_session_manifest(manifest)
    dates = {entry["date"]: entry for entry in manifest["sessions"]}
    results: list[dict[str, Any]] = []
    duplicate_keys: dict[tuple[str, str], list[int]] = {}
    parsed_rows: list[tuple[int, dict[str, Any], datetime | None, list[str]]] = []
    for idx, original in enumerate(records):
        row = dict(original)
        reasons: list[str] = []
        stamp: datetime | None = None
        try:
            validate_candle_schema(row)
            stamp = parse_explicit_timestamp(row["timestamp_original"]).astimezone(IST)
        except (ValueError, TypeError, KeyError) as exc:
            reason = str(exc)
            reasons.append("schema_or_timestamp_invalid:" + reason)
            if "explicit UTC offset" in reason:
                reasons.append("timestamp_timezone_unresolved")
        if stamp is not None:
            if stamp.minute % row["interval_minutes"] != 0 or stamp.second or stamp.microsecond:
                reasons.append("timestamp_not_interval_aligned")
            vals = [row.get(k) for k in ("open", "high", "low", "close", "provider_volume")]
            if not all(_finite_number(v) for v in vals):
                reasons.append("ohlcv_nonfinite_or_nonnumeric")
            else:
                o, h, low, close, volume = map(float, vals)
                if min(o, h, low, close) <= 0 or low > min(o, close) or h < max(o, close) or low > h:
                    reasons.append("invalid_ohlc_relationship")
                if volume < 0:
                    reasons.append("negative_volume")
            oi = row.get("open_interest")
            if oi is not None and (not _finite_number(oi) or float(oi) < 0):
                reasons.append("invalid_open_interest")
            key = (str(row.get("instrument", {}).get("instrument_key", "")), stamp.isoformat())
            duplicate_keys.setdefault(key, []).append(idx)
            entry = dates.get(stamp.date().isoformat())
            if entry is None or not _trusted_session(entry, manifest):
                reasons.append("session_manifest_unresolved")
            elif entry["session_type"] == "HOLIDAY":
                reasons.append("candle_on_verified_holiday")
            elif entry["session_type"] == "WEEKEND_CLOSED":
                reasons.append("candle_on_verified_closed_date")
            elif not any(
                time.fromisoformat(interval["start"]) <= stamp.timetz().replace(tzinfo=None) <
                time.fromisoformat(interval["end"])
                and (stamp.hour * 60 + stamp.minute - (time.fromisoformat(interval["start"]).hour * 60 + time.fromisoformat(interval["start"]).minute)) % row["interval_minutes"] == 0
                for interval in entry.get("normal_market_intervals", [])
            ):
                reasons.append("out_of_session_or_unaligned_start_label")
        parsed_rows.append((idx, row, stamp, reasons))
    for indexes in duplicate_keys.values():
        if len(indexes) > 1:
            for idx in indexes:
                parsed_rows[idx][3].append("duplicate_instrument_timestamp")
    for idx, row, stamp, reasons in parsed_rows:
        reasons.extend(row.pop("_validation_reasons", []))
        # Session absence/untrusted calendar is unresolved. All structural,
        # duplicate, and session-rule violations with trusted evidence are invalid.
        unresolved = "session_manifest_unresolved" in reasons or "timestamp_timezone_unresolved" in reasons
        invalid_reasons = [r for r in reasons if r != "session_manifest_unresolved" and r != "not_validated"]
        if unresolved:
            classification = "UNRESOLVED"
        elif invalid_reasons:
            classification = "VERIFIED_INVALID"
        else:
            classification = "VERIFIED_VALID"
        row["validation_classification"] = classification
        row["validation_reasons"] = reasons or ["all_offline_checks_passed"]
        row["timestamp_ist"] = stamp.isoformat() if stamp else None
        results.append(row)
    coverage = coverage_report(results, manifest)
    return results, coverage


def partition_candle_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Keep verified, invalid, and unresolved records in distinct result buckets."""
    output = {status: [] for status in sorted(CLASSIFICATIONS)}
    for record in records:
        status = record.get("validation_classification")
        if status not in CLASSIFICATIONS:
            raise ValueError("candle record has no valid validation classification")
        if not record.get("validation_reasons"):
            raise ValueError("classified candle record must retain validation reasons")
        output[status].append(record)
    return output

def coverage_report(records: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    validate_session_manifest(manifest)
    instruments = sorted({r.get("instrument", {}).get("instrument_key", "") for r in records})
    observed: dict[tuple[str, str], set[str]] = {}
    for row in records:
        value = row.get("timestamp_ist")
        if not value:
            continue
        stamp = parse_explicit_timestamp(value).astimezone(IST)
        key = (row.get("instrument", {}).get("instrument_key", ""), stamp.date().isoformat())
        observed.setdefault(key, set()).add(stamp.strftime("%H:%M"))
    observed_dates = [key[1] for key in observed]
    if not observed_dates:
        return []
    first_observed, last_observed = min(observed_dates), max(observed_dates)
    result=[]
    for entry in manifest["sessions"]:
        if entry["date"] < first_observed or entry["date"] > last_observed:
            continue
        if entry["session_type"] in {"WEEKEND_CLOSED", "HOLIDAY"}:
            continue
        if not _trusted_session(entry, manifest):
            result.append({"date": entry["date"], "classification":"UNRESOLVED", "reason":"untrusted_session_manifest"})
            continue
        expected = _expected_start_times(entry, manifest["candle_interval_minutes"])
        for instrument in instruments:
            actual=observed.get((instrument, entry["date"]), set())
            missing=sorted(expected-actual)
            result.append({
                "instrument_key":instrument, "date":entry["date"], "expected_count":len(expected),
                "observed_count":len(actual), "missing_start_labels":missing,
                "classification":"VERIFIED_INVALID" if missing else "VERIFIED_VALID",
                "reason":"missing_expected_start_labels" if missing else "complete_expected_start_labels",
            })
    return result
