"""Opt-in, read-only Upstox/DeepSeek transports for the guarded PAPER account.

No network call occurs at import or construction. Every send requires explicit
authorization, current preflight, a durable reservation and a single-use send ID.
There is no broker order, balance or arbitrary URL/callback interface.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import hashlib
import math
import os
from pathlib import Path
import re
import threading

import pandas as pd

from app.config import get_settings
from app import upstox_client
from app.execution_observations import parse_upstox_full_quote
from app.market_analysis import candles_to_dataframe
from app.paper_session import completed_frame
from app.candle_integrity import validate_candles
from app.research import ai_led_guarded as guarded
from app.research import ai_led_offline as core
from app.research.deepseek_prospective import AppendOnlyJsonl, PersistentRequestBudget, PROSPECTIVE_SCHEMA
from app.v2.provider import DeepSeekProvider, retained_provider_diagnostic
from app.research.prospective_preflight import SETTLEMENT_WINDOW_POLICY, settlement_acquisition_window


def _utc_now():
    return datetime.now(timezone.utc)


def _validated_policy(policy):
    expected = {"schema_version", "include_quotes", "timeout_seconds", "provider_retries",
                "upstox_retries", "max_completion_tokens", "max_response_bytes", "total_deadline_seconds",
                "max_retry_delay_seconds", "settlement_window_policy"}
    if (type(policy) is not dict or set(policy) != expected
            or policy["schema_version"] != "reviewed-readonly-paper-transports-v1"
            or type(policy["include_quotes"]) is not bool
            or type(policy["timeout_seconds"]) not in (int, float)
            or not math.isfinite(policy["timeout_seconds"]) or not 0 < policy["timeout_seconds"] <= 20
            or type(policy["provider_retries"]) is not int or policy["provider_retries"] != 0
            or type(policy["upstox_retries"]) is not int or policy["upstox_retries"] not in (0, 2)
            or type(policy["max_completion_tokens"]) is not int or policy["max_completion_tokens"] != 512
            or type(policy["max_response_bytes"]) is not int or policy["max_response_bytes"] != 16384):
        raise guarded.GuardedBatchError("invalid_transport_policy")
    if (type(policy["total_deadline_seconds"]) not in (int, float)
            or not math.isfinite(policy["total_deadline_seconds"]) or not 0 < policy["total_deadline_seconds"] <= 60
            or type(policy["max_retry_delay_seconds"]) not in (int, float)
            or not math.isfinite(policy["max_retry_delay_seconds"])
            or not 0 <= policy["max_retry_delay_seconds"] <= min(5, policy["total_deadline_seconds"])
            or policy["settlement_window_policy"] != SETTLEMENT_WINDOW_POLICY):
        raise guarded.GuardedBatchError("invalid_deadline_or_settlement_policy")


def _response_hash(value):
    try:
        data = value.encode() if type(value) is str else core._encoded(value)
        return hashlib.sha256(data).hexdigest()
    except Exception:
        return None


def _error_fields(exc):
    # Fixed labels/numeric HTTP status only, including for malformed SDK metadata.
    category, status = "transport_failure", None
    try:
        proposed = vars(exc).get("status_code", vars(exc).get("provider_http_status"))
        if type(proposed) is int and 100 <= proposed <= 599:
            status = proposed
        if isinstance(exc, upstox_client.UpstoxRequestError) and vars(exc).get("category") in {
                "authentication", "rate_limit", "http_error", "network_error", "json_decode"}:
            category = vars(exc)["category"]
        elif status in (401, 403): category = "authentication"
        elif status == 400 and vars(exc).get("provider_stage") == "request_submission_or_transport":
            category = "invalid_request"
        elif status == 429: category = "rate_limit"
        elif isinstance(exc, TimeoutError): category = "timeout"
    except Exception:
        pass
    result = {"category": category, "http_status": status, "message": "Request failed; details redacted"}
    diagnostic = retained_provider_diagnostic(exc)
    if diagnostic is not None:
        result["provider_diagnostic"] = diagnostic
    return result


@contextmanager
def _binding_scope(client, provider):
    # Called only inside the shared acquisition helper's experiment lock.
    if type(client) is not _ReviewedRun or provider is not client:
        raise guarded.GuardedBatchError("reviewed_binding_required")
    previous = client._lock_owner
    client._lock_owner = threading.get_ident()
    try:
        yield
    finally:
        client._lock_owner = previous


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        if self._lock_owner == threading.get_ident():
            return method(self, *args, **kwargs)
        with core._experiment_lock(self.output), _binding_scope(self, self):
            return method(self, *args, **kwargs)
    return call


def _validated_quote_row(row, symbol, now):
    fields = {"instrument_key", "provider_timestamp", "received_at", "ask_price", "ask_quantity",
              "bid_price", "bid_quantity", "spread_bps", "source_quote_timestamp", "timestamp_kind"}
    if (type(row) is not dict or set(row) != fields or row["instrument_key"] != core.STOCKS[symbol]
            or row["source_quote_timestamp"] is not None
            or row["timestamp_kind"] != "response_generated_not_depth_timestamp"
            or any(type(row[k]) not in (int, float) or not math.isfinite(row[k]) or row[k] <= 0
                   for k in ("ask_price", "bid_price"))
            or any(type(row[k]) is not int or row[k] <= 0 for k in ("ask_quantity", "bid_quantity"))
            or type(row["spread_bps"]) not in (int, float) or not math.isfinite(row["spread_bps"])
            or row["spread_bps"] < 0 or row["bid_price"] > row["ask_price"]):
        raise guarded.GuardedBatchError("invalid_cached_quote_schema")
    source = core._aware_timestamp(row["provider_timestamp"], "quote timestamp")
    received = core._aware_timestamp(row["received_at"], "quote receipt")
    if not source <= received <= now or not 0 <= (now - source).total_seconds() <= 5:
        raise guarded.GuardedBatchError("quote_no_longer_fresh")
    return row


class _ReviewedRun:
    """Private binding; even direct fetch/complete calls must prove all send guards."""
    def __init__(self, *, output_dir, instrument_priority, capital, provider_cap, market_data_cap,
                 enable_prospective_paper=False, allow_market_data_requests=False,
                 allow_provider_requests=False, include_quotes=False, min_turnover=100000.0,
                 purpose="entry", enable_settlement_only=False, allow_settlement_market_data_requests=False,
                 enable_signal_only=False, enable_signal_future=False):
        self.purpose = purpose
        self.flags = (enable_prospective_paper, allow_market_data_requests, allow_provider_requests)
        if purpose in {"signal", "signal_recovery", "signal_future"}:
            if enable_prospective_paper or enable_settlement_only or allow_settlement_market_data_requests:
                raise guarded.GuardedBatchError("signal_mode_cannot_authorize_paper_execution")
            if purpose == "signal_recovery" and (allow_market_data_requests or allow_provider_requests):
                raise guarded.GuardedBatchError("signal_recovery_must_be_local_only")
            self.flags = ((enable_signal_only,) if purpose == "signal_recovery" else
                          (enable_signal_only, allow_market_data_requests, allow_provider_requests))
            if purpose == "signal_future":
                if allow_provider_requests:
                    raise guarded.GuardedBatchError("future_candles_only_no_provider")
                self.flags = (enable_signal_only, enable_signal_future, allow_market_data_requests)
        elif purpose == "settlement":
            if any(self.flags):
                raise guarded.GuardedBatchError("settlement_must_not_authorize_new_entries_or_provider")
            self.flags = (enable_settlement_only, allow_settlement_market_data_requests)
        elif purpose == "recovery":
            if allow_market_data_requests or allow_provider_requests:
                raise guarded.GuardedBatchError("local_recovery_must_not_authorize_network")
            self.flags = (enable_prospective_paper,)
        self._settings()
        settings = get_settings()
        self.policy = {"schema_version": "reviewed-readonly-paper-transports-v1",
                       "include_quotes": include_quotes, "timeout_seconds": settings.request_timeout_seconds,
                       "provider_retries": 0, "upstox_retries": 0 if purpose.startswith("signal") else 2,
                       "max_completion_tokens": 512, "max_response_bytes": 16384,
                       "total_deadline_seconds": getattr(settings, "upstox_total_deadline_seconds", 20.0),
                       "max_retry_delay_seconds": getattr(settings, "upstox_max_retry_delay_seconds", 1.0),
                       "settlement_window_policy": SETTLEMENT_WINDOW_POLICY}
        _validated_policy(self.policy)
        self.output = Path(output_dir)
        self._lock_owner = None
        self.options = dict(priority=instrument_priority, capital=capital, model=settings.deepseek_model,
                            provider_cap=provider_cap, market_data_cap=market_data_cap,
                            min_turnover=min_turnover, network_policy=self.policy)
        self.candidate = core._aware_timestamp(_utc_now(), "request_clock").floor("5min") - pd.Timedelta(minutes=5)

    def _settings(self):
        if any(flag is not True for flag in self.flags):
            raise guarded.GuardedBatchError("network_disabled_requires_three_explicit_opt_ins")
        settings = get_settings()
        if (settings.paper_trading is not True or settings.deepseek_model != "deepseek-flash"
                or (self.purpose not in {"recovery", "signal_recovery"} and (type(settings.upstox_access_token) is not str or not settings.upstox_access_token.strip()))
                or (self.purpose in {"entry", "signal"} and (type(settings.deepseek_api_key) is not str or not settings.deepseek_api_key.strip()))
                or type(settings.request_timeout_seconds) not in (int, float)
                or not math.isfinite(settings.request_timeout_seconds)
                or not 0 < settings.request_timeout_seconds <= 20):
            raise guarded.GuardedBatchError("paper_transport_settings_invalid")
        return settings

    def _preflight(self):
        settings = self._settings()
        if (settings.request_timeout_seconds != self.policy["timeout_seconds"]
                or getattr(settings, "upstox_total_deadline_seconds", 20.0) != self.policy["total_deadline_seconds"]
                or getattr(settings, "upstox_max_retry_delay_seconds", 1.0) != self.policy["max_retry_delay_seconds"]
                or settings.deepseek_model != self.options["model"]):
            raise guarded.GuardedBatchError("transport_settings_changed")
        if self.purpose == "signal":
            from app.research.ai_signal_only import preflight
            return preflight(self, _utc_now())
        if self.purpose == "signal_future":
            from app.research.signal_observations import future_preflight
            return future_preflight(self, _utc_now())
        if self.purpose == "settlement":
            from app.research.ai_led_recovery import verified_open_position
            saved = guarded._read(self.output / guarded.MANIFEST)
            state = guarded._account(self.output, saved)
            position = verified_open_position(state)
            config, calendar = guarded._preflight(self.output, now=_utc_now(), purpose="settlement",
                                                   position_entry=position["expected_entry"], **self.options)
            _, end, _ = settlement_acquisition_window(calendar, position["expected_entry"])
            if not hasattr(self, "position_id"):
                self.candidate = min(core._aware_timestamp(_utc_now(), "now").floor("5min") - pd.Timedelta(minutes=5),
                                     end - pd.Timedelta(minutes=5))
                self.position_id = position["decision_id"]
            elif self.position_id != position["decision_id"]:
                raise guarded.GuardedBatchError("settlement_position_changed")
            return config, calendar
        if self.purpose != "entry":
            raise guarded.GuardedBatchError("recovery_cannot_authorize_network")
        config, calendar = guarded._preflight(self.output, now=_utc_now(), **self.options)
        if not any(start <= self.candidate and self.candidate + pd.Timedelta(minutes=5) <= end
                   and self.candidate - start >= pd.Timedelta(minutes=34*5)
                   for start, end in calendar.intervals(self.candidate)):
            raise guarded.GuardedBatchError("insufficient_session_warmup_before_request")
        return config, calendar

    def _market_id(self, symbol, kind="UPSTOX_CAPTURE"):
        if self.purpose == "signal_future":
            if kind != "UPSTOX_CAPTURE" or symbol != self.future_job["symbol"]:
                raise guarded.GuardedBatchError("future_instrument_or_purpose_changed")
            return self.future_job["request_id"]
        if self.purpose == "settlement":
            return core._digest({"position_id": self.position_id, "candidate_timestamp": self.candidate.isoformat(),
                                 "symbol": symbol, "kind": "UPSTOX_SETTLEMENT"})
        return core._digest({"candidate_timestamp": self.candidate.isoformat(), "symbol": symbol,
                             "universe": self.options["priority"], "kind": kind})

    def _mark(self, request_id, kind, symbol=None):
        self._preflight()
        stem = "request_budget" if kind == "deepseek" else "market_data_request_budget"
        path = self.output / (stem + ".jsonl")
        ledger = AppendOnlyJsonl(path, "request_id")
        if request_id not in ledger._records:
            raise guarded.GuardedBatchError("transport_requires_persistent_reservation")
        sent = AppendOnlyJsonl(self.output / "transport_sent.jsonl", "request_id")
        if request_id in sent._records:
            raise guarded.GuardedBatchError("transport_request_already_sent")
        sent.append({"request_id": request_id, "kind": kind, "symbol": symbol,
                     "sent_at_utc": _utc_now().isoformat(), "trade_placement_enabled": False})
        # Make both the reservation and send checkpoint durable before network I/O.
        for durable in (path, self.output / "transport_sent.jsonl"):
            with durable.open("ab") as stream:
                stream.flush()
                os.fsync(stream.fileno())

    def _receipt(self, request_id, kind, response=None, error=None):
        record = {"request_id": request_id, "kind": kind, "received_at_utc": _utc_now().isoformat(),
                  "status": "FAILED" if error is not None else "RECEIVED",
                  "response_sha256": None if error is not None else _response_hash(response)}
        if error is not None:
            record.update(_error_fields(error))
        AppendOnlyJsonl(self.output / "transport_receipts.jsonl", "request_id").append(record)

    @_serialized
    def _send_market(self, symbol, *, quote=False):
        config, calendar = self._preflight()
        if self.purpose in {"settlement", "signal_future"} and quote:
            raise guarded.GuardedBatchError("settlement_candles_only")
        if self.purpose == "settlement" and symbol != guarded._account(self.output, config)["position"]["symbol"]:
            raise guarded.GuardedBatchError("settlement_instrument_mismatch")
        if type(symbol) is not str or symbol not in self.options["priority"]:
            raise guarded.GuardedBatchError("instrument_not_configured")
        request_id = self._market_id(symbol, "UPSTOX_QUOTE" if quote else "UPSTOX_CAPTURE")
        kind = "upstox_quote" if quote else "upstox_candles"
        self._mark(request_id, kind, symbol)
        try:
            key = core.STOCKS[symbol]
            if self.purpose == "signal_future":
                end = pd.Timestamp(self.future_job["capture_before"])
            elif self.purpose == "settlement":
                state = guarded._account(self.output, config)
                _, _, end = settlement_acquisition_window(calendar, state["position"]["expected_entry"])
            else:
                end = calendar.candle_end(self.candidate) - pd.Timedelta(minutes=5)
            send_options = {}
            if self.purpose in {"signal", "signal_future"}:
                from app.research.signal_request_budget import record_physical_attempt
                send_options = dict(max_attempts=1, before_send=lambda: record_physical_attempt(
                    self, request_id, symbol, "INITIAL_QUOTE" if quote else
                    "FUTURE_OBSERVATION" if self.purpose == "signal_future" else "INITIAL_SNAPSHOT"))
            with upstox_client.request_window(end=end.to_pydatetime(), before_attempt=self._preflight, **send_options):
                response = upstox_client.get_quote(key) if quote else upstox_client.get_intraday_candles(key, interval=5)
        except Exception as exc:
            self._receipt(request_id, kind, error=exc)
            raise guarded.GuardedBatchError("upstox_request_failed") from None
        self._receipt(request_id, kind, response)
        if self.purpose == "signal_future":
            from app.research.signal_observations import record_response
            record_response(self, request_id, response)
        if self.purpose == "signal" and quote:
            from app.research.ai_signal_only import record_quote
            record_quote(self, symbol, request_id, response)
        return response

    def _quote(self, symbol, response):
        if type(response) is not dict or response.get("status") != "success":
            raise guarded.GuardedBatchError("invalid_quote_response")
        data = response.get("data")
        if type(data) is not dict or len(data) != 1:
            raise guarded.GuardedBatchError("invalid_quote_identity")
        item = next(iter(data.values()))
        if (type(item) is not dict or item.get("instrument_token", item.get("instrument_key")) != core.STOCKS[symbol]
                or pd.Timestamp(item.get("timestamp")).tzinfo is None):
            raise guarded.GuardedBatchError("invalid_quote_identity_or_timestamp")
        parsed = parse_upstox_full_quote(response, _utc_now())
        if parsed["quote_valid"] is not True or parsed["spread_available"] is not True:
            raise guarded.GuardedBatchError("invalid_or_stale_quote")
        return {"instrument_key": core.STOCKS[symbol], "provider_timestamp": parsed["provider_timestamp"].isoformat(),
                "received_at": parsed["observed_at"].isoformat(), "ask_price": parsed["ask_price"],
                "ask_quantity": parsed["ask_quantity"], "bid_price": parsed["bid_price"],
                "bid_quantity": parsed["bid_quantity"], "spread_bps": parsed["spread_bps"],
                "source_quote_timestamp": None, "timestamp_kind": "response_generated_not_depth_timestamp"}

    @_serialized
    def fetch(self, symbol):
        response = self._send_market(symbol)
        received = core._aware_timestamp(_utc_now(), "received_at")
        try:
            if type(response) is not dict or response.get("status") != "success":
                raise ValueError()
            data = response["data"]
            if type(data) is not dict or any(data[k] != core.STOCKS[symbol]
                                            for k in ("instrument_key", "instrument_token") if k in data):
                raise ValueError()
            rows = data["candles"]
            raw = candles_to_dataframe(rows)
            if validate_candles(raw).errors or any(raw.timestamp > received):
                raise ValueError()
            frame = completed_frame(rows, received)
            frame = frame.loc[frame.timestamp <= self.candidate]
            body = {"schema_version": PROSPECTIVE_SCHEMA, "symbol": symbol,
                    "instrument_key": core.STOCKS[symbol], "source": "upstox-v3-intraday-readonly",
                    "observed_at": received.isoformat(),
                    "candles": [{"timestamp": row.timestamp.isoformat(),
                                 **{k: float(getattr(row, k)) for k in ("open", "high", "low", "close", "volume")}}
                                for row in frame.itertuples(index=False)]}
            encoded = core._encoded(body)
            path = self.output / "received" / (hashlib.sha256(encoded).hexdigest() + ".json")
            guarded._write_bytes_once(path, encoded)
            if self.policy["include_quotes"] and self.purpose in {"entry", "signal"}:
                request_id = self._market_id(symbol, "UPSTOX_QUOTE")
                self._preflight()
                if self.purpose == "signal":
                    from app.research.signal_request_budget import reserve
                    reserved = reserve(self, request_id, symbol, "INITIAL_QUOTE")
                else:
                    budget = PersistentRequestBudget(self.output / "market_data_request_budget.jsonl",
                                                     self.options["market_data_cap"])
                    reserved = budget.reserve(request_id)
                if not reserved:
                    raise guarded.GuardedBatchError("quote_request_already_reserved")
                quote = self._quote(symbol, self._send_market(symbol, quote=True))
                guarded._write(self.output / "quotes" / (request_id + ".json"), quote)
            return path
        except Exception:
            raise guarded.GuardedBatchError("market_data_validation_failed") from None

    def market_context(self):
        self._preflight()
        quotes = {}
        for symbol in self.options["priority"]:
            row = guarded._read(self.output / "quotes" / (self._market_id(symbol, "UPSTOX_QUOTE") + ".json"))
            now = core._aware_timestamp(_utc_now(), "now")
            _validated_quote_row(row, symbol, now)
            quotes[symbol] = row
        return quotes

    def _authorize_entry(self, payload):
        if self.purpose != "entry":
            raise guarded.GuardedBatchError("settlement_or_recovery_cannot_request_provider")
        config, calendar = self._preflight()
        request_id = core._digest({"model": self.options["model"], "payload": payload})
        saved = guarded._read(self.output / "requests" / (request_id + ".json"))
        if saved["payload"] != payload or payload["system_prompt"] != guarded.PROMPT.replace(guarded.SCHEMA, guarded.REVIEWED_SCHEMA):
            raise guarded.GuardedBatchError("provider_request_identity")
        import json
        request = json.loads(payload["user_payload"])
        priority = self.options["priority"]
        fields = {"schema_version", "candidate_timestamp", "decision_time", "instrument_priority", "market"}
        if self.policy["include_quotes"]:
            fields.add("quotes")
        if (type(request) is not dict or set(request) != fields or request["schema_version"] != core.SCHEMA
                or request["instrument_priority"] != priority or set(request["market"]) != set(priority)):
            raise guarded.GuardedBatchError("provider_universe_mismatch")
        paths = []
        for symbol in priority:
            digest = request["market"][symbol]["source_sha256"]
            if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise guarded.GuardedBatchError("provider_input_hash")
            paths.append(self.output / "inputs" / (digest + ".json"))
        guarded._verify_frozen(paths)
        now = core._aware_timestamp(_utc_now(), "now")
        statuses = {s: {"status": "READY", "snapshot": p} for s, p in zip(priority, paths)}
        frames, snapshots = guarded._validated_batch(statuses, priority, now, self.options["min_turnover"], calendar)
        decision_time = core._aware_timestamp(request["decision_time"], "decision_time")
        candidate = frames[priority[0]].iloc[-1].timestamp
        expected_entry = decision_time.floor("5min") + pd.Timedelta(minutes=5)
        if (request["market"] != snapshots or pd.Timestamp(request["candidate_timestamp"]) != candidate
                or not decision_time <= now < expected_entry):
            raise guarded.GuardedBatchError("provider_data_changed_or_entry_time_elapsed")
        if self.policy["include_quotes"] and request.get("quotes") != self.market_context():
            raise guarded.GuardedBatchError("provider_quote_identity")
        preview = copy.deepcopy(guarded._account(self.output, config))
        core._advance(preview, frames, decision_time, calendar)
        allowed, _ = core.check_trade_allowed(core.RiskConfig(capital=self.options["capital"]),
                                              core._risk_state(preview, decision_time.date()))
        if not allowed or preview["cash"] <= 0:
            raise guarded.GuardedBatchError("provider_account_blocked")
        return expected_entry

    @_serialized
    def complete(self, payload):
        if self.purpose == "signal":
            from app.research.ai_signal_only import authorize_request
            expected_entry = authorize_request(self, payload)
        else:
            expected_entry = self._authorize_entry(payload)
        request_id = core._digest({"model": self.options["model"], "payload": payload})
        self._mark(request_id, "deepseek")
        try:
            settings = self._settings()
            result = DeepSeekProvider(api_key=settings.deepseek_api_key, model_id=settings.deepseek_model,
                                      timeout_seconds=settings.request_timeout_seconds).complete(payload)
            self._receipt(request_id, "deepseek", result.content)
            self._preflight()
            if core._aware_timestamp(_utc_now(), "response_time") >= expected_entry:
                raise guarded.GuardedBatchError("response_after_expected_entry")
            return result
        except Exception as exc:
            # A successfully received response already has a receipt; do not rewrite it.
            receipts = AppendOnlyJsonl(self.output / "transport_receipts.jsonl", "request_id")
            if request_id not in receipts._records:
                self._receipt(request_id, "deepseek", error=exc)
            raise guarded.GuardedBatchError("deepseek_request_failed_or_expired") from None


def run_reviewed_paper(*, output_dir, instrument_priority, capital, provider_cap, market_data_cap,
                       enable_prospective_paper=False, allow_market_data_requests=False,
                       allow_provider_requests=False, include_quotes=False, min_turnover=100000.0):
    """No caller-supplied provider, URL, broker, market files or simulation clock."""
    binding = _ReviewedRun(output_dir=output_dir, instrument_priority=instrument_priority, capital=capital,
                           provider_cap=provider_cap, market_data_cap=market_data_cap,
                           enable_prospective_paper=enable_prospective_paper,
                           allow_market_data_requests=allow_market_data_requests,
                           allow_provider_requests=allow_provider_requests, include_quotes=include_quotes,
                           min_turnover=min_turnover)
    if (binding.output / guarded.MANIFEST).exists():
        with core._experiment_lock(binding.output):
            config, calendar = _checked_recovery_config(binding)
            from app.research.ai_led_recovery import recover_pending, resolve_expired_entry
            recovered = recover_pending(binding.output, config, _utc_now())
            try:
                replay_terminal = not calendar.entry_allowed(_utc_now())
            except ValueError:
                replay_terminal = True
            resolved = resolve_expired_entry(binding.output, config, calendar, _utc_now(), replay_terminal=replay_terminal)
            if resolved is not None:
                return resolved
            if recovered is not None:
                return recovered
    return guarded._capture_batch(binding, candidate_timestamp=binding.candidate, output_dir=output_dir,
        instrument_priority=instrument_priority, capital=capital, now=_utc_now(), provider=binding,
        provider_cap=provider_cap, market_data_cap=market_data_cap, model=binding.options["model"],
        min_turnover=min_turnover, network_policy=binding.policy)


def _checked_recovery_config(binding):
    """Current identity checks without granting a current network/entry window."""
    from app.client_attestation import verify_client_attestation
    from app.research.prospective_preflight import CALENDAR_PATH, _budget_config
    from app.research.offline_session_calendar import OfflineSessionCalendar
    binding._settings()
    verify_client_attestation()
    calendar = OfflineSessionCalendar(CALENDAR_PATH)
    saved = guarded._read(binding.output / guarded.MANIFEST)
    args = binding.options
    expected = guarded._configuration(args["priority"], args["capital"], args["model"], args["provider_cap"],
        args["market_data_cap"], args["min_turnover"], calendar, binding.policy)
    if saved != expected:
        raise guarded.GuardedBatchError("recovery_experiment_identity_changed")
    guarded._account(binding.output, saved)
    _budget_config(binding.output, "market_data", saved["market_data_cap"])
    _budget_config(binding.output, "provider", saved["provider_cap"])
    return saved, calendar


def recover_reviewed_paper(**options):
    """Explicit local-only recovery. Never acquires a snapshot or invokes DeepSeek."""
    binding = _ReviewedRun(purpose="recovery", **options)
    with core._experiment_lock(binding.output):
        config, calendar = _checked_recovery_config(binding)
        from app.research.ai_led_recovery import recover_pending, resolve_expired_entry
        recovered = recover_pending(binding.output, config, _utc_now(), strict=True)
        resolved = resolve_expired_entry(binding.output, config, calendar, _utc_now(), replay_terminal=True)
        return resolved if resolved is not None else recovered


def run_reviewed_settlement(*, output_dir, instrument_priority, capital, provider_cap, market_data_cap,
                            enable_settlement_only=False, allow_settlement_market_data_requests=False,
                            include_quotes=False, min_turnover=100000.0):
    """Acquire candles only for an existing accepted position. No AI/new decision path."""
    binding = _ReviewedRun(output_dir=output_dir, instrument_priority=instrument_priority, capital=capital,
        provider_cap=provider_cap, market_data_cap=market_data_cap, include_quotes=include_quotes,
        min_turnover=min_turnover, purpose="settlement", enable_settlement_only=enable_settlement_only,
        allow_settlement_market_data_requests=allow_settlement_market_data_requests)
    from app.research.ai_led_recovery import finish_settlement, verified_open_position, resolve_expired_entry
    with core._experiment_lock(binding.output), _binding_scope(binding, binding):
        config, calendar = _checked_recovery_config(binding)
        recovered = finish_settlement(binding.output, config, calendar)
        if recovered is not None:
            return recovered
        resolved = resolve_expired_entry(binding.output, config, calendar, _utc_now(), replay_terminal=True)
        if resolved is not None:
            return resolved
        config, calendar = binding._preflight()
        state = guarded._account(binding.output, config)
        position = verified_open_position(state)
        if binding.candidate <= core._aware_timestamp(position["last_bar"], "last settled observation"):
            raise guarded.GuardedBatchError("duplicate_settlement_observation")
        symbol = position["symbol"]
        request_id = binding._market_id(symbol)
        cache_path = binding.output / "settlement_observations" / (request_id + ".json")
        if cache_path.exists():
            observation = guarded._read(cache_path)
            path = binding.output / "inputs" / (observation["input_sha256"] + ".json")
        else:
            budget = PersistentRequestBudget(binding.output / "market_data_request_budget.jsonl", market_data_cap)
            if not budget.reserve(request_id):
                raise guarded.GuardedBatchError("settlement_reserved_without_saved_result")
            path = binding.fetch(symbol)
        frames, snapshots = guarded._validated_batch({symbol: {"status": "READY", "snapshot": path}},
            [symbol], core._aware_timestamp(_utc_now(), "settlement receipt"), min_turnover, calendar)
        if frames[symbol].iloc[-1].timestamp != binding.candidate:
            raise guarded.GuardedBatchError("latest_completed_settlement_candle_missing")
        paths = guarded._freeze(binding.output, snapshots)
        observation = {"request_id": request_id, "position_id": position["decision_id"],
                       "input_sha256": paths[0].stem, "manifest_sha256": core._digest(config),
                       "observed_at": snapshots[symbol]["observed_at"]}
        guarded._write(cache_path, observation)
        after = copy.deepcopy(state)
        observed = core._aware_timestamp(_utc_now(), "settlement clock")
        core._advance(after, frames, observed, calendar)
        after["last_time"] = observed.isoformat()
        prepared = {**observation, "entry_timestamp": position["expected_entry"], "symbol": symbol,
                    "recorded_at": observed.isoformat(), "before_sha256": core._digest(state),
                    "after_sha256": core._digest(after)}
        guarded._write(binding.output / "settlement_prepared" / (request_id + ".json"), prepared)
        return finish_settlement(binding.output, config, calendar)
