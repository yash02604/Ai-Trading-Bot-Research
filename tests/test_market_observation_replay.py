from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app.research import market_observation_replay as market
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.risk_engine import RiskConfig, calculate_position_size
from app.cost_model import estimate_round_trip_costs
from strategy_backtest import STOCKS

KEY = STOCKS["RELIANCE"]
START = pd.Timestamp("2026-11-02T12:00:00+05:30")


@pytest.fixture
def calendar():
    return OfflineSessionCalendar(Path(__file__).resolve().parents[1] / "data/audits/nse_cm_session_manifest_v3.json")


def fixture(sequence=1, *, transport="WEBSOCKET", kind="SNAPSHOT", connection="c1", seconds=None,
            key=KEY, bid=599.9, ask=600, bid_quantity=10, ask_quantity=10, phase="UNKNOWN",
            freshness="UNKNOWN", mutate=None, received=None):
    stamp = START + pd.Timedelta(seconds=sequence if seconds is None else seconds) if received is None else pd.Timestamp(received)
    millis = str(int(stamp.timestamp() * 1000))
    if kind in market.CONTROLS:
        raw = {"event": kind.lower()}
        key = None
    elif transport == "REST":
        raw = {"status": "success", "data": {"NSE_EQ:RELIANCE": {"instrument_token": key,
            "timestamp": stamp.isoformat(), "last_trade_time": millis, "market_phase": phase,
            "depth": {"buy": [{"price": bid, "quantity": bid_quantity}],
                      "sell": [{"price": ask, "quantity": ask_quantity}]}}}}
    else:
        raw = {"type": "initial_feed" if kind == "SNAPSHOT" else "live_feed", "currentTs": millis,
            "feeds": {key: {"fullFeed": {"marketFF": {"ltpc": {"ltt": millis}, "market_phase": phase,
                "marketLevel": {"bidAskQuote": [{"bidP": bid, "bidQ": str(bid_quantity),
                                                 "askP": ask, "askQ": str(ask_quantity)}]}}}}}}
    if mutate:
        mutate(raw)
    payload = json.dumps(raw, separators=(",", ":")).encode()
    result = market.normalize_observation(payload, transport=transport, instrument_key=key,
        connection_id=connection, local_sequence=sequence, received_at=stamp.isoformat(), kind=kind,
        freshness_status=freshness)
    assert result.raw_payload_sha256 == hashlib.sha256(payload).hexdigest()
    return result


def replay(events, calendar, as_of=None, reference=None):
    return market.replay_observations(events, calendar=calendar,
        as_of=as_of or (START + pd.Timedelta(minutes=1)).isoformat(), reference_time=reference)


@pytest.mark.parametrize("transport", ["REST", "WEBSOCKET"])
def test_immutable_envelope_timestamp_semantics_and_hashes(transport):
    event = fixture(transport=transport)
    assert event.provider == "upstox" and event.exchange == "NSE" and event.segment == "EQ"
    assert event.evidence_kind == "SYNTHETIC_FIXTURE" and event.freshness_status == "UNKNOWN"
    assert event.received_at_utc.endswith("+00:00")
    assert event.bids == ((599.9, 10),) and event.asks == ((600.0, 10),)
    assert event.sha256 == fixture(transport=transport).sha256
    assert all(semantics != "DEPTH_EVENT_TIME" for _, _, semantics in event.provider_timestamps)
    with pytest.raises(FrozenInstanceError):
        event.freshness_status = "VERIFIED"
    copy = asdict(event)
    copy["asks"] = ()
    assert event.asks


def test_initial_snapshot_updates_submission_order_and_duplicates(calendar):
    a, b = fixture(), fixture(2, kind="UPDATE", bid=600, ask=600.1)
    result = replay([b, a, b], calendar)
    assert result.duplicate_count == 1 and result.trades_created == 0
    assert result.views[-1].books == (b,)
    assert result.sha256 == replay([a, b], calendar).sha256
    assert result == replay([b, b, a], calendar)


def test_update_before_initial_snapshot_does_not_establish_a_book(calendar):
    result = replay([fixture(kind="UPDATE"), fixture(2)], calendar)
    assert result.views[0].continuity == "AWAITING_SNAPSHOT" and not result.views[0].books
    assert result.views[1].books


def test_missing_and_ambiguous_provider_timestamps_remain_unknown(calendar):
    def missing(raw):
        raw.pop("currentTs")
        raw["feeds"][KEY]["fullFeed"]["marketFF"]["ltpc"].pop("ltt")
    a = fixture(mutate=missing)
    b = fixture(2, kind="UPDATE")
    assert a.provider_timestamps == () and a.freshness_status == "UNKNOWN"
    assert any(semantics == "FEED_TIME_UNESTABLISHED" for _, _, semantics in b.provider_timestamps)
    assert all(book.freshness_status == "UNKNOWN" for book in replay([a, b], calendar).views[-1].books)


def test_provider_phase_and_session_change_require_a_fresh_snapshot(calendar):
    events = [fixture(), fixture(2, kind="UPDATE", phase="CTS_CLOSE"), fixture(3, kind="UPDATE"), fixture(4)]
    result = replay(events, calendar)
    assert not result.views[1].books and not result.views[2].books and result.views[3].books
    later = fixture(5, kind="UPDATE", received="2026-11-03T12:00:00+05:30")
    assert not replay(events + [later], calendar, as_of=later.received_at_utc).views[-1].books


def test_normalization_orders_depth_without_coercing_fractional_quantities():
    def unsorted(raw):
        depth = next(iter(raw["data"].values()))["depth"]
        depth["buy"] = [{"price": 599, "quantity": 2}, {"price": 599.9, "quantity": 3}]
        depth["sell"] = [{"price": 602, "quantity": 2}, {"price": 600, "quantity": 3}]
    a = fixture(transport="REST", mutate=unsorted)
    assert a.bids == ((599.9, 3), (599.0, 2)) and a.asks == ((600.0, 3), (602.0, 2))


def test_original_provider_timestamp_value_is_preserved_not_replaced_by_receipt():
    original = "2026-11-02T11:59:59.123+05:30"
    def older(raw):
        next(iter(raw["data"].values()))["timestamp"] = original
    a = fixture(transport="REST", mutate=older)
    assert ("timestamp", original, "RESPONSE_GENERATED") in a.provider_timestamps
    assert a.received_at_utc != original and a.freshness_status == "UNKNOWN"


@pytest.mark.parametrize("stamp", ["now", "today", "2026-11-02T12:00:00"])
def test_relative_and_naive_local_receipt_times_are_rejected(stamp):
    with pytest.raises(market.ObservationError, match="MALFORMED_OBSERVATION"):
        market.normalize_observation(b'{"event":"heartbeat"}', transport="WEBSOCKET", instrument_key=None,
            connection_id="c1", local_sequence=1, received_at=stamp, kind="HEARTBEAT")


def test_connection_ids_cannot_be_reused_on_reconnect(calendar):
    events = [fixture(), fixture(2, kind="DISCONNECT"), fixture(3, kind="RECONNECT", connection="c2"),
              fixture(4, kind="DISCONNECT", connection="c2"), fixture(5, kind="RECONNECT")]
    with pytest.raises(market.ObservationError, match="UNMARKED_CONNECTION_CHANGE"):
        replay(events, calendar)


def test_recorded_payload_label_does_not_authenticate_freshness():
    a = market.normalize_observation(b'{"event":"heartbeat"}', transport="WEBSOCKET", instrument_key=None,
        connection_id="saved1", local_sequence=1, received_at=START.isoformat(), kind="HEARTBEAT",
        evidence_kind="RECORDED_UNAUTHENTICATED")
    assert a.evidence_kind == "RECORDED_UNAUTHENTICATED" and a.freshness_status == "UNKNOWN"


def test_conflicting_same_ordinal_is_rejected(calendar):
    with pytest.raises(market.ObservationError, match="CONFLICTING_EVENT"):
        replay([fixture(), fixture(bid=599.8)], calendar)


def test_future_event_prices_and_hash_are_not_used_in_earlier_prefix(calendar):
    a, b = fixture(), fixture(2, kind="UPDATE", bid=590, ask=591)
    poisoned = replace(b, asks=((float("nan"), 100),))  # Invalid future hash/payload must remain unseen.
    cutoff = a.received_at_utc
    assert replay([a, poisoned], calendar, as_of=cutoff) == replay([a], calendar, as_of=cutoff)
    with pytest.raises(market.ObservationError, match="OBSERVATION_INTEGRITY_INVALID"):
        replay([a, poisoned], calendar)


def test_tampered_envelope_cannot_change_replay_without_detection(calendar):
    with pytest.raises(market.ObservationError, match="OBSERVATION_INTEGRITY_INVALID"):
        replay([replace(fixture(), received_at_utc="2026-11-02T06:30:00+00:00")], calendar)


def test_unknown_freshness_never_becomes_verified(calendar):
    a = fixture()
    assert replay([a], calendar).views[0].books[0].freshness_status == "UNKNOWN"
    with pytest.raises(market.ObservationError, match="DEPTH_VERIFICATION_UNSUPPORTED"):
        fixture(freshness="VERIFIED")
    forged = replace(a, freshness_status="VERIFIED")
    forged = replace(forged, sha256=market._hash(forged))
    with pytest.raises(market.ObservationError, match="OBSERVATION_INTEGRITY_INVALID"):
        replay([forged], calendar)


def test_invalid_freshness_is_quarantined_and_not_promoted(calendar):
    result = replay([fixture(freshness="INVALID")], calendar)
    assert not result.views[0].books and result.views[0].observation.freshness_status == "INVALID"


def test_explicit_gap_requires_new_snapshot_and_does_not_hide_price_path(calendar):
    events = [fixture(), fixture(3, kind="GAP"), fixture(4, kind="UPDATE"), fixture(5)]
    result = replay(events, calendar)
    assert result.views[1].continuity == result.views[2].continuity == "GAPPED"
    assert not result.views[1].books and not result.views[2].books
    assert result.views[3].books == (events[3],)
    with pytest.raises(market.ObservationError, match="LOCAL_SEQUENCE_GAP_REQUIRES_MARKER"):
        replay([events[0], fixture(3, kind="UPDATE")], calendar)


def test_disconnect_reconnect_and_heartbeat_never_restore_a_book(calendar):
    events = [fixture(), fixture(2, kind="DISCONNECT"), fixture(3, kind="RECONNECT", connection="c2"),
              fixture(4, kind="HEARTBEAT", connection="c2"), fixture(5, kind="UPDATE", connection="c2"),
              fixture(6, connection="c2")]
    result = replay(events, calendar)
    assert all(not view.books for view in result.views[1:5])
    assert result.views[-1].books == (events[-1],)
    assert result.trades_created == 0


def test_heartbeat_does_not_refresh_price_or_receipt_time(calendar):
    a = fixture()
    result = replay([a, fixture(2, kind="HEARTBEAT", seconds=30)], calendar)
    assert result.views[-1].books == (a,)
    assert result.views[-1].books[0].received_at_utc == a.received_at_utc


@pytest.mark.parametrize("kind,connection,reason", [
    ("UPDATE", "c2", "UNMARKED_CONNECTION_CHANGE"),
    ("RECONNECT", "c1", "RECONNECT_REQUIRES_NEW_CONNECTION")])
def test_unmarked_connection_changes_are_rejected(calendar, kind, connection, reason):
    with pytest.raises(market.ObservationError, match=reason):
        replay([fixture(), fixture(2, kind=kind, connection=connection)], calendar)


def test_data_while_disconnected_is_not_treated_as_live(calendar):
    with pytest.raises(market.ObservationError, match="DATA_WHILE_DISCONNECTED"):
        replay([fixture(), fixture(2, kind="DISCONNECT"), fixture(3, kind="UPDATE")], calendar)


def test_local_clock_regression_is_not_hidden_by_receipt_time_sort(calendar):
    with pytest.raises(market.ObservationError, match="LOCAL_CLOCK_REGRESSION"):
        replay([fixture(seconds=2), fixture(2, kind="UPDATE", seconds=1)], calendar)


def test_provider_clock_regression_is_detected_separately(calendar):
    def old(raw):
        raw["currentTs"] = str(int((START - pd.Timedelta(seconds=1)).timestamp() * 1000))
    with pytest.raises(market.ObservationError, match="PROVIDER_CLOCK_REGRESSION"):
        replay([fixture(), fixture(2, kind="UPDATE", mutate=old)], calendar)


@pytest.mark.parametrize("field,value", [("bid", float("nan")), ("ask", -1), ("ask_quantity", 1.5), ("bid_quantity", True)])
def test_malformed_prices_and_quantities_rejected(field, value):
    with pytest.raises(market.ObservationError, match="MALFORMED_OBSERVATION"):
        fixture(**{field: value})


def test_crossed_market_rejected():
    with pytest.raises(market.ObservationError, match="CROSSED_MARKET"):
        fixture(bid=601, ask=600)


def test_one_sided_circuit_book_and_insufficient_exit_depth_are_observations_only(calendar):
    def circuit(raw):
        item = next(iter(raw["data"].values()))
        item["lower_circuit_limit"], item["upper_circuit_limit"] = 600, 700
    event = fixture(transport="REST", bid=0, bid_quantity=0, ask=600, mutate=circuit)
    assert event.bids == () and event.lower_circuit == 600
    assert not market.displayed_depth_covers(event, side="SELL", quantity=1)
    assert market.displayed_depth_covers(event, side="BUY", quantity=1)  # Quantity only, not eligibility.
    low = fixture(2, kind="UPDATE", bid_quantity=1)
    assert not market.displayed_depth_covers(low, side="SELL", quantity=2)
    assert replay([event, low], calendar).trades_created == 0


def test_pre_reference_extrema_and_observed_stop_gap_never_generate_outcomes(calendar):
    events = [fixture(bid=580, ask=581), fixture(2, kind="UPDATE", bid=600, ask=601),
              fixture(3, kind="UPDATE", bid=590, ask=591)]
    result = replay(events, calendar, reference=events[1].received_at_utc)
    assert [v.post_reference for v in result.views] == [False, False, True]
    assert result.views[-1].books[0].bids[0][0] == 590  # Observed gap, not a fabricated stop-price fill.
    assert result.trades_created == 0 and not hasattr(result, "outcomes")


@pytest.mark.parametrize("stamp", ["2026-11-02T15:15:00+05:30", "2026-11-07T12:00:00+05:30",
    "2026-11-08T14:30:00+05:30", "2027-01-01T12:00:00+05:30"])
def test_session_end_weekend_special_and_uncovered_dates_are_quarantined(calendar, stamp):
    event = fixture(received=stamp)
    result = replay([event], calendar, as_of=stamp)
    assert result.views[0].phase == "OUTSIDE_SUPPORTED_SESSION" and not result.views[0].books


def test_later_verified_close_and_provider_auction_override(calendar):
    a = fixture(received="2026-12-07T15:20:00+05:30")
    assert replay([a], calendar, as_of=a.received_at_utc).views[0].phase == "VERIFIED_CONTINUOUS"
    b = fixture(2, kind="UPDATE", received="2026-12-07T15:20:01+05:30", phase="CAS_LM_START")
    result = replay([a, b], calendar, as_of=b.received_at_utc)
    assert result.views[-1].phase == "PROVIDER_PHASE_BLOCKED" and not result.views[-1].books


def test_heartbeat_at_close_cannot_keep_a_book_active(calendar):
    a = fixture(received="2026-11-02T15:14:59+05:30")
    b = fixture(2, kind="HEARTBEAT", received="2026-11-02T15:15:00+05:30")
    assert not replay([a, b], calendar, as_of=b.received_at_utc).views[-1].books


def test_multiple_stocks_have_independent_books_and_shared_gap(calendar):
    a, b = fixture(), fixture(2, key=STOCKS["HDFCBANK"])
    result = replay([b, a, fixture(3, kind="GAP")], calendar)
    assert len(result.views[1].books) == 2 and not result.views[2].books


@pytest.mark.parametrize("capital", [1000, 2500, 10000, 100000])
def test_replay_does_not_allocate_or_hard_code_capital(calendar, capital):
    result = replay([fixture()], calendar)
    config = RiskConfig(capital=capital)
    size = calculate_position_size(config, capital * .6, capital * .594)
    assert size.quantity == 1 and config.capital * config.max_risk_per_trade == capital * .01
    assert estimate_round_trip_costs(capital * .6, capital * .594, 1, START)["total_cost"] > 0
    assert result == replay([fixture()], calendar) and result.trades_created == 0


def test_raw_timestamp_future_duplicate_json_and_unknown_identity_rejected():
    with pytest.raises(market.ObservationError, match="FUTURE_PROVIDER_TIMESTAMP"):
        fixture(mutate=lambda raw: raw.update(currentTs=str(int((START + pd.Timedelta(seconds=10)).timestamp() * 1000))))
    with pytest.raises(market.ObservationError, match="INVALID_INSTRUMENT"):
        fixture(key="NYSE|FAKE")
    with pytest.raises(market.ObservationError, match="MALFORMED_OBSERVATION"):
        market.normalize_observation(b'{"status":"success","status":"failure"}', transport="REST", instrument_key=KEY,
            connection_id="c1", local_sequence=1, received_at=START.isoformat(), kind="SNAPSHOT")


def test_no_io_clock_provider_account_or_budget_access(calendar, monkeypatch):
    import builtins
    import socket
    from app import upstox_client
    from app.research import ai_led_offline
    from app.research.deepseek_prospective import PersistentRequestBudget
    from app.v2.provider import DeepSeekProvider
    def denied(*args, **kwargs):
        pytest.fail("offline observation/replay attempted side effects")
    monkeypatch.setattr(builtins, "open", denied)
    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(Path, "write_bytes", denied)
    monkeypatch.setattr(pd.Timestamp, "now", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(upstox_client, "_get", denied)
    monkeypatch.setattr(DeepSeekProvider, "complete", denied)
    monkeypatch.setattr(PersistentRequestBudget, "reserve", denied)
    monkeypatch.setattr(ai_led_offline, "_commit", denied)
    result = replay([fixture()], calendar)
    assert result.trades_created == 0
