"""Offline observations only: no acquisition, orders, fills or settlement.

Decoded JSON fixtures are supported; this is not a WebSocket/Protobuf client.
Local ordinals order observations across all instruments and connections in one
capture. Hashes establish local integrity, not provider authenticity/completeness.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from app.research.ai_led_offline import _digest
from app.research.offline_session_calendar import OfflineSessionCalendar
from strategy_backtest import STOCKS

SCHEMA = "nse-market-observation-v1"
REPLAY_SCHEMA = "nse-observation-prefix-replay-v1"
CONTROLS = {"GAP", "DISCONNECT", "RECONNECT", "HEARTBEAT"}
PHASES = {"UNKNOWN", "NORMAL_OPEN", "NORMAL_CLOSE", "PRE_OPEN_START", "PRE_OPEN_END",
          "CLOSING_START", "CLOSING_END", "CTS_CLOSE", "CAS_LM_START", "CAS_M_STOP", "CAS_STOP"}


class ObservationError(ValueError):
    """Fixed reason code; no raw payload/exception text is propagated."""


def _stamp(value):
    if type(value) is str:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _provider_stamp(name, value):
    if name == "timestamp":
        return _stamp(value)
    if type(value) is int or (type(value) is str and re.fullmatch(r"[0-9]{1,16}", value)):
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    raise ValueError()


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError()
    return float(value)


def _quantity(value):
    if type(value) is str and re.fullmatch(r"[0-9]{1,16}", value):
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError()
    return value


def _levels(rows, price_key, quantity_key, reverse=False):
    if type(rows) is not list:
        raise ValueError()
    result = []
    for row in rows:
        price, quantity = _number(row[price_key]), _quantity(row[quantity_key])
        if price == quantity == 0:  # Explicit empty-level placeholders are not liquidity.
            continue
        if price <= 0 or quantity <= 0 or any(p == price for p, _ in result):
            raise ValueError()
        result.append((price, quantity))
    return tuple(sorted(result, reverse=reverse))


@dataclass(frozen=True)
class Observation:
    schema_version: str
    provider: str
    evidence_kind: str
    transport: str
    exchange: str
    segment: str
    instrument_key: str | None
    connection_id: str
    local_sequence: int
    received_at_utc: str
    kind: str
    provider_timestamps: tuple[tuple[str, str, str], ...]
    bids: tuple[tuple[float, int], ...]
    asks: tuple[tuple[float, int], ...]
    lower_circuit: float | None
    upper_circuit: float | None
    provider_phase: str
    freshness_status: str
    raw_payload_sha256: str
    sha256: str = ""


def _hash(observation):
    record = asdict(observation)
    record.pop("sha256")
    return _digest(record)


def normalize_observation(raw_payload, *, transport, instrument_key, connection_id,
                          local_sequence, received_at, kind, evidence_kind="SYNTHETIC_FIXTURE",
                          freshness_status="UNKNOWN"):
    """Pure normalization from original JSON bytes; never promotes freshness.

    VERIFIED is reserved for a future independently reviewed evidence contract.
    Caller-asserted INVALID can quarantine evidence; it cannot authorize trading.
    """
    try:
        if (type(raw_payload) is not bytes or not raw_payload or len(raw_payload) > 2 * 1024 * 1024
                or transport not in {"REST", "WEBSOCKET"} or kind not in CONTROLS | {"SNAPSHOT", "UPDATE"}
                or evidence_kind not in {"SYNTHETIC_FIXTURE", "RECORDED_UNAUTHENTICATED"}
                or type(connection_id) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", connection_id)
                or type(local_sequence) is not int or local_sequence <= 0):
            raise ObservationError("INVALID_ENVELOPE_INPUT")
        if freshness_status not in {"UNKNOWN", "INVALID"}:
            raise ObservationError("DEPTH_VERIFICATION_UNSUPPORTED")
        received = _stamp(received_at)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        raw = json.loads(raw_payload, object_pairs_hook=unique)
        if type(raw) is not dict:
            raise ValueError()
        fields, bids, asks, lower, upper, phase = [], (), (), None, None, "UNKNOWN"
        if kind in CONTROLS:
            if instrument_key is not None or raw.get("event") != kind.lower() or set(raw) - {"event", "currentTs"}:
                raise ObservationError("INVALID_CONTROL_MARKER")
            candidates = [("currentTs", raw.get("currentTs"), "FEED_TIME_UNESTABLISHED")]
        else:
            if type(instrument_key) is not str or instrument_key not in STOCKS.values() or not instrument_key.startswith("NSE_EQ|"):
                raise ObservationError("INVALID_INSTRUMENT")
            if transport == "REST":
                if kind != "SNAPSHOT" or raw.get("status") != "success" or type(raw.get("data")) is not dict:
                    raise ValueError()
                matches = [v for v in raw["data"].values() if type(v) is dict and v.get("instrument_token") == instrument_key]
                if len(matches) != 1:
                    raise ObservationError("INSTRUMENT_MISMATCH")
                item = matches[0]
                bids = _levels(item["depth"].get("buy", []), "price", "quantity", True)
                asks = _levels(item["depth"].get("sell", []), "price", "quantity")
                candidates = [("timestamp", item.get("timestamp"), "RESPONSE_GENERATED"),
                              ("last_trade_time", item.get("last_trade_time"), "LAST_TRADE_TIME")]
                lower, upper = item.get("lower_circuit_limit"), item.get("upper_circuit_limit")
                phase = item.get("market_phase", "UNKNOWN")
            else:
                expected_type = "initial_feed" if kind == "SNAPSHOT" else "live_feed"
                if raw.get("type") != expected_type:
                    raise ObservationError("SNAPSHOT_UPDATE_CLASSIFICATION_INVALID")
                item = raw["feeds"][instrument_key]["fullFeed"]["marketFF"]
                levels = item["marketLevel"]["bidAskQuote"]
                bids = _levels(levels, "bidP", "bidQ", True)
                asks = _levels(levels, "askP", "askQ")
                candidates = [("currentTs", raw.get("currentTs"), "FEED_TIME_UNESTABLISHED"),
                              ("ltt", item.get("ltpc", {}).get("ltt"), "LAST_TRADE_TIME")]
                phase = item.get("market_phase", "UNKNOWN")
            if bids and asks and bids[0][0] > asks[0][0]:
                raise ObservationError("CROSSED_MARKET")
            lower = None if lower is None else _number(lower)
            upper = None if upper is None else _number(upper)
            if (phase not in PHASES or (lower is not None and lower <= 0) or (upper is not None and upper <= 0)
                    or (lower is not None and upper is not None and lower > upper)
                    or any((lower is not None and p < lower) or (upper is not None and p > upper) for p, _ in bids + asks)):
                raise ObservationError("INVALID_PHASE_OR_CIRCUIT")
        for name, value, semantics in candidates:
            if value is not None:
                if _provider_stamp(name, value) > received:
                    raise ObservationError("FUTURE_PROVIDER_TIMESTAMP")
                fields.append((name, str(value), semantics))
        observation = Observation(SCHEMA, "upstox", evidence_kind, transport, "NSE", "EQ", instrument_key,
            connection_id, local_sequence, received.isoformat(), kind, tuple(fields), bids, asks,
            lower, upper, phase, freshness_status, hashlib.sha256(raw_payload).hexdigest())
        return replace(observation, sha256=_hash(observation))
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("MALFORMED_OBSERVATION") from None


@dataclass(frozen=True)
class ReplayView:
    observation: Observation
    books: tuple[Observation, ...]
    phase: str
    continuity: str
    post_reference: bool
    prefix_sha256: str


@dataclass(frozen=True)
class ReplayResult:
    views: tuple[ReplayView, ...]
    duplicate_count: int
    sha256: str
    trades_created: int = 0


def _interval(calendar, stamp):
    try:
        return next(((a, b) for a, b in calendar.intervals(stamp) if a <= stamp < b), None)
    except ValueError:
        return None


def replay_observations(observations, *, as_of, calendar, reference_time=None):
    """Replay a causal prefix by global local_sequence, never provider-time sort.

    Out-of-order submissions are reordered by their recorded local ordinal.
    A missing ordinal requires an explicit GAP marker. No state is persisted.
    """
    try:
        if (type(calendar) is not OfflineSessionCalendar or type(observations) not in (list, tuple)
                or any(type(o) is not Observation for o in observations)):
            raise ObservationError("INVALID_REPLAY_INPUT")
        cutoff = _stamp(as_of)
        reference = None if reference_time is None else _stamp(reference_time)
        digest = _digest({"schema_version": REPLAY_SCHEMA, "calendar": calendar.identity,
                          "reference_time": None if reference is None else reference.isoformat()})
        views, books, seen, provider_times, connections = [], {}, {}, {}, set()
        previous_sequence, previous_time, active, continuity, duplicates = 0, None, None, "AWAITING_SNAPSHOT", 0
        for event in sorted(observations, key=lambda o: o.local_sequence):
            received = _stamp(event.received_at_utc)
            if received > cutoff:
                break  # Future payload, hashes and prices are not inspected.
            try:
                valid_hash = event.sha256 == _hash(event)
            except Exception:
                valid_hash = False
            if not valid_hash or event.schema_version != SCHEMA or event.freshness_status not in {"UNKNOWN", "INVALID"}:
                raise ObservationError("OBSERVATION_INTEGRITY_INVALID")
            if event.local_sequence in seen:
                if seen[event.local_sequence] != event:
                    raise ObservationError("CONFLICTING_EVENT")
                duplicates += 1
                continue
            if event.local_sequence != previous_sequence + 1 and event.kind != "GAP":
                raise ObservationError("LOCAL_SEQUENCE_GAP_REQUIRES_MARKER")
            if previous_time is not None and received < previous_time:
                raise ObservationError("LOCAL_CLOCK_REGRESSION")
            if active is None:
                active = event.connection_id
                connections.add(active)
            elif event.connection_id != active:
                if event.kind != "RECONNECT" or continuity not in {"DISCONNECTED", "GAPPED"} or event.connection_id in connections:
                    raise ObservationError("UNMARKED_CONNECTION_CHANGE")
                active = event.connection_id
                connections.add(active)
            elif event.kind == "RECONNECT":
                raise ObservationError("RECONNECT_REQUIRES_NEW_CONNECTION")
            for name, value, semantics in event.provider_timestamps:
                if semantics == "LAST_TRADE_TIME":
                    continue  # Last trade time is not a book-update clock.
                key = (event.connection_id, event.instrument_key, name)
                stamp = _provider_stamp(name, value)
                if key in provider_times and stamp < provider_times[key]:
                    raise ObservationError("PROVIDER_CLOCK_REGRESSION")
                provider_times[key] = stamp
            interval = _interval(calendar, received)
            books = {key: book for key, book in books.items()
                     if interval is not None and _interval(calendar, _stamp(book.received_at_utc)) == interval}
            phase = "CONTROL" if event.kind in CONTROLS else "VERIFIED_CONTINUOUS" if interval else "OUTSIDE_SUPPORTED_SESSION"
            if event.kind in {"GAP", "DISCONNECT", "RECONNECT"}:
                books.clear()
                continuity = {"GAP": "GAPPED", "DISCONNECT": "DISCONNECTED", "RECONNECT": "AWAITING_SNAPSHOT"}[event.kind]
            elif event.kind in {"SNAPSHOT", "UPDATE"}:
                if continuity == "DISCONNECTED":
                    raise ObservationError("DATA_WHILE_DISCONNECTED")
                if event.provider_phase not in {"UNKNOWN", "NORMAL_OPEN"}:
                    phase = "PROVIDER_PHASE_BLOCKED"
                if phase != "VERIFIED_CONTINUOUS" or event.freshness_status == "INVALID":
                    books.pop(event.instrument_key, None)
                elif event.kind == "SNAPSHOT":
                    books[event.instrument_key] = event
                    continuity = "OBSERVABLE"
                elif event.instrument_key in books and continuity == "OBSERVABLE":
                    books[event.instrument_key] = event
                # Updates without a fresh snapshot never establish a book.
            digest = _digest({"previous": digest, "observation": event.sha256})
            views.append(ReplayView(event, tuple(books[key] for key in sorted(books)), phase, continuity,
                                    reference is not None and received > reference, digest))
            seen[event.local_sequence] = event
            previous_sequence, previous_time = event.local_sequence, received
        return ReplayResult(tuple(views), duplicates, digest)
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("INVALID_REPLAY_INPUT") from None


def displayed_depth_covers(observation, *, side, quantity):
    """Displayed top-level quantity only; never a fill/exit eligibility decision."""
    if type(observation) is not Observation or side not in {"BUY", "SELL"} or type(quantity) is not int or quantity <= 0:
        raise ObservationError("INVALID_DEPTH_QUERY")
    levels = observation.asks if side == "BUY" else observation.bids
    return bool(levels and levels[0][1] >= quantity)
