"""Isolated offline journal for causal-forward-candle-v1. No transport or execution adapter."""
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd

from app.candle_integrity import validate_candles
from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.research import ai_led_offline as core
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_paper_fills import _stamp
from app.risk_engine import RiskConfig, RiskState, check_trade_allowed

MODE = "CAUSAL_FORWARD_PAPER_V1"
POLICY = "causal-forward-candle-v1"
BAR = pd.Timedelta(minutes=5)
OFFLINE_KINDS = ("UNVERIFIED_CALLER_SUPPLIED", "SYNTHETIC_TEST_DATA")
CANDLE_FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}
RETAINED_CALENDAR = Path(__file__).resolve().parents[2] / "data/audits/nse_cm_session_manifest_v3.json"


def _prices_valid(entry, stop, target):
    return (0 < stop < entry < target and 0.0005 <= (entry - stop) / entry <= 0.01 + 1e-12
            and (target - entry) / entry <= 0.05 + 1e-12)


def _freeze(db, body):
    digest = hashlib.sha256(body).hexdigest()
    row = db.execute("SELECT body FROM evidence WHERE sha256=?", (digest,)).fetchone()
    if row is None:
        db.execute("INSERT INTO evidence VALUES (?, ?)", (digest, body))
    elif row[0] != body:
        raise ValueError("frozen evidence integrity failure")
    return digest


def _evidence(db, digest):
    row = db.execute("SELECT body FROM evidence WHERE sha256=?", (digest,)).fetchone()
    if row is None or hashlib.sha256(row[0]).hexdigest() != digest:
        raise ValueError("frozen evidence integrity failure")
    return json.loads(row[0])


def _append(db, decision, slot, digest, snapshot):
    db.execute("INSERT INTO journal(decision,slot,evidence_sha256,body,body_sha256) VALUES (?,?,?,?,?)",
               (decision, slot, digest, core._encoded(snapshot), core._digest(snapshot)))
    return snapshot


def _decode(row):
    state = json.loads(row["body"])
    if core._digest(state) != row["body_sha256"]:
        raise ValueError("journal integrity failure")
    return state


def _clock(db, at):
    row = db.execute("SELECT * FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
    if row and at < _stamp(_decode(row)["recorded_at"]):
        raise ValueError("offline acceptance clock moved backwards")


def _quarantine(db, decision=None, slot=None, digest=None):
    row = db.execute("SELECT * FROM journal WHERE slot='conflict' LIMIT 1").fetchone()
    if row:
        record = _decode(row)
        if (row["decision"] == decision and record["disputed_slot"] == slot
                and record["conflicting_sha256"] == digest):
            return record
        raise ValueError("conflicting evidence; ledger quarantined for read-only audit")


def _conflict(db, decision, at, prior, digest):
    latest = db.execute("SELECT * FROM journal ORDER BY seq DESC LIMIT 1").fetchone()
    record = {"policy": POLICY, "decision_id": decision, "state": "INVALID_OR_CONFLICTING", "reason": "immutable_input_conflict",
              "recorded_at": max(at, _stamp(_decode(latest)["recorded_at"])).isoformat(),
              "observation_provenance": "INVALID_OR_CONFLICTING", "disputed_slot": prior["slot"],
              "original_sha256": prior["evidence_sha256"], "conflicting_sha256": digest}
    return _append(db, decision, "conflict", digest, record)


def _account(db, initial, day):
    # A missing post-entry observation keeps the trade unresolved and cash reserved.
    entries, closed = {}, {}
    for row in db.execute("SELECT * FROM journal ORDER BY seq"):
        record = _decode(row)
        if record["state"] == "OPEN" and record["bars_held"] == 0:
            entries[row["decision"]] = record
        elif record["state"].startswith("CLOSED_"):
            closed[row["decision"]] = record
    balance = initial + math.fsum(r["net_pnl"] for r in closed.values())
    open_ids = entries.keys() - closed.keys()
    reserved = math.fsum(entries[key]["entry_cash_required"] for key in open_ids)
    risk = RiskState(
        math.fsum(max(0, -r["net_pnl"]) for r in closed.values() if _stamp(r["exit_time"]).date() == day),
        len(open_ids), sum(_stamp(r["entry_accepted_at"]).date() == day for r in entries.values()))
    return {"balance": balance, "available_cash": min(initial, balance) - reserved,
            "reserved_cash": reserved, **asdict(risk)}


class CausalForwardPaper:
    """One experiment per previously empty directory. All clocks and inputs are offline."""
    def __init__(self, directory, *, capital, calendar_path=RETAINED_CALENDAR, provenance="UNVERIFIED_CALLER_SUPPLIED"):
        if provenance not in OFFLINE_KINDS or not core._number(capital) or capital <= 0:
            raise ValueError("invalid offline configuration")
        self._capital = capital
        self._kind = provenance
        if Path(calendar_path).resolve() != RETAINED_CALENDAR:
            raise ValueError("retained session manifest required")
        self._calendar = OfflineSessionCalendar(RETAINED_CALENDAR)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / "causal_forward_v1.sqlite3"
        if any(p.name not in {self._path.name, self._path.name + "-journal"} for p in directory.iterdir()):
            raise ValueError("dedicated causal policy directory required")
        app = Path(__file__).resolve().parents[1]
        sources = ["research/causal_forward_paper.py", "research/ai_led_offline.py",
                   "research/deepseek_prospective.py", "research/offline_session_calendar.py",
                   "research/prospective_paper_fills.py", "risk_engine.py", "cost_model.py",
                   "candle_integrity.py", "session_manifest_v3.py", "offline_market_data_v1.py"]
        config = {"mode": MODE, "policy": POLICY, "capital": capital, "provenance": provenance,
                  "calendar": self._calendar.identity, "risk": asdict(RiskConfig(capital=capital)),
                  "costs": asdict(CostAssumptions()), "instrument_keys": dict(core.STOCKS),
                  "source_hashes": {p: hashlib.sha256((app / p).read_bytes()).hexdigest() for p in sources}}
        with self._transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {"settings", "journal", "evidence"}:
                raise ValueError("unrecognized ledger")
            if not tables:
                db.execute("CREATE TABLE settings(config BLOB NOT NULL)")
                db.execute("CREATE TABLE evidence(sha256 TEXT PRIMARY KEY, body BLOB NOT NULL)")
                db.execute("""CREATE TABLE journal(seq INTEGER PRIMARY KEY, decision TEXT NOT NULL,
                    slot TEXT NOT NULL, evidence_sha256 TEXT NOT NULL REFERENCES evidence(sha256),
                    body BLOB NOT NULL, body_sha256 TEXT NOT NULL, UNIQUE(decision,slot))""")
                db.execute("INSERT INTO settings VALUES (?)", (core._encoded(config),))
                for table in ("settings", "journal", "evidence"):
                    for op in ("UPDATE", "DELETE"):
                        db.execute(f"""CREATE TRIGGER {table}_{op} BEFORE {op} ON {table}
                            BEGIN SELECT RAISE(ABORT,'immutable causal ledger'); END""")
                for table, duplicate in (("settings", "1"), ("evidence", "sha256=NEW.sha256"),
                                         ("journal", "seq=NEW.seq OR (decision=NEW.decision AND slot=NEW.slot)")):
                    db.execute(f"""CREATE TRIGGER {table}_duplicate BEFORE INSERT ON {table}
                        WHEN EXISTS(SELECT 1 FROM {table} WHERE {duplicate})
                        BEGIN SELECT RAISE(ABORT,'immutable causal ledger'); END""")
            elif [row[0] for row in db.execute("SELECT config FROM settings")] != [core._encoded(config)]:
                raise ValueError("immutable causal configuration mismatch")
            else:
                for row in db.execute("SELECT * FROM journal"):
                    _decode(row)
                    _evidence(db, row["evidence_sha256"])

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self._path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db

    def history(self, decision_id):
        with self._transaction() as db:
            history = []
            for row in db.execute("SELECT * FROM journal WHERE decision=? ORDER BY seq", (decision_id,)):
                _evidence(db, row["evidence_sha256"])
                history.append(_decode(row))
            return history

    def evidence(self, digest):
        with self._transaction() as db:
            return _evidence(db, digest)

    def _entry_allowed(self, at):
        session = self._calendar.sessions.get(at.date().isoformat(), {})
        # Older special-session rows can describe trading hours without granting
        # this policy execution permission (including historical Muhurat dates).
        if session.get("special_session") and session.get("execution_supported") is not True:
            return False
        return self._calendar.entry_allowed(at)

    def account(self, as_of):
        with self._transaction() as db:
            _quarantine(db)
            stamp = _stamp(as_of)
            _clock(db, stamp)
            return _account(db, self._capital, stamp.date())

    def propose(self, *, decision_id, decision_ready_at, proposal, instrument_key, quantity,
                four_index_down_count=None):
        """Accept an upstream selected BUY; no provider, prompt or ranking runs here."""
        ready = _stamp(decision_ready_at)
        if type(decision_id) is not str or not decision_id.strip():
            raise ValueError("decision ID required")
        if (set(proposal) != {"symbol", "score", "entry_price", "stop_loss", "target_price", "holding_bars"}
                or proposal["symbol"] not in core.STOCKS
                or not all(core._number(proposal[k]) for k in ("entry_price", "stop_loss", "target_price", "score"))
                or not 0 <= proposal["score"] <= 1 or type(proposal["holding_bars"]) is not int
                or not 1 <= proposal["holding_bars"] <= 12 or type(quantity) is not int or quantity < 1):
            raise ValueError("invalid upstream BUY proposal")
        if four_index_down_count is not None and (type(four_index_down_count) is not int
                                                  or not 0 <= four_index_down_count <= 4):
            raise ValueError("invalid comparator metadata")
        request = {"decision_id": decision_id, "decision_ready_at": ready.isoformat(), "proposal": proposal,
                   "instrument_key": instrument_key, "quantity": quantity, "four_index_down_count": four_index_down_count}
        with self._transaction() as db:
            digest = _freeze(db, core._encoded(request))
            conflict = _quarantine(db, decision_id, "selection", digest)
            if conflict:
                return conflict
            prior = db.execute("SELECT * FROM journal WHERE decision=? AND slot='selection'", (decision_id,)).fetchone()
            if prior:
                if prior["evidence_sha256"] != digest:
                    return _conflict(db, decision_id, ready, prior, digest)
                return _decode(prior)
            _clock(db, ready)
            if db.execute("SELECT 1 FROM journal WHERE slot='selection' AND json_extract(CAST(body AS TEXT),'$.decision_ready_at')=? "
                          "AND json_extract(CAST(body AS TEXT),'$.instrument_key')=?", (ready.isoformat(), instrument_key)).fetchone():
                raise ValueError("duplicate instrument decision at this time")
            state = {**request, "policy": POLICY, "mode": MODE, "provenance": self._kind,
                     "recorded_at": ready.isoformat(), "decision_sha256": digest,
                     "state": "PROPOSED", "bars_held": 0, "trade_placement_enabled": False}
            _append(db, decision_id, "proposal", digest, state)
            start = ready.floor("5min") + BAR
            reason = None
            try:
                if (not self._entry_allowed(ready)
                        or self._calendar.candle_end(start) != self._calendar.candle_end(ready.floor("5min"))):
                    reason = "unsupported_session_or_entry_cutoff"
            except ValueError:
                reason = "unsupported_session_or_entry_cutoff"
            if core.STOCKS[proposal["symbol"]] != instrument_key:
                reason = "instrument_mismatch"
            elif not _prices_valid(proposal["entry_price"], proposal["stop_loss"], proposal["target_price"]):
                reason = "invalid_proposal_prices"
            state.update(state="ENTRY_REJECTED" if reason else "ENTRY_PENDING", reason=reason,
                         next_start=None if reason else start.isoformat())
            return _append(db, decision_id, "selection", digest, state)

    def observe(self, decision_id, *, expected_start, as_of, observation):
        """Consume one identified five-minute slot, or explicitly publish None for missing."""
        start, at = _stamp(expected_start), _stamp(as_of)
        if at < start + BAR:
            raise ValueError("slot is not completed")
        slot = start.isoformat()
        with self._transaction() as db:
            prior = db.execute("SELECT * FROM journal WHERE decision=? AND slot=?", (decision_id, slot)).fetchone()
            envelope = {"provenance": self._kind, "slot": slot, "observation": observation}
            try:
                body = core._encoded(envelope)
            except ValueError:
                # Preserve JSON numeric extensions as invalid text; they cannot be
                # replaced with a corrected price on a subsequent delivery.
                body = core._encoded({"provenance": "INVALID_OR_CONFLICTING",
                                      "invalid_json": json.dumps(envelope, sort_keys=True)})
                observation = {}
            digest = _freeze(db, body)
            conflict = _quarantine(db, decision_id, slot, digest)
            if conflict:
                return conflict
            if prior:
                if prior["evidence_sha256"] != digest:
                    return _conflict(db, decision_id, at, prior, digest)
                if at < _stamp(_decode(prior)["recorded_at"]):
                    raise ValueError("retry precedes original acceptance")
                return _decode(prior)
            latest = db.execute("SELECT * FROM journal WHERE decision=? ORDER BY seq DESC LIMIT 1",
                                (decision_id,)).fetchone()
            if not latest:
                raise ValueError("unknown decision")
            state = _decode(latest)
            if state["state"] not in ("OPEN", "ENTRY_PENDING") or slot != state["next_start"]:
                raise ValueError("terminal decision or unexpected slot; publish missing slot explicitly")
            _clock(db, at)
            state["recorded_at"] = at.isoformat()
            state.setdefault("observation_hashes", []).append(digest)
            try:
                bar = self._validate_observation(observation, state, start, at)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                state.update(state="OBSERVATION_MISSING", reason=str(exc),
                             observation_provenance="INVALID_OR_CONFLICTING" if observation is not None else self._kind)
            else:
                if state["state"] == "ENTRY_PENDING":
                    self._enter(db, state, bar["close"], at, start)
                else:
                    self._monitor(state, bar, start)
            if state["state"] != "OPEN":
                state["next_start"] = None
            return _append(db, decision_id, slot, digest, state)

    def _validate_observation(self, obs, state, start, at):
        if obs is None:
            raise ValueError("missing_expected_candle")
        if type(obs) is not dict or set(obs) != {"symbol", "instrument_key", "observed_at", "candle"}:
            raise ValueError("invalid_observation_envelope")
        if (obs["symbol"] != state["proposal"]["symbol"] or obs["instrument_key"] != state["instrument_key"]
                or core.STOCKS[obs["symbol"]] != obs["instrument_key"]):
            raise ValueError("instrument_mismatch")
        bar = obs["candle"]
        if type(bar) is not dict or set(bar) != CANDLE_FIELDS or _stamp(bar["timestamp"]) != start:
            raise ValueError("unexpected_candle")
        received = _stamp(obs["observed_at"])
        if not start + BAR <= received <= at < start + 2 * BAR:
            raise ValueError("incomplete_or_stale_observation")
        self._calendar.candle_end(start)
        if (any(not core._number(bar[k]) for k in CANDLE_FIELDS - {"timestamp"})
                or bar["volume"] <= 0 or not validate_candles(pd.DataFrame([bar])).valid):
            raise ValueError("invalid_candle")
        return bar

    def _enter(self, db, state, price, at, start):
        p = state["proposal"]
        reason = None
        account = _account(db, self._capital, at.date())
        risk = RiskState(account["daily_loss"], account["open_positions"], account["trades_today"])
        allowed, risk_reason = check_trade_allowed(RiskConfig(capital=self._capital), risk)
        if not self._entry_allowed(at) or at >= self._calendar.candle_end(start):
            reason = "entry_cutoff"
        elif not _prices_valid(price, p["stop_loss"], p["target_price"]):
            reason = "invalid_causal_entry_prices"
        elif not allowed:
            reason = risk_reason
        elif account["available_cash"] <= 0:
            reason = "insufficient_available_cash"
        else:
            check = core._check({"config": {"initial_capital": self._capital}, "cash": account["available_cash"]},
                                price, p["stop_loss"], p["target_price"], state["quantity"], at)
            state["entry_check"] = check
            if not check["eligible"]:
                reason = check["reason"]
        if reason:
            state.update(state="ENTRY_REJECTED", reason=reason, causal_reference_close=price)
            return
        state.update(state="OPEN", entry_price=price, entry_accepted_at=at.isoformat(),
                     entry_reference_at=(start + BAR).isoformat(), next_start=(start + BAR).isoformat(),
                     entry_cash_required=check["entry_cash_required"], expected_net_target_pnl=check["estimated_net_at_target"],
                     expected_gross_target_pnl=(p["target_price"] - price) * state["quantity"],
                     mfe_price=0.0, mae_price=0.0, mfe_upper_price=0.0, mae_lower_price=0.0,
                     partial_first_bar=False, same_bar_ambiguous=False)

    def _monitor(self, state, bar, start):
        p = state["proposal"]
        stop, target = p["stop_loss"], p["target_price"]
        low, high, opening, close = (bar[k] for k in ("low", "high", "open", "close"))
        hit_stop, hit_target = low <= stop, high >= target
        partial = start < _stamp(state["entry_accepted_at"])
        close_confirms_only_hit = ((close <= stop and not hit_target) or (close >= target and not hit_stop))
        if partial and (hit_stop or hit_target) and not close_confirms_only_hit:
            state.update(state="OBSERVATION_MISSING", reason="threshold_timing_overlaps_entry_acceptance",
                         partial_first_bar=True)
            return
        state["partial_first_bar"] |= partial
        state["bars_held"] += 1
        entry = state["entry_price"]
        end = start + BAR
        reason = None
        price = close
        if hit_stop:
            reason = "CLOSED_AMBIGUOUS_STOP_FIRST" if hit_target else "CLOSED_STOP"
            price = close if partial else min(opening, stop)
        elif hit_target:
            reason, price = "CLOSED_TARGET", target
        elif end == self._calendar.candle_end(start):
            reason, price = "CLOSED_SESSION", close
        elif state["bars_held"] >= p["holding_bars"]:
            reason, price = "CLOSED_HORIZON", close
        # Record confirmed excursions separately from whole-candle outer bounds.
        # An intrabar exit can precede the opposite extreme of its exit candle.
        known_low, known_high = low, high
        if partial:
            known_low = known_high = close
        elif hit_stop or hit_target:
            known_low, known_high = min(opening, price), max(opening, price)
            if opening <= stop or (opening >= target and not hit_stop):
                low, high = known_low, known_high
        state["mfe_price"] = max(state["mfe_price"], known_high - entry)
        state["mae_price"] = min(state["mae_price"], known_low - entry)
        state["mfe_upper_price"] = max(state["mfe_upper_price"], high - entry)
        state["mae_lower_price"] = min(state["mae_lower_price"], low - entry)
        if reason is None:
            state["next_start"] = end.isoformat()
            return
        costs = estimate_round_trip_costs(entry, price, state["quantity"], end.date())
        gross = (price - entry) * state["quantity"]
        if any(not core._number(v) or v < 0 for v in costs.values()) or not math.isfinite(gross - costs["total_cost"]):
            raise ValueError("invalid settlement")
        exit_time, time_kind = end, "candle_close"
        if (hit_stop or hit_target) and not partial:
            if opening <= stop or (opening >= target and not hit_stop):
                exit_time, time_kind = start, "candle_open"
            else:
                time_kind = "intrabar_time_unknown_end_bound"
        state.update(state=reason, exit_reason=reason, exit_price=price, exit_time=exit_time.isoformat(),
                     exit_time_kind=time_kind, exit_interval_start=start.isoformat(), exit_interval_end=end.isoformat(),
                     exit_observed_at=state["recorded_at"], same_bar_ambiguous=hit_stop and hit_target,
                     exit_bar=bar, gross_pnl=gross, cost_components=costs, modeled_costs=costs["total_cost"],
                     net_pnl=gross - costs["total_cost"])
