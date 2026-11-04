"""One-shot, fail-closed runner for the approved RELIANCE history pilot.

The CLI has no override arguments. It reads only the fixed pilot config and a
separate human-reviewed authorization attestation. It never loads .env files.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import gzip
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.parse import quote, urlsplit
import uuid

import requests
from requests.adapters import HTTPAdapter
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PILOT_RELATIVE = Path("data/acquisition_pilots/pilot_v1.json")
AUTHORIZATION_RELATIVE = Path("data/acquisition_pilots/execution_authorization_v1.json")
AUTH_SCHEMA_RELATIVE = Path("schemas/acquisition-pilot-authorization-v1.schema.json")
SESSION_RELATIVE = Path("data/audits/nse_cm_session_manifest_v2.json")
PILOT_ID = "upstox-v3-reliance-2025-02-03-5m-v1"
INSTRUMENT_KEY = "NSE_EQ|INE002A01018"
SYMBOL = "RELIANCE"
TARGET_DATE = "2025-02-03"
INTERVAL_MINUTES = 5
MAX_REQUESTS = 1
MAX_RETRIES = 0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CANDLES = 75
TIMEOUT_SECONDS = 15
PARSER_VERSION = "upstox-v3-pilot-parser-v1"
NORMALIZER_VERSION = "offline-market-data-v1"
IST = ZoneInfo("Asia/Kolkata")

from app.offline_market_data_v1 import (  # noqa: E402
    canonical_json_hash,
    load_session_manifest_v2,
    make_acquisition_manifest,
    normalize_v3_candle,
    partition_candle_records,
    parse_explicit_timestamp,
    sha256_bytes,
    validate_acquisition_manifest,
    validate_candle_records,
)


class PilotBlocked(RuntimeError):
    """A prerequisite is not established; no HTTP request was made."""


class PilotExecutionError(RuntimeError):
    """The single response failed transport or validation checks."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PilotBlocked(f"cannot read required JSON file: {path.name}") from exc


def _within_root(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PilotBlocked("configured path must be relative and cannot traverse parents")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PilotBlocked("configured path resolves outside the research copy")
    current = root.resolve()
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise PilotBlocked("symlinked pilot paths are not permitted")
    return resolved


def _expected_output_paths(pilot_id: str) -> dict[str, str]:
    base = f"data/acquisition_pilots/runs/{pilot_id}"
    return {
        "root": base + "/",
        "raw_response": base + "/raw/response-{sha256}.json",
        "request_manifest": base + "/requests/{request_id}.json",
        "normalized_records": base + "/normalized/candles.jsonl",
        "quarantine_invalid": base + "/quarantine/verified_invalid.jsonl",
        "quarantine_unresolved": base + "/quarantine/unresolved.jsonl",
        "validation_report": base + "/validation.json",
    }


def validate_pilot_config(root: Path, plan: dict[str, Any]) -> tuple[list[str], Path]:
    blockers: list[str] = []
    if plan.get("pilot_id") != PILOT_ID:
        blockers.append("pilot_id is not the single allowlisted pilot")
    if plan.get("status") != "APPROVED_FOR_SINGLE_REQUEST":
        blockers.append("pilot configuration is not explicitly approved for one request")
    if (plan.get("provider_id"), plan.get("api_version")) != ("upstox", "v3"):
        blockers.append("provider/API version is not the approved Upstox V3 endpoint")
    if plan.get("endpoint_template") != "https://api.upstox.com/v3/historical-candle/{instrument_key}/minutes/{interval}/{to_date}/{from_date}":
        blockers.append("endpoint template differs from the allowlisted read-only endpoint")

    request = plan.get("request")
    if not isinstance(request, dict):
        blockers.append("request configuration is missing")
        request = {}
    allowlist = request.get("instrument_allowlist")
    if not isinstance(allowlist, list) or len(allowlist) != 1:
        blockers.append("exactly one instrument must be allowlisted")
    elif (allowlist[0].get("symbol"), allowlist[0].get("instrument_key"), allowlist[0].get("exchange"), allowlist[0].get("segment")) != (SYMBOL, INSTRUMENT_KEY, "NSE", "CM"):
        blockers.append("instrument/date scope is not the RELIANCE NSE CM allowlist")
    if request.get("from_date") != TARGET_DATE or request.get("to_date") != TARGET_DATE:
        blockers.append("only 2025-02-03 is permitted")
    if request.get("interval") != {"value": 5, "unit": "minutes"} or request.get("timestamp_label") != "START" or request.get("timezone") != "Asia/Kolkata":
        blockers.append("only five-minute START labels in Asia/Kolkata are permitted")
    if request.get("max_http_requests") != MAX_REQUESTS or request.get("max_retries") != MAX_RETRIES:
        blockers.append("request/retry limits must be exactly one request and zero retries")
    if request.get("max_response_bytes") != MAX_RESPONSE_BYTES or request.get("max_candle_rows") != MAX_CANDLES:
        blockers.append("response/candle limits must be exactly 2 MiB and 75 rows")

    retention = plan.get("retention")
    if not isinstance(retention, dict) or retention.get("provider_terms_review_required") is not True or retention.get("retention_permission_confirmed") is not True:
        blockers.append("exact raw-response retention permission is not confirmed in the pilot config")

    session = plan.get("session_validation")
    if not isinstance(session, dict) or session.get("manifest_path") != SESSION_RELATIVE.as_posix() or session.get("manifest_version") != "nse-cm-2023-12-01_2026-10-01-v2" or session.get("required_session_date_status") != "VERIFIED" or session.get("expected_interval_count") != MAX_CANDLES:
        blockers.append("pinned verified session requirements do not match the approved pilot")

    outputs = plan.get("outputs")
    expected_outputs = _expected_output_paths(PILOT_ID)
    if not isinstance(outputs, dict) or outputs != expected_outputs:
        blockers.append("output paths differ from the fixed pilot-specific paths")
        output_root = root / expected_outputs["root"].rstrip("/")
    else:
        output_root = _within_root(root, outputs["root"].rstrip("/"))
        for key, value in outputs.items():
            if key != "root":
                _within_root(root, value.replace("{sha256}", "0" * 64).replace("{request_id}", "request-id"))
    if output_root.exists():
        blockers.append("pilot output directory already exists; refusing to overwrite")
    return blockers, output_root


def _require_evidence(section: Any, name: str) -> list[str]:
    if not isinstance(section, dict):
        return [f"{name} evidence is missing"]
    issues = []
    if section.get("verified") is not True:
        issues.append(f"{name} is not verified")
    if not isinstance(section.get("evidence_reference"), str) or not section["evidence_reference"].strip():
        issues.append(f"{name} evidence reference is missing")
    try:
        reviewed = parse_explicit_timestamp(section.get("reviewed_at_utc"))
        if reviewed.utcoffset() != timezone.utc.utcoffset(None):
            issues.append(f"{name} review time must be UTC")
    except (ValueError, TypeError):
        issues.append(f"{name} review time is missing or invalid")
    return issues


def _master_rows(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".gz":
        raw = gzip.decompress(raw)
        suffix = path.with_suffix("").suffix.lower()
    if suffix == ".json":
        value = json.loads(raw.decode("utf-8-sig"))
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            rows = value.get("data", value.get("instruments"))
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    if suffix == ".csv":
        return list(csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline="")))
    raise PilotBlocked("instrument-master snapshot must be a JSON array/object or CSV")


def _verify_instrument_master(root: Path, evidence: Any) -> list[str]:
    issues = _require_evidence(evidence, "dated official instrument-master snapshot")
    if not isinstance(evidence, dict):
        return issues
    try:
        snapshot_date = date.fromisoformat(evidence.get("snapshot_date", ""))
        if snapshot_date > date.today():
            issues.append("instrument-master snapshot date is in the future")
    except (ValueError, TypeError):
        issues.append("instrument-master snapshot date is invalid")
    try:
        url = urlsplit(evidence.get("official_source_url", ""))
        if url.scheme != "https" or not (url.hostname == "upstox.com" or (url.hostname or "").endswith(".upstox.com")):
            issues.append("instrument-master source is not an HTTPS Upstox URL")
    except ValueError:
        issues.append("instrument-master source URL is invalid")
    try:
        path = _within_root(root, evidence.get("snapshot_path", ""))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != evidence.get("snapshot_sha256"):
            issues.append("instrument-master snapshot SHA-256 does not match")
        rows = _master_rows(path)
        matches = [row for row in rows if row.get("instrument_key") == INSTRUMENT_KEY]
        if len(matches) != 1:
            issues.append("snapshot does not contain exactly one matching RELIANCE instrument key")
        else:
            row = matches[0]
            if row.get("trading_symbol") != SYMBOL or row.get("exchange") != "NSE" or row.get("segment") not in {"NSE_EQ", "CM"}:
                issues.append("snapshot mapping does not confirm RELIANCE / NSE CM")
            if evidence.get("symbol") != SYMBOL or evidence.get("instrument_key") != INSTRUMENT_KEY or evidence.get("exchange") != "NSE" or evidence.get("segment") not in {"NSE_EQ", "CM"}:
                issues.append("attested instrument mapping does not match the single pilot")
    except (PilotBlocked, OSError, ValueError, TypeError) as exc:
        issues.append(str(exc) if isinstance(exc, PilotBlocked) else "instrument-master snapshot cannot be verified")
    return issues


def validate_authorization(root: Path, evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    expected_keys = {"schema_version", "pilot_id", "approval_status", "provider_authorization", "research_use", "raw_retention", "request_limits", "instrument_master", "execution_authorization"}
    if set(evidence) != expected_keys:
        issues.append("authorization attestation has missing or unexpected fields")
    if type(evidence.get("schema_version")) is not int or evidence.get("schema_version") != 1 or evidence.get("pilot_id") != PILOT_ID or evidence.get("approval_status") != "APPROVED":
        issues.append("authorization attestation is not approved for this pilot")
    section_keys = {
        "provider_authorization": {"verified", "evidence_reference", "reviewed_at_utc", "endpoint", "read_only"},
        "research_use": {"verified", "evidence_reference", "reviewed_at_utc", "research_only_permitted"},
        "raw_retention": {"verified", "evidence_reference", "reviewed_at_utc", "raw_bytes_permitted", "retention_duration"},
        "request_limits": {"verified", "evidence_reference", "reviewed_at_utc", "requests_per_minute", "max_concurrent_requests", "zero_retries_permitted"},
        "instrument_master": {"verified", "evidence_reference", "reviewed_at_utc", "snapshot_path", "snapshot_sha256", "snapshot_date", "official_source_url", "symbol", "instrument_key", "exchange", "segment"},
        "execution_authorization": {"approved", "evidence_reference", "pilot_id", "instrument_key", "date", "interval_minutes", "max_http_requests"},
    }
    for section, keys in section_keys.items():
        value = evidence.get(section)
        if not isinstance(value, dict) or set(value) != keys:
            issues.append(f"{section.replace('_', ' ')} has missing or unexpected fields")
    for name in ("provider_authorization", "research_use", "raw_retention", "request_limits"):
        issues.extend(_require_evidence(evidence.get(name), name.replace("_", " ")))
    limits = evidence.get("request_limits")
    if isinstance(limits, dict):
        if type(limits.get("requests_per_minute")) is not int or limits["requests_per_minute"] < 1:
            issues.append("applicable request-per-minute limit is not verified")
        if type(limits.get("max_concurrent_requests")) is not int or limits["max_concurrent_requests"] < 1:
            issues.append("applicable concurrency limit is not verified")
        if limits.get("zero_retries_permitted") is not True:
            issues.append("zero-retry policy is not explicitly permitted")
    raw_retention = evidence.get("raw_retention")
    if isinstance(raw_retention, dict):
        if raw_retention.get("raw_bytes_permitted") is not True:
            issues.append("provider terms do not explicitly permit exact raw response bytes")
        if not isinstance(raw_retention.get("retention_duration"), str) or not raw_retention["retention_duration"].strip():
            issues.append("permitted raw-response retention duration is missing")
    provider = evidence.get("provider_authorization")
    if isinstance(provider, dict) and (provider.get("endpoint") != "https://api.upstox.com/v3/historical-candle/{instrument_key}/minutes/{interval}/{to_date}/{from_date}" or provider.get("read_only") is not True):
        issues.append("provider authorization must cover the exact read-only V3 historical-candle endpoint")
    research = evidence.get("research_use")
    if isinstance(research, dict) and research.get("research_only_permitted") is not True:
        issues.append("intended research use is not expressly permitted")
    issues.extend(_verify_instrument_master(root, evidence.get("instrument_master")))
    approval = evidence.get("execution_authorization")
    expected_approval = {"approved": True, "pilot_id": PILOT_ID, "instrument_key": INSTRUMENT_KEY, "date": TARGET_DATE, "interval_minutes": INTERVAL_MINUTES, "max_http_requests": MAX_REQUESTS}
    if not isinstance(approval, dict) or any(approval.get(key) != value for key, value in expected_approval.items()) or not isinstance(approval.get("evidence_reference"), str) or not approval["evidence_reference"].strip():
        issues.append("explicit execution authorization does not exactly match the one-request pilot")
    return issues


def preflight(root: str | Path = ROOT) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    root = Path(root).resolve()
    config_path = root / PILOT_RELATIVE
    if not config_path.is_file():
        raise PilotBlocked("fixed pilot configuration is missing")
    plan = _read_json(config_path)
    blockers, output_root = validate_pilot_config(root, plan)
    authorization_path = root / AUTHORIZATION_RELATIVE
    if not authorization_path.is_file():
        blockers.append("explicit source/retention/request/execution authorization attestation is missing")
        evidence = {}
    else:
        evidence = _read_json(authorization_path)
        if not isinstance(evidence, dict):
            blockers.append("authorization attestation must be a JSON object")
        else:
            blockers.extend(validate_authorization(root, evidence))
    manifest_path = _within_root(root, plan.get("session_validation", {}).get("manifest_path", SESSION_RELATIVE.as_posix()))
    try:
        manifest = load_session_manifest_v2(manifest_path)
        sessions = [s for s in manifest["sessions"] if s.get("date") == TARGET_DATE]
        if len(sessions) != 1 or sessions[0].get("verification_status") != "VERIFIED" or sessions[0].get("session_type") != "REGULAR" or sessions[0].get("normal_market_intervals") != [{"start": "09:15", "end": "15:30"}]:
            blockers.append("pinned manifest does not verify the exact regular-session pilot date")
    except (PilotBlocked, OSError, ValueError) as exc:
        blockers.append(str(exc) if isinstance(exc, PilotBlocked) else "pinned session manifest failed validation")
        manifest = {}
    if blockers:
        raise PilotBlocked("; ".join(sorted(set(blockers))))
    return plan, evidence, manifest, output_root, {"config_path": config_path, "authorization_path": authorization_path, "manifest_path": manifest_path}


def _new_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=0))
    return session


def _read_body(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise PilotExecutionError("response exceeds the configured byte limit")
        except ValueError as exc:
            raise PilotExecutionError("invalid response Content-Length") from exc
    encoding = str(response.headers.get("Content-Encoding", "identity")).lower()
    if encoding not in {"", "identity"}:
        raise PilotExecutionError("compressed response is not permitted for exact-byte parsing")
    raw = getattr(response, "raw", None)
    if raw is None or not hasattr(raw, "stream"):
        raise PilotExecutionError("raw response stream is unavailable")
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in raw.stream(64 * 1024, decode_content=False):
            if not isinstance(chunk, bytes):
                raise PilotExecutionError("response stream yielded non-byte content")
            total += len(chunk)
            if total > max_bytes:
                raise PilotExecutionError("response exceeds the configured byte limit")
            chunks.append(chunk)
    except PilotExecutionError:
        raise
    except Exception as exc:
        raise PilotExecutionError(f"response read failed ({type(exc).__name__})") from None
    return b"".join(chunks)


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n" for row in records)


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def _unresolved_row(request_id: str, index: int, reason: str) -> dict[str, Any]:
    return {"source_request_id": request_id, "source_row_index": index, "validation_classification": "UNRESOLVED", "validation_reasons": [reason]}


def _normalize_payload(payload: Any, request_id: str, plan: dict[str, Any], manifest: dict[str, Any], instrument_master_sha256: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict) or not isinstance(payload["data"].get("candles"), list):
        return [_unresolved_row(request_id, 0, "unexpected_response_schema")], [], ["unexpected_response_schema"], []
    candles = payload["data"]["candles"]
    max_rows = plan["request"]["max_candle_rows"]
    if len(candles) > max_rows:
        return [], [], ["response_row_limit_exceeded"], []
    normalized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        if not isinstance(candle, list) or len(candle) not in (6, 7):
            unresolved.append(_unresolved_row(request_id, index, "unexpected_candle_row_schema"))
            errors.append("unexpected_candle_row_schema")
            continue
        try:
            row = normalize_v3_candle(candle, acquisition_request_id=request_id, source_row_index=index,
                                      instrument={"instrument_key": INSTRUMENT_KEY, "symbol": SYMBOL, "exchange": "NSE", "segment": "CM",
                                                  "instrument_master_snapshot_sha256": instrument_master_sha256}, interval_minutes=INTERVAL_MINUTES)
        except (TypeError, ValueError, IndexError):
            unresolved.append(_unresolved_row(request_id, index, "timestamp_or_row_normalization_unresolved"))
            errors.append("timestamp_or_row_normalization_unresolved")
            continue
        try:
            stamp = parse_explicit_timestamp(row["timestamp_original"])
            if stamp.astimezone(IST).date().isoformat() != TARGET_DATE:
                row["_validation_reasons"] = ["unexpected_requested_date"]
        except ValueError:
            row["_validation_reasons"] = ["timestamp_timezone_unresolved"]
        normalized.append(row)
    results, coverage = validate_candle_records(normalized, manifest)
    results.extend(unresolved)
    expected_labels = 75
    day_coverage = [entry for entry in coverage if entry.get("date") == TARGET_DATE and entry.get("instrument_key") == INSTRUMENT_KEY]
    if len(day_coverage) != 1 or day_coverage[0].get("expected_count") != expected_labels or day_coverage[0].get("observed_count") != expected_labels or day_coverage[0].get("classification") != "VERIFIED_VALID":
        errors.append("expected_session_coverage_incomplete")
    if len(candles) != expected_labels:
        errors.append("returned_row_count_does_not_match_expected_75")
    return results, unresolved, errors, coverage


def run_pilot(*, root: str | Path = ROOT, token: str | None = None,
              session_factory: Callable[[], Any] = _new_session,
              now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """Execute exactly one request. Tests inject a fake session; CLI has no overrides."""
    root = Path(root).resolve()
    plan, evidence, manifest, output_root, _ = preflight(root)
    token = token if token is not None else os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not isinstance(token, str) or not token.strip():
        raise PilotBlocked("protected process environment variable UPSTOX_ACCESS_TOKEN is absent")
    # Exclusive creation is the final local guard; existing runs are never reused.
    try:
        output_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise PilotBlocked("pilot output directory already exists; refusing to overwrite") from exc
    for subdir in ("raw", "requests", "normalized", "quarantine"):
        (output_root / subdir).mkdir()

    request_id = f"{PILOT_ID}-{uuid.uuid4().hex}"
    instrument_path = quote(INSTRUMENT_KEY, safe="")
    url = plan["endpoint_template"].format(instrument_key=instrument_path, interval=INTERVAL_MINUTES,
                                            to_date=TARGET_DATE, from_date=TARGET_DATE)
    request_parameters = {"instrument_key": INSTRUMENT_KEY, "from_date": TARGET_DATE,
                          "to_date": TARGET_DATE, "interval_minutes": INTERVAL_MINUTES,
                          "timestamp_label": "START"}
    session = session_factory()
    session.mount("https://", HTTPAdapter(max_retries=0))
    request_count = 0
    response = None
    raw_body: bytes | None = None
    transport_error: str | None = None
    try:
        if request_count >= MAX_REQUESTS:
            raise PilotExecutionError("single-request limit already consumed")
        request_count += 1
        response = session.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                             "Accept-Encoding": "identity"}, timeout=TIMEOUT_SECONDS,
                               stream=True, allow_redirects=False)
        raw_body = _read_body(response, MAX_RESPONSE_BYTES)
    except PilotExecutionError as exc:
        transport_error = str(exc)
    except Exception as exc:
        transport_error = f"request failed ({type(exc).__name__})"
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    retrieved = now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    http_status = getattr(response, "status_code", None)
    request_header = getattr(response, "headers", {}) if response is not None else {}
    request_provider_id = request_header.get("X-Request-Id") or request_header.get("X-Request-ID")
    if not isinstance(request_provider_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_provider_id):
        request_provider_id = None
    raw_hash = sha256_bytes(raw_body) if raw_body is not None else None
    raw_path = None
    if raw_body is not None:
        raw_path = output_root / "raw" / f"response-{raw_hash}.json"
        _write_new(raw_path, raw_body)

    classified: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    if transport_error:
        validation_errors.append(transport_error)
    elif http_status != 200:
        validation_errors.append(f"unexpected_http_status:{http_status}")
    else:
        try:
            assert raw_body is not None
            payload = json.loads(raw_body.decode("utf-8-sig"))
            classified, _, validation_errors, coverage = _normalize_payload(payload, request_id, plan, manifest, evidence["instrument_master"]["snapshot_sha256"])
        except (UnicodeDecodeError, json.JSONDecodeError, AssertionError):
            validation_errors.append("response_body_is_not_valid_utf8_json")
    buckets = partition_candle_records(classified) if classified else {"VERIFIED_VALID": [], "VERIFIED_INVALID": [], "UNRESOLVED": []}
    normalized_content = _jsonl_bytes(buckets["VERIFIED_VALID"])
    normalized_hash = sha256_bytes(normalized_content)
    invalid_content = _jsonl_bytes(buckets["VERIFIED_INVALID"])
    unresolved_content = _jsonl_bytes(buckets["UNRESOLVED"])
    _write_new(output_root / "normalized" / "candles.jsonl", normalized_content)
    _write_new(output_root / "quarantine" / "verified_invalid.jsonl", invalid_content)
    _write_new(output_root / "quarantine" / "unresolved.jsonl", unresolved_content)

    response_status = "SUCCESS" if http_status == 200 and raw_body is not None else "FAILED"
    master = evidence["instrument_master"]
    record = make_acquisition_manifest(
        acquisition_request_id=request_id, provider_id="upstox", api_version="v3",
        endpoint=url, request_parameters=request_parameters, retrieved_at_utc=retrieved,
        instrument={"instrument_key": INSTRUMENT_KEY, "symbol": SYMBOL, "exchange": "NSE", "segment": "CM",
                    "instrument_master_snapshot_sha256": master["snapshot_sha256"], "instrument_master_snapshot_date": master["snapshot_date"]},
        requested_date_range={"from_date": TARGET_DATE, "to_date": TARGET_DATE},
        interval={"value": INTERVAL_MINUTES, "unit": "minutes"},
        response={"status": response_status, "http_status": http_status, "request_id": request_provider_id},
        retry_metadata={"attempts": request_count, "retries": 0, "retry_delays_seconds": []},
        parser_version=PARSER_VERSION, normalizer_version=NORMALIZER_VERSION,
        session_manifest_version=manifest["manifest_version"], raw_response_sha256=raw_hash,
        normalized_output_sha256=normalized_hash,
        raw_response_retention={"enabled": True, "provider_terms_reviewed": True, "policy_version": "attested-execution-v1"})
    validate_acquisition_manifest(record)
    manifest_path = output_root / "requests" / f"{request_id}.json"
    _write_new(manifest_path, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
    validation = {
        "schema_version": 1, "pilot_id": PILOT_ID, "request_id": request_id,
        "retrieved_at_utc": retrieved, "http_status": http_status,
        "request_count": request_count, "retry_count": 0,
        "response_bytes": len(raw_body) if raw_body is not None else None,
        "response_sha256": raw_hash, "normalized_rows_sha256": normalized_hash,
        "counts": {key: len(value) for key, value in buckets.items()},
        "coverage": coverage, "validation_errors": sorted(set(validation_errors)),
        "status": "PASS" if not validation_errors and not buckets["VERIFIED_INVALID"] and not buckets["UNRESOLVED"] else "FAIL",
        "raw_response_path": raw_path.relative_to(root).as_posix() if raw_path else None,
        "request_manifest_path": manifest_path.relative_to(root).as_posix(),
    }
    _write_new(output_root / "validation.json", json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
    if validation["status"] != "PASS":
        raise PilotExecutionError("pilot response was retained and quarantined, but validation failed; inspect validation.json")
    return validation


def main() -> int:
    try:
        result = run_pilot()
    except PilotBlocked as exc:
        print(f"PILOT BLOCKED: {exc}", file=sys.stderr)
        return 2
    except PilotExecutionError as exc:
        print(f"PILOT FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"PILOT COMPLETE: {result['status']} request_id={result['request_id']} rows={result['counts']['VERIFIED_VALID']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())