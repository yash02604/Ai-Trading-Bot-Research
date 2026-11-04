import copy
import json
from pathlib import Path
import pytest

from app.offline_market_data_v1 import (
    canonical_json_hash,
    load_session_manifest_v2,
    make_acquisition_manifest,
    normalize_timestamp,
    normalize_v3_candle,
    parse_explicit_timestamp,
    partition_candle_records,
    sanitize_endpoint,
    sanitize_parameters,
    sha256_file,
    validate_acquisition_manifest,
    write_acquisition_manifest_immutable,
    validate_candle_records,
    validate_candle_schema,
    validate_session_manifest,
)
from app.session_manifest_v2 import build_session_manifest_v2
from app.client_attestation import verify_historical_audit_inputs

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "data/audits/nse_cm_session_manifest_v1.json"
V2 = ROOT / "data/audits/nse_cm_session_manifest_v2.json"
MANIFEST = load_session_manifest_v2(V2)


def candle(ts="2025-02-03T09:15:00+05:30", **changes):
    row = normalize_v3_candle(
        [ts, 100.0, 102.0, 99.0, 101.0, 250, 0],
        acquisition_request_id="req-fixture-01",
        source_row_index=0,
        instrument={"instrument_key": "NSE_EQ|INE000A01000", "symbol": "TEST", "exchange": "NSE", "segment": "CM"},
        interval_minutes=5,
    )
    row.update(changes)
    return row


def acquisition_record(**changes):
    return make_acquisition_manifest(
        acquisition_request_id="req-test-uuid",
        provider_id="upstox",
        api_version="v3",
        endpoint="https://api.upstox.com/v3/historical-candle/NSE_EQ%7CINE000A01000/minutes/5/2025-02-03?token=must-not-persist",
        request_parameters={"instrument_key": "NSE_EQ|INE000A01000", "interval": 5, "access_token": "never-store-me"},
        retrieved_at_utc="2026-10-19T12:00:00Z",
        instrument={"instrument_key": "NSE_EQ|INE000A01000", "symbol": "TEST", "isin": "INE000A01000", "exchange": "NSE", "segment": "CM", "instrument_master_snapshot_sha256": None},
        requested_date_range={"from_date": "2025-02-03", "to_date": "2025-02-03"},
        interval={"value": 5, "unit": "minutes"},
        response={"status": "SUCCESS", "http_status": 200, "request_id": "provider-request-id"},
        retry_metadata={"attempts": 1, "retry_delays_seconds": []},
        parser_version="raw-v1",
        normalizer_version="normalizer-v1",
        session_manifest_version="nse-cm-2023-12-01_2026-10-01-v2",
        raw_response_sha256="a" * 64,
        normalized_output_sha256="b" * 64,
        raw_response_retention={"enabled": False, "provider_terms_reviewed": False, "policy_version": "retention-v1"},
    ) | changes


def test_session_manifest_v2_and_holiday_regression():
    validate_session_manifest(MANIFEST)
    day = next(item for item in MANIFEST["sessions"] if item["date"] == "2024-11-20")
    assert day["session_type"] == "HOLIDAY"
    assert day["normal_market_intervals"] == []
    assert "NSE_2024_NOV20" in day["source_ids"]
    regular = copy.deepcopy(MANIFEST)
    changed = next(item for item in regular["sessions"] if item["date"] == "2024-11-20")
    changed["session_type"] = "REGULAR"
    changed["normal_market_intervals"] = [{"start": "09:15", "end": "15:30"}]
    with pytest.raises(ValueError, match="2024-11-20"):
        validate_session_manifest(regular)


def test_invalid_manifest_fails_closed():
    invalid = copy.deepcopy(MANIFEST)
    invalid["schema_version"] = 1
    with pytest.raises(ValueError, match="unsupported session manifest"):
        validate_session_manifest(invalid)
    invalid = copy.deepcopy(MANIFEST)
    invalid["sources"] = []
    with pytest.raises(ValueError):
        validate_session_manifest(invalid)


def test_manifest_builder_preserves_v1_and_prior_artifacts(tmp_path):
    input_manifest = json.loads((ROOT / "backtests/provenance_integrity_v1/input_hashes.json").read_text(encoding="utf-8"))
    audited_inputs = {ROOT / item["path"]: item["sha256"] for item in input_manifest["inputs"]}
    prior_audit_outputs = list((ROOT / "backtests/provenance_integrity_v1").glob("*"))
    preserved_paths = set(audited_inputs) | {p for p in prior_audit_outputs if p.is_file()}
    before = {p: sha256_file(p) for p in preserved_paths}
    # Preserve the original historical pins. Client evolution additionally needs
    # the independently pinned, reviewed-current snapshot and full diff evidence.
    verified = verify_historical_audit_inputs(ROOT)
    assert all(verified[str(p.relative_to(ROOT)).replace("\\", "/")]["historical_observed_sha256"] == expected
               for p, expected in audited_inputs.items())
    assert verified["app/upstox_client.py"]["current_source_verified"] is True
    output = tmp_path / "nse_cm_session_manifest_v2.json"
    built = build_session_manifest_v2(V1, output)
    assert built["manifest_version"].endswith("-v2")
    assert {p: sha256_file(p) for p in preserved_paths} == before
    with pytest.raises(FileExistsError):
        build_session_manifest_v2(V1, output)

def test_content_hashes_are_repeatable_and_canonical():
    payload = b"fixture response bytes"
    assert canonical_json_hash({"b": 2, "a": 1}) == canonical_json_hash({"a": 1, "b": 2})
    assert sha256_file(V1) == sha256_file(V1)
    assert len(canonical_json_hash(payload.decode())) == 64


def test_explicit_timezone_conversion_and_naive_timestamp_rejection():
    original, utc, ist = normalize_timestamp("2023-12-01T03:45:00Z")
    assert original.endswith("Z")
    assert utc == "2023-12-01T03:45:00Z"
    assert ist == "2023-12-01T09:15:00+05:30"
    assert normalize_timestamp("2023-12-01T09:15:00+0530")[2] == ist
    with pytest.raises(ValueError, match="explicit UTC offset"):
        parse_explicit_timestamp("2023-12-01 09:15:00")


def test_v3_candle_normalization_uses_start_label_and_keeps_source_fields():
    row = candle("2023-12-01T03:45:00Z")
    assert row["timestamp_original"] == "2023-12-01T03:45:00Z"
    assert row["timestamp_offset"] == "Z"
    assert row["timestamp_ist"] == "2023-12-01T09:15:00+05:30"
    assert row["interval_label"] == "START"
    assert row["provider_volume"] == 250
    assert row["open_interest"] == 0
    validate_candle_schema(row)


def test_acquisition_manifest_redacts_secret_metadata():
    record = acquisition_record()
    encoded = json.dumps(record)
    assert "must-not-persist" not in encoded
    assert "never-store-me" not in encoded
    assert "access_token" in record["redacted_parameter_names"]
    assert "?" not in record["endpoint"]
    validate_acquisition_manifest(record)


def test_acquisition_manifest_rejects_secret_fields_and_bad_hash():
    record = acquisition_record()
    record["authorization"] = "Bearer abc123"
    with pytest.raises(ValueError, match="secret-bearing"):
        validate_acquisition_manifest(record)
    record = acquisition_record()
    record["raw_response_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_acquisition_manifest(record)


def test_duplicate_rows_and_malformed_ohlc_are_verified_invalid():
    dup_a, dup_b = candle(), candle()
    malformed = candle("2025-02-03T09:20:00+05:30", high=98.0)
    results, _ = validate_candle_records([dup_a, dup_b, malformed], MANIFEST)
    assert [r["validation_classification"] for r in results] == ["VERIFIED_INVALID"] * 3
    assert "duplicate_instrument_timestamp" in results[0]["validation_reasons"]
    assert "invalid_ohlc_relationship" in results[2]["validation_reasons"]


def test_missing_intervals_reported_without_filling():
    rows = [candle("2025-02-03T09:15:00+05:30"), candle("2025-02-03T09:25:00+05:30")]
    results, coverage = validate_candle_records(rows, MANIFEST)
    assert len(results) == 2
    day = next(x for x in coverage if x["date"] == "2025-02-03")
    assert day["expected_count"] == 75
    assert day["observed_count"] == 2
    assert day["missing_start_labels"][:2] == ["09:20", "09:30"]
    assert day["classification"] == "VERIFIED_INVALID"


def test_out_of_session_and_untrusted_session_are_distinct():
    out, _ = validate_candle_records([candle("2025-02-03T15:30:00+05:30")], MANIFEST)
    assert out[0]["validation_classification"] == "VERIFIED_INVALID"
    untrusted = copy.deepcopy(MANIFEST)
    session = next(item for item in untrusted["sessions"] if item["date"] == "2025-02-03")
    session["verification_status"] = "UNRESOLVED"
    rows, _ = validate_candle_records([candle()], untrusted)
    assert rows[0]["validation_classification"] == "UNRESOLVED"
    assert "session_manifest_unresolved" in rows[0]["validation_reasons"]


def test_naive_record_is_unresolved_not_silently_shifted():
    row = candle()
    row["timestamp_original"] = "2025-02-03 09:15:00"
    rows, _ = validate_candle_records([row], MANIFEST)
    assert rows[0]["validation_classification"] == "UNRESOLVED"
    assert "timestamp_timezone_unresolved" in rows[0]["validation_reasons"]
    assert rows[0]["timestamp_ist"] is None
def test_failed_provider_response_can_be_audited_without_response_hashes():
    record = acquisition_record(
        response={"status": "FAILED", "http_status": 504, "request_id": None},
        raw_response_sha256=None,
        normalized_output_sha256=None,
    )
    validate_acquisition_manifest(record)


def test_raw_retention_requires_provider_terms_review():
    record = acquisition_record(
        raw_response_retention={"enabled": True, "provider_terms_reviewed": False, "policy_version": "retention-v1"}
    )
    with pytest.raises(ValueError, match="provider-terms review"):
        validate_acquisition_manifest(record)


def test_immutable_manifest_record_is_idempotent_but_conflicts_fail(tmp_path):
    record = acquisition_record()
    first = write_acquisition_manifest_immutable(tmp_path, record)
    before = first.read_bytes()
    assert json.loads(before.decode("utf-8"))["acquisition_request_id"] == record["acquisition_request_id"]
    assert write_acquisition_manifest_immutable(tmp_path, record) == first
    assert first.read_bytes() == before
    altered = dict(record, provider_id="different-provider")
    with pytest.raises(FileExistsError, match="immutable"):
        write_acquisition_manifest_immutable(tmp_path, altered)
    assert first.read_bytes() == before


def test_checked_in_schema_documents_are_parseable_and_versioned():
    acq = json.loads((ROOT / "schemas/acquisition-manifest-v1.schema.json").read_text(encoding="utf-8"))
    candle_schema = json.loads((ROOT / "schemas/candle-record-v1.schema.json").read_text(encoding="utf-8"))
    assert acq["properties"]["schema_version"] == {"const": 1}
    assert candle_schema["properties"]["schema_version"] == {"const": 1}
    assert candle_schema["properties"]["interval_label"] == {"const": "START"}


def test_candle_schema_rejects_end_label_substitution():
    row = candle(interval_label="END")
    with pytest.raises(ValueError, match="START"):
        validate_candle_schema(row)
def test_nested_secret_parameters_are_removed_with_names_only():
    from app.offline_market_data_v1 import sanitize_parameters
    cleaned, names = sanitize_parameters({"filters": [{"from_date": "2025-01-01", "api_key": "do-not-save"}]})
    assert cleaned == {"filters": [{"from_date": "2025-01-01"}]}
    assert names == ["filters[0].api_key"]
def test_manifest_v2_diff_is_limited_to_one_amended_date_and_source():
    old = json.loads(V1.read_text(encoding="utf-8"))
    new = json.loads(V2.read_text(encoding="utf-8"))
    old_core = {k: v for k, v in old.items() if k not in {"schema_version", "manifest_version", "sources", "sessions"}}
    new_core = {k: v for k, v in new.items() if k not in {"schema_version", "manifest_version", "sources", "sessions"}}
    assert old_core == new_core
    assert new["sources"][:-1] == old["sources"]
    assert new["sources"][-1]["document_id"] == "NSE/CMTR/64960"
    old_days = {item["date"]: item for item in old["sessions"]}
    new_days = {item["date"]: item for item in new["sessions"]}
    assert set(old_days) == set(new_days)
    changed = [day for day in old_days if old_days[day] != new_days[day]]
    assert changed == ["2024-11-20"]
def test_quarantine_partition_keeps_invalid_and_unresolved_separate():
    valid, _ = validate_candle_records([candle()], MANIFEST)
    invalid, _ = validate_candle_records([candle("2025-02-03T15:30:00+05:30")], MANIFEST)
    unresolved_record = candle()
    unresolved_record["timestamp_original"] = "2025-02-03 09:15:00"
    unresolved, _ = validate_candle_records([unresolved_record], MANIFEST)
    buckets = partition_candle_records(valid + invalid + unresolved)
    assert len(buckets["VERIFIED_VALID"]) == 1
    assert len(buckets["VERIFIED_INVALID"]) == 1
    assert len(buckets["UNRESOLVED"]) == 1
    assert all(row["validation_reasons"] for rows in buckets.values() for row in rows)
def test_numeric_strings_do_not_pass_candle_schema_as_numbers():
    row = candle(high="102.0")
    with pytest.raises(ValueError, match="JSON number"):
        validate_candle_schema(row)
    rows, _ = validate_candle_records([row], MANIFEST)
    assert rows[0]["validation_classification"] == "VERIFIED_INVALID"