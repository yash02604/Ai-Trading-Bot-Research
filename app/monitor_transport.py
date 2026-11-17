"""Bounded monitor market acquisition; no trading/order interface or implicit opt-in."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from app import upstox_client
from app.client_attestation import verify_client_attestation
from app.research.deepseek_prospective import AppendOnlyJsonl, PersistentRequestBudget, _experiment_lock
from app.research.ai_led_guarded import _write, _read


class MonitorAcquisitionError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def _sync(path):
    with path.open("ab") as stream:
        stream.flush()
        os.fsync(stream.fileno())


class BoundedMonitorTransport:
    """Explicit finite lifetime budget; every logical request permits one physical attempt."""
    def __init__(self, output_dir, *, maximum_requests, calendar, stocks, now_fn,
                 post_close_drain_seconds=120, enabled=False):
        if type(maximum_requests) is not int or not 1 <= maximum_requests <= 10000:
            raise ValueError("explicit bounded monitor request cap required")
        self.output = Path(output_dir)
        self.maximum = maximum_requests
        self.calendar, self.stocks, self.now_fn = calendar, dict(stocks), now_fn
        self.drain = pd.Timedelta(seconds=post_close_drain_seconds)
        if not pd.Timedelta(0) < self.drain <= pd.Timedelta(seconds=300):
            raise ValueError("invalid acquisition drain")
        self.enabled = enabled
        self.identity = {"mode": "MONITOR_ONLY", "calendar": calendar.identity, "stocks": self.stocks,
                         "maximum_requests": maximum_requests, "max_attempts_per_request": 1,
                         "drain_seconds": post_close_drain_seconds, "interval_minutes": 5}

    def _check(self, deadline):
        now = pd.Timestamp(self.now_fn()).tz_convert("Asia/Kolkata")
        if now >= deadline:
            raise MonitorAcquisitionError("ACQUISITION_DEADLINE_EXCEEDED")
        intervals = self.calendar.intervals(now)
        if not any(start <= now <= end + self.drain for start, end in intervals):
            raise MonitorAcquisitionError("SESSION_INVALID")
        verify_client_attestation()

    def fetch(self, kind, instrument_key, *, deadline, date=None):
        if self.enabled is not True:
            raise MonitorAcquisitionError("ACQUISITION_DISABLED")
        if kind not in {"intraday", "history"} or instrument_key not in self.stocks.values():
            raise MonitorAcquisitionError("ACQUISITION_IDENTITY_INVALID")
        policy = self.output / "monitor_acquisition.json"
        if self.output.exists() and not policy.exists() and any(self.output.iterdir()):
            raise MonitorAcquisitionError("ACQUISITION_IDENTITY_INVALID")
        deadline = pd.Timestamp(deadline).tz_convert("Asia/Kolkata")
        if kind == "history":
            stamp = pd.Timestamp(date, tz="Asia/Kolkata")
            if stamp.date() >= pd.Timestamp(self.now_fn()).tz_convert("Asia/Kolkata").date() or not self.calendar.intervals(stamp):
                raise MonitorAcquisitionError("SESSION_INVALID")
        with _experiment_lock(self.output):
            if not policy.exists() and any(p.name != ".prospective.lock" for p in self.output.iterdir()):
                raise MonitorAcquisitionError("ACQUISITION_IDENTITY_INVALID")
            _write(self.output / "monitor_acquisition.json", self.identity)
            self._check(deadline)
            budget = PersistentRequestBudget(self.output / "request_budget.jsonl", self.maximum)
            identity = {"ordinal": budget.used, "kind": kind, "instrument_key": instrument_key,
                        "date": date, "policy": self.identity}
            request_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            try:
                if not budget.reserve(request_id):
                    raise MonitorAcquisitionError("REQUEST_BUDGET_EXHAUSTED")
            except RuntimeError:
                raise MonitorAcquisitionError("REQUEST_BUDGET_EXHAUSTED") from None
            _sync(budget.ledger.path)
            _write(self.output / "requests" / (request_id + ".json"), identity)

            def before_attempt():
                self._check(deadline)
                if _read(self.output / "monitor_acquisition.json") != self.identity:
                    raise MonitorAcquisitionError("ACQUISITION_IDENTITY_INVALID")

            def before_send():
                attempts = AppendOnlyJsonl(self.output / "physical_attempts.jsonl", "request_id")
                if request_id in attempts._records or len(attempts._records) >= self.maximum:
                    raise MonitorAcquisitionError("REQUEST_BUDGET_EXHAUSTED")
                attempts.append({"request_id": request_id, **identity})
                _sync(attempts.path)

            try:
                with upstox_client.request_window(end=deadline.to_pydatetime(),
                        before_attempt=before_attempt, before_send=before_send, max_attempts=1):
                    response = (upstox_client.get_intraday_candles(instrument_key, interval=5)
                                if kind == "intraday" else
                                upstox_client.get_historical_candles(instrument_key, date, interval=5))
                self._check(deadline)
                _write(self.output / "responses" / (request_id + ".json"), response)
                receipt = {"request_id": request_id, "status": "RECEIVED"}
            except Exception:
                receipt = {"request_id": request_id, "status": "FAILED"}
                ledger = AppendOnlyJsonl(self.output / "receipts.jsonl", "request_id")
                ledger.append(receipt)
                _sync(ledger.path)
                raise
            ledger = AppendOnlyJsonl(self.output / "receipts.jsonl", "request_id")
            ledger.append(receipt)
            _sync(ledger.path)
            return response
