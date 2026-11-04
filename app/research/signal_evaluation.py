"""Pure prediction evaluation: subsequent price movement, never executable P&L."""
import copy
import re

import pandas as pd

from app.candle_integrity import validate_candles
from app.research.prospective_paper_fills import _stamp
from app.research.ai_led_offline import _digest, _number
from strategy_backtest import STOCKS

POLICY = {"version": "signal-movement-evaluation-v1", "horizon_minutes": 30,
          "start": "first_full_5m_boundary_after_decision_ready", "bar_count": 6,
          "reference": "last_completed_close_in_decision_batch",
          "baseline": "frozen_reversal_predicate_at_same_candidate", "same_bar_hits": "AMBIGUOUS",
          "calibration_min_samples": 30, "confidence_bins": 5, "execution_profitability": False}


def evaluation_policy():
    return copy.deepcopy(POLICY)


def evaluate_signal(decision, future_snapshots, *, as_of, calendar):
    """Inputs are saved numeric candle bodies, not paths or network providers.

    Caller must load an integrity-checked signal record. Numeric future bodies
    have local hashes, not verified acquisition provenance or provider authorship.
    """
    if decision["mode"] != "AI_SIGNAL_ONLY" or decision["evaluation_policy"] != POLICY:
        raise ValueError("signal_evaluation_policy_or_mode_invalid")
    result = {"decision_id": decision["decision_id"], "policy_sha256": _digest(POLICY),
              "decision_sha256": _digest(decision),
              "status": "NOT_A_VALID_PREDICTION", "rows": [], "execution_pnl": None,
              "trade_placement_enabled": False, "depth_freshness": "UNKNOWN"}
    if decision["status"] != "VALID_PREDICTION":
        return result
    ready, cutoff = _stamp(decision["decision_ready_at"]), _stamp(as_of)
    if cutoff < ready:
        raise ValueError("evaluation_before_decision_ready")
    start = ready.floor("5min") + pd.Timedelta(minutes=5)
    expected = pd.date_range(start, periods=POLICY["bar_count"], freq="5min")
    result.update(status="EVALUATED", action=decision["prediction"]["action"], as_of=cutoff.isoformat(),
                  window_start=start.isoformat(), window_end=(expected[-1] + pd.Timedelta(minutes=5)).isoformat())
    proposals = {p["symbol"]: p for p in decision["prediction"]["proposals"]}
    for symbol in decision["instrument_priority"]:
        row = {"symbol": symbol, "status": "MISSING_FUTURE_DATA", "direction": None,
               "return_pct": None, "target_stop_order": None,
               "ai_selected": symbol in proposals, "baseline_selected": decision["baseline"][symbol],
               "confidence": proposals[symbol]["score"] if symbol in proposals else None}
        if cutoff < expected[-1] + pd.Timedelta(minutes=5):
            row["status"] = "PENDING_FUTURE_DATA"
            result["rows"].append(row)
            continue
        if symbol not in future_snapshots:
            result["rows"].append(row)
            continue
        try:
            body = future_snapshots[symbol]
            row["observation_evidence"] = {"input_sha256": _digest(body), "source": body.get("source"),
                "observed_at": body.get("observed_at"), "symbol": body.get("symbol"),
                "instrument_key": body.get("instrument_key"), "provenance": "UNVERIFIED_CALLER_SUPPLIED"}
            observed = _stamp(body["observed_at"])
            if not ready < observed <= cutoff or body["instrument_key"] != STOCKS[symbol] or body["symbol"] != symbol:
                raise ValueError()
            if type(body["candles"]) is not list:
                raise ValueError()
            if not body["candles"]:
                result["rows"].append(row)
                continue
            frame = pd.DataFrame(body["candles"])
            frame["timestamp"] = frame.timestamp.map(_stamp)
            if validate_candles(frame).errors:
                raise ValueError()
            # Only complete, wholly post-decision bars in the frozen horizon.
            frame = frame.loc[frame.timestamp.isin(expected) & (frame.timestamp + pd.Timedelta(minutes=5) <= observed)]
            frame = frame.sort_values("timestamp").reset_index(drop=True)
            if frame.timestamp.tolist() != list(expected):
                result["rows"].append(row)
                continue
            interval = next((i for i in calendar.intervals(ready) if i[0] <= ready < i[1]), None)
            if interval is None or expected[-1] + pd.Timedelta(minutes=5) > interval[1]:
                raise ValueError()
            price = float(frame.iloc[-1].close)
            reference = decision["reference_prices"][symbol]
            movement = (price / reference - 1) * 100
            row.update(status="OBSERVED_PRICE_MOVEMENT", return_pct=movement,
                       direction="UP" if movement > 0 else "DOWN" if movement < 0 else "FLAT",
                       input_sha256=_digest(body), target_stop_order="NOT_APPLICABLE")
            if symbol in proposals:
                p = proposals[symbol]
                order = "NEITHER_OBSERVED"
                for bar in frame.itertuples():
                    stop, target = bar.low <= p["stop_loss"], bar.high >= p["target_price"]
                    if stop or target:
                        order = "AMBIGUOUS" if stop and target else "STOP_OBSERVED_FIRST" if stop else "TARGET_OBSERVED_FIRST"
                        break
                row["target_stop_order"] = order
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            row["status"] = "INVALID_FUTURE_DATA"
        result["rows"].append(row)
    if any(r["status"] == "INVALID_FUTURE_DATA" for r in result["rows"]):
        result["status"] = "INVALID_FUTURE_DATA"
    elif any(r["status"] == "PENDING_FUTURE_DATA" for r in result["rows"]):
        result["status"] = "PENDING_FUTURE_DATA"
    elif any(r["status"] != "OBSERVED_PRICE_MOVEMENT" for r in result["rows"]):
        result["status"] = "INCOMPLETE_FUTURE_DATA"
    return result


def summarize_predictions(decisions, evaluations, *, as_of=None):
    """Reconcile the full recorded universe; omitted outcomes never disappear.

    Without an explicit as_of, omitted rows are missing, never presumed pending.
    Ambiguous stop/target ordering is a subset of completed movement evaluations.
    """
    if len({d["decision_id"] for d in decisions}) != len(decisions) or len({e["decision_id"] for e in evaluations}) != len(evaluations):
        raise ValueError("duplicate_signal_or_evaluation")
    identities = {d["decision_id"]: _digest(d) for d in decisions}
    if (any(d["mode"] != "AI_SIGNAL_ONLY" or d["evaluation_policy"] != POLICY for d in decisions)
            or any(e["decision_id"] not in identities or e["policy_sha256"] != _digest(POLICY)
                   or e.get("decision_sha256") != identities.get(e["decision_id"]) for e in evaluations)):
        raise ValueError("prediction_evaluation_identity_mismatch")
    valid = {d["decision_id"]: d for d in decisions if d["status"] == "VALID_PREDICTION"}
    supplied = {e["decision_id"]: e for e in evaluations}
    cutoff = _stamp(as_of) if as_of is not None else None
    accounting = []
    for decision_id, d in valid.items():
        universe = d["instrument_priority"]
        proposals = {p["symbol"]: p for p in d["prediction"]["proposals"]}
        if (len(set(universe)) != len(universe) or not universe or any(s not in STOCKS for s in universe)
                or not set(proposals) <= set(universe) or len(proposals) != len(d["prediction"]["proposals"])
                or d["prediction"]["action"] not in {"BUY", "NO_TRADE"}
                or bool(proposals) != (d["prediction"]["action"] == "BUY")):
            raise ValueError("invalid_prediction_universe_or_action")
        ready = _stamp(d["decision_ready_at"])
        start = ready.floor("5min") + pd.Timedelta(minutes=5)
        end = start + pd.Timedelta(minutes=5 * POLICY["bar_count"])
        e = supplied.get(decision_id)
        indexed = {}
        if e is not None:
            observed = _stamp(e["as_of"])
            if (observed < ready or (cutoff is not None and observed != cutoff)
                    or e["action"] != d["prediction"]["action"]
                    or _stamp(e["window_start"]) != start or _stamp(e["window_end"]) != end
                    or e["execution_pnl"] is not None or e["trade_placement_enabled"] is not False):
                raise ValueError("conflicting_evaluation_evidence")
            for row in e["rows"]:
                symbol = row["symbol"]
                if symbol not in universe or symbol in indexed:
                    raise ValueError("duplicate_or_unknown_evaluation_instrument")
                selected = symbol in proposals
                if (type(row["ai_selected"]) is not bool or row["ai_selected"] != selected
                        or type(row["baseline_selected"]) is not bool or row["baseline_selected"] != d["baseline"][symbol]
                        or row["confidence"] != (proposals[symbol]["score"] if selected else None)):
                    raise ValueError("conflicting_evaluation_selection")
                status = row["status"]
                if status not in {"OBSERVED_PRICE_MOVEMENT", "PENDING_FUTURE_DATA", "MISSING_FUTURE_DATA", "INVALID_FUTURE_DATA"}:
                    raise ValueError("invalid_evaluation_status")
                if (status == "PENDING_FUTURE_DATA") != (observed < end):
                    raise ValueError("conflicting_evaluation_horizon")
                if status == "OBSERVED_PRICE_MOVEMENT":
                    movement = row["return_pct"]
                    proof = row.get("observation_evidence", {})
                    orders = {"AMBIGUOUS", "STOP_OBSERVED_FIRST", "TARGET_OBSERVED_FIRST", "NEITHER_OBSERVED"} if selected else {"NOT_APPLICABLE"}
                    if (not _number(movement) or row["direction"] != ("UP" if movement > 0 else "DOWN" if movement < 0 else "FLAT")
                            or row["target_stop_order"] not in orders
                            or type(row.get("input_sha256")) is not str
                            or not re.fullmatch(r"[0-9a-f]{64}", row["input_sha256"])
                            or proof.get("input_sha256") != row["input_sha256"]
                            or proof.get("symbol") != symbol or proof.get("instrument_key") != STOCKS[symbol]
                            or proof.get("provenance") != "UNVERIFIED_CALLER_SUPPLIED"
                            or not end <= _stamp(proof["observed_at"]) <= observed):
                        raise ValueError("conflicting_evaluation_outcome")
                elif any(row[k] is not None for k in ("return_pct", "direction", "target_stop_order")):
                    raise ValueError("incomplete_evaluation_has_outcome")
                indexed[symbol] = row
            statuses = {r["status"] for r in indexed.values()}
            expected_status = ("INVALID_FUTURE_DATA" if "INVALID_FUTURE_DATA" in statuses else
                               "PENDING_FUTURE_DATA" if "PENDING_FUTURE_DATA" in statuses else
                               "INCOMPLETE_FUTURE_DATA" if "MISSING_FUTURE_DATA" in statuses else "EVALUATED")
            if e["status"] != expected_status:
                raise ValueError("conflicting_evaluation_status")
        for symbol in universe:
            row = indexed.get(symbol)
            if row is None:
                clock = cutoff if cutoff is not None else (_stamp(e["as_of"]) if e is not None else None)
                if clock is not None and clock < ready:
                    raise ValueError("evaluation_before_decision_ready")
                row = {"symbol": symbol, "status": "PENDING_FUTURE_DATA" if clock is not None and clock < end else "MISSING_FUTURE_DATA"}
            accounting.append({"decision_id": decision_id, "symbol": symbol, "window_start": start.isoformat(),
                               "window_end": end.isoformat(), "status": row["status"],
                               "target_stop_order": row.get("target_stop_order"), "row": row})
    for e in evaluations:
        if e["decision_id"] not in valid and (e["status"] != "NOT_A_VALID_PREDICTION" or e["rows"]):
            raise ValueError("evaluation_for_invalid_prediction")
    rows = [a["row"] for a in accounting if a["status"] == "OBSERVED_PRICE_MOVEMENT"]
    bins = []
    selected = [r for r in rows if r["ai_selected"]]
    if len(selected) >= POLICY["calibration_min_samples"]:
        for n in range(POLICY["confidence_bins"]):
            bucket = [r for r in selected if min(int(r["confidence"] * 5), 4) == n]
            bins.append({"bin": n, "count": len(bucket),
                         "up_frequency_denominator": len(bucket),
                         "up_frequency": sum(r["direction"] == "UP" for r in bucket) / len(bucket) if bucket else None})
    expected_count = len(accounting)
    completed = len(rows)
    no_trade = sum(d["prediction"]["action"] == "NO_TRADE" for d in valid.values())
    proposal_count = sum(len(d["prediction"]["proposals"]) for d in valid.values())
    baseline_count = sum(r["baseline_selected"] for r in rows)
    return {"total_recorded_decisions": len(decisions), "total_valid_buy_predictions": len(valid) - no_trade,
            "total_no_trade_decisions": no_trade, "total_expected_prediction_evaluations": expected_count,
            "total_expected_buy_evaluations": proposal_count, "total_completed_evaluations": completed,
            "total_pending": sum(a["status"] == "PENDING_FUTURE_DATA" for a in accounting),
            "total_missing_insufficient": sum(a["status"] == "MISSING_FUTURE_DATA" for a in accounting),
            "total_invalid_conflicting": sum(a["status"] == "INVALID_FUTURE_DATA" for a in accounting),
            "total_ambiguous": sum(r["target_stop_order"] == "AMBIGUOUS" for r in rows),
            "evaluation_coverage_pct": 100 * completed / expected_count if expected_count else None,
            "unevaluated_count": expected_count - completed,
            "evaluation_accounting": [{k: v for k, v in a.items() if k != "row"} for a in accounting],
            "rate_denominators": {"no_trade_frequency": len(valid), "candidate_coverage": expected_count,
                                  "ai_up_frequency": len(selected), "baseline_up_frequency": baseline_count,
                                  "evaluation_coverage_pct": expected_count},
            "future_observation_provenance": "UNVERIFIED_CALLER_SUPPLIED; hashes are local integrity only",
            "valid_predictions": len(valid), "failures": len(decisions) - len(valid),
            "no_trade_frequency": no_trade / len(valid) if valid else None,
            "candidate_coverage": proposal_count / expected_count if expected_count else None,
            "ai_up_frequency": sum(r["direction"] == "UP" for r in selected) / len(selected) if selected else None,
            "baseline_observed_count": baseline_count,
            "baseline_up_frequency": sum(r["baseline_selected"] and r["direction"] == "UP" for r in rows) / baseline_count if baseline_count else None,
            "confidence_bins": bins, "calibration_status": "DESCRIPTIVE_SCORES_NOT_ESTABLISHED_PROBABILITIES" if bins else "INSUFFICIENT_SAMPLE",
            "execution_pnl": None}
