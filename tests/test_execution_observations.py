import csv
from datetime import datetime
import hashlib
import sqlite3

import pandas as pd
import pytest

from app.cost_model import estimate_round_trip_costs
from app.execution_observations import parse_upstox_full_quote
from app.paper_store import EXECUTION_OBSERVATION_COLUMNS, PaperStore
from scripts.execution_cost_sensitivity import analyze, write_report
from scripts.export_execution_observations import export_and_summarize


TZ = "Asia/Kolkata"
OBSERVED = pd.Timestamp("2026-10-12 09:25:01", tz=TZ)


def quote_response(timestamp=None, bid=100.0, bid_quantity=40, ask=100.2, ask_quantity=75):
    quote = {"depth": {
        "buy": [{"price": bid, "quantity": bid_quantity}],
        "sell": [{"price": ask, "quantity": ask_quantity}],
    }}
    if timestamp is not None:
        quote["timestamp"] = timestamp
    return {"data": {"NSE_EQ:TEST": quote}}


def observation(observed_at="2026-10-12T09:25:01+05:30"):
    return {
        "signal_id": "signal-1", "observed_at": observed_at,
        "provider_timestamp": "2026-10-12T09:25:00+05:30",
        "provider_timestamp_kind": "response_generated",
        "source_timestamp_status": "not_exposed_by_quote_endpoint",
        "symbol": "TEST", "instrument_key": "NSE_EQ|TEST",
        "signal_timestamp": "2026-10-12T09:20:00+05:30",
        "entry_candle_time": "2026-10-12T09:25:00+05:30",
        "reference_price": 100.1, "ask_vs_reference_price": 0.1,
        "ask_vs_reference_bps": (0.1 / 100.1) * 10_000, "required_quantity": 50,
        "bid_price": 100.0, "bid_quantity": 40,
        "ask_price": 100.2, "ask_quantity": 75, "spread_price": 0.2,
        "spread_bps": 19.98, "spread_available": True, "spread_reason": "available",
        "quote_age_ms": 1000.0, "quote_valid": True, "quote_reason": "valid",
        "paper_entry_eligible": True, "eligibility_reason": "simulated_paper_fill",
        "simulated_entry_price": 100.2, "simulated_quantity": 50,
    }


def test_parse_valid_two_sided_depth_and_spread_units():
    result = parse_upstox_full_quote(
        quote_response(OBSERVED.isoformat()), OBSERVED,
    )
    assert result["quote_valid"] is True
    assert result["bid_price"] == 100.0 and result["bid_quantity"] == 40
    assert result["ask_price"] == 100.2 and result["ask_quantity"] == 75
    assert result["spread_price"] == pytest.approx(0.2)
    assert result["spread_bps"] == pytest.approx((0.2 / 100.1) * 10_000)
    assert result["quote_age_ms"] == 0
    assert result["source_quote_timestamp"] is None
    assert result["provider_timestamp_kind"] == "response_generated"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (quote_response(None), "missing_provider_timestamp"),
        (quote_response("not-a-timestamp"), "malformed_provider_timestamp"),
        (quote_response((OBSERVED - pd.Timedelta(seconds=6)).isoformat()), "stale_provider_timestamp"),
        (quote_response((OBSERVED + pd.Timedelta(seconds=1)).isoformat()), "future_provider_timestamp"),
        (quote_response(OBSERVED.isoformat(), ask=0, ask_quantity=0), "missing_ask_depth"),
        (quote_response(OBSERVED.isoformat(), bid=101, ask=100), "crossed_market"),
    ],
)
def test_invalid_timestamp_or_ask_data_fails_closed(response, reason):
    result = parse_upstox_full_quote(response, OBSERVED)
    assert result["quote_valid"] is False
    assert result["quote_reason"] == reason


@pytest.mark.parametrize(("identity", "valid"), [
    ({"instrument_token": "expected"}, True),
    ({"instrument_key": "expected"}, True),
    ({}, False),
    ({"instrument_token": "different"}, False),
    ({"instrument_token": "expected", "instrument_key": "different"}, False),
])
def test_requested_instrument_matches_quote_body(identity, valid):
    response = quote_response(OBSERVED.isoformat())
    response["data"]["NSE_EQ:TEST"].update(identity)
    result = parse_upstox_full_quote(response, OBSERVED, expected_instrument_key="expected")
    assert result["quote_valid"] is valid
    if not valid:
        assert result["quote_reason"] == "instrument_identity_mismatch"
        assert result["price"] is None


def test_missing_bid_is_recorded_without_changing_existing_ask_validity():
    response = quote_response(OBSERVED.isoformat())
    response["data"]["NSE_EQ:TEST"]["depth"].pop("buy")
    result = parse_upstox_full_quote(response, OBSERVED)
    assert result["quote_valid"] is True
    assert result["ask_price"] == 100.2
    assert result["spread_available"] is False
    assert result["spread_reason"] == "missing_bid_depth"


def test_malformed_depth_is_retained_as_invalid_observation():
    response = quote_response(OBSERVED.isoformat())
    response["data"]["NSE_EQ:TEST"]["depth"]["sell"] = [{"price": 100.2, "quantity": "bad"}]
    result = parse_upstox_full_quote(response, OBSERVED)
    assert result["quote_valid"] is False
    assert result["quote_reason"] == "malformed_depth_level"


def test_execution_observation_store_is_idempotent_append_only_and_exportable(tmp_path):
    store = PaperStore(tmp_path / "observations.sqlite3")
    first = observation()
    assert store.record_execution_observation(first) is True
    assert store.record_execution_observation(first) is False
    assert store.record_execution_observation(observation("2026-10-12T09:25:02+05:30")) is True
    store.commit()
    assert store.db.execute("SELECT COUNT(*) FROM execution_observations").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(
            "UPDATE execution_observations SET symbol='OTHER' WHERE signal_id='signal-1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM execution_observations WHERE signal_id='signal-1'")
    exported = store.export_execution_observations_csv(tmp_path / "export" / "quotes.csv")
    with exported.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["symbol"] == "TEST"
    assert rows[0]["source_quote_timestamp"] == ""
    assert rows[0]["paper_entry_eligible"] == "1"
    assert rows[0]["simulated_entry_price"] == "100.2"
    assert rows[0]["ask_vs_reference_price"] == "0.1"
    assert rows[0]["required_quantity"] == "50"
    store.close()
    before_export = hashlib.sha256((tmp_path / "observations.sqlite3").read_bytes()).hexdigest()
    summary = export_and_summarize(
        tmp_path / "observations.sqlite3", tmp_path / "summary_export",
    )
    after_export = hashlib.sha256((tmp_path / "observations.sqlite3").read_bytes()).hexdigest()
    assert after_export == before_export
    assert summary["observations"] == 2
    assert summary["quotes_with_spread"] == 2
    assert summary["source_quote_timestamp_available"] == 0
    assert summary["ask_quantity_sufficiency"] == {
        "comparable_observations": 2, "sufficient": 2, "insufficient": 0,
    }
    assert (tmp_path / "summary_export" / "observation_summary.md").exists()


def test_insufficient_depth_fixture_is_explicitly_rejected_in_summary(tmp_path):
    store = PaperStore(tmp_path / "insufficient.sqlite3")
    rejected = observation()
    rejected.update({
        "signal_id": "insufficient-depth", "required_quantity": 80,
        "ask_quantity": 75, "paper_entry_eligible": False,
        "eligibility_reason": "insufficient_ask_quantity",
        "simulated_entry_price": None, "simulated_quantity": None,
    })
    assert rejected["ask_quantity"] < rejected["required_quantity"]
    assert rejected["paper_entry_eligible"] is False
    assert rejected["eligibility_reason"] == "insufficient_ask_quantity"
    assert store.record_execution_observation(rejected)
    store.commit()
    store.close()
    summary = export_and_summarize(tmp_path / "insufficient.sqlite3", tmp_path / "insufficient-export")
    assert summary["paper_entry_eligible"] == 0
    assert summary["eligibility_reasons"] == {"insufficient_ask_quantity": 1}
    assert summary["ask_quantity_sufficiency"] == {
        "comparable_observations": 1, "sufficient": 0, "insufficient": 1,
    }


def test_exporter_supports_older_task4_schema_read_only(tmp_path):
    db_path = tmp_path / "older-task4.sqlite3"
    old_optional = {"ask_vs_reference_price", "ask_vs_reference_bps", "required_quantity"}
    old_columns = [column for column in EXECUTION_OBSERVATION_COLUMNS if column not in old_optional]
    integer_columns = {"bid_quantity", "ask_quantity", "spread_available", "quote_valid",
                       "paper_entry_eligible", "simulated_quantity"}
    real_columns = {"reference_price", "bid_price", "ask_price", "spread_price", "spread_bps",
                    "quote_age_ms", "simulated_entry_price"}
    declarations = []
    for column in old_columns:
        kind = "INTEGER" if column in integer_columns else "REAL" if column in real_columns else "TEXT"
        declarations.append(f"{column} {kind}")
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE execution_observations ({', '.join(declarations)})")
    old_record = observation()
    old_record["observation_id"] = "older-row"
    values = [old_record.get(column) for column in old_columns]
    conn.execute(
        f"INSERT INTO execution_observations ({','.join(old_columns)}) "
        f"VALUES ({','.join('?' for _ in old_columns)})", values,
    )
    conn.commit()
    before_columns = [row[1] for row in conn.execute("PRAGMA table_info(execution_observations)")]
    conn.close()
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    summary = export_and_summarize(db_path, tmp_path / "older-export")
    with (tmp_path / "older-export" / "execution_observations.csv").open(
        encoding="utf-8", newline="",
    ) as handle:
        exported = next(csv.DictReader(handle))
    assert summary["observations"] == 1
    assert exported["signal_id"] == "signal-1"
    assert exported["ask_vs_reference_price"] == ""
    assert exported["ask_vs_reference_bps"] == ""
    assert exported["required_quantity"] == ""

    conn = sqlite3.connect(db_path)
    after_columns = [row[1] for row in conn.execute("PRAGMA table_info(execution_observations)")]
    conn.close()
    assert after_columns == before_columns
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash


def test_exporter_rejects_missing_required_schema_columns_clearly(tmp_path):
    db_path = tmp_path / "incompatible.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE execution_observations (symbol TEXT)")
    conn.commit()
    conn.close()
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Incompatible execution_observations schema.*missing required columns"):
        export_and_summarize(db_path, tmp_path / "incompatible-export")
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash


def test_cost_sensitivity_separates_slippage_and_spread_without_double_counting(tmp_path):
    entry, exit_price, quantity = 100.0, 101.0, 10
    costs = estimate_round_trip_costs(entry, exit_price, quantity, datetime(2026, 10, 12).date())
    rows = [{
        "book": "portfolio", "symbol": "TEST", "signal_timestamp": "2026-10-12T09:20:00+05:30",
        "entry_timestamp": "2026-10-12T09:25:00+05:30", "entry_price": entry,
        "exit_price": exit_price, "quantity": quantity, "gross_pnl": 10.0,
        "net_pnl": 10.0 - costs["total_cost"], "total_cost": costs["total_cost"],
        "slippage": costs["slippage"], **{key: costs[key] for key in (
            "brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst",
        )},
    }, {
        "book": "standalone", "symbol": "IGNORED", "signal_timestamp": "2026-10-12T09:20:00+05:30",
        "entry_timestamp": "2026-10-12T09:25:00+05:30", "entry_price": entry,
        "exit_price": exit_price, "quantity": quantity, "gross_pnl": 10.0,
        "net_pnl": 0.0, "total_cost": 0.0, "slippage": 0.0,
        **{key: 0.0 for key in (
            "brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst",
        )},
    }]
    source = tmp_path / "trades.csv"
    pd.DataFrame(rows).to_csv(source, index=False)
    portfolio, scenarios, details = analyze(source)
    assert len(portfolio) == 1
    baseline = scenarios.set_index("scenario").loc["existing_model"]
    higher_slippage = scenarios.set_index("scenario").loc["higher_slippage"]
    spread = scenarios.set_index("scenario").loc["assumed_10bps_spread"]
    assert baseline["net_pnl_inr"] == pytest.approx(10.0 - costs["total_cost"])
    assert baseline["brokerage_inr"] == pytest.approx(costs["brokerage"])
    assert baseline["stt_inr"] == pytest.approx(costs["stt"])
    added_per_side = (entry + exit_price) * quantity * 5 / 10_000
    assert higher_slippage["total_costs_inr"] - baseline["total_costs_inr"] == pytest.approx(added_per_side)
    assert spread["assumed_spread_allowance_inr"] == pytest.approx(added_per_side)
    assert spread["modeled_slippage_inr"] == pytest.approx(costs["slippage"])
    assert len(details) == 5
    write_report(portfolio, scenarios, details, source, tmp_path / "report")
    report = (tmp_path / "report" / "execution_cost_sensitivity.md").read_text(encoding="utf-8")
    assert "₹10.00" in report and "10 bps" in report
    assert (tmp_path / "report" / "trade_sensitivity.csv").exists()
