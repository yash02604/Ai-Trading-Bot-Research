"""Explicit observation-only transports. No financial state or execution capability."""
import copy
import json
import math
import os
import time
from pathlib import Path

import pandas as pd

from app import upstox_client
from app.config import get_settings
from app.execution_observations import parse_upstox_full_quote
from app.market_analysis import candles_to_dataframe
from app.candle_integrity import validate_candles
from app.research import ai_led_guarded as guarded, ai_led_offline as core
from app.research.deepseek_prospective import AppendOnlyJsonl, PersistentRequestBudget, _experiment_lock
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH
from app.research.v2_transport_contract import (CODES, TransportError, stamp, digest, readiness,
    configuration_identity, validate_quote)
from app.v2.provider import DeepSeekProvider
from strategy_backtest import STOCKS


SYSTEM_PROMPT = guarded.PROMPT.replace(guarded.SCHEMA, guarded.REVIEWED_SCHEMA) + " Return JSON."


def _sync(path):
    with Path(path).open("ab") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class LiveTransport:
    def __init__(self, root, config, clock):
        self.root, self.config, self.clock = Path(root), copy.deepcopy(config), clock

    def _check(self, kind, slot, purpose):
        if purpose not in {"entry", "monitoring", "monitor", "settlement"}:
            raise TransportError("CONFIG_INVALID")
        now = stamp(self.clock())
        policy = self.config.get("transport", {})
        if policy.get("provider_enabled" if kind == "provider" else "market_data_enabled") is not True:
            raise TransportError("PROVIDER_DISABLED" if kind == "provider" else "NETWORK_DISABLED")
        result = readiness(self.config, now, purpose=purpose, expected_start=slot)
        if result["status"] != "READY":
            raise TransportError(result.get("reason_codes", ["CONFIG_INVALID"])[0])
        if now >= slot + pd.Timedelta(minutes=10):
            raise TransportError("ENTRY_WINDOW_CLOSED" if purpose == "entry" else "RESPONSE_STALE")
        settings = get_settings()
        token = settings.deepseek_api_key if kind == "provider" else settings.upstox_access_token
        if type(token) is not str or not token.strip():
            raise TransportError("CREDENTIALS_INVALID")
        for field, fallback in (("request_timeout_seconds", 10), ("upstox_total_deadline_seconds", 20),
                                ("upstox_max_retry_delay_seconds", 1)):
            policy_field = field.removeprefix("upstox_")
            if getattr(settings, field, fallback) != policy[policy_field]:
                raise TransportError("CONFIG_INVALID")
        return now

    def _identity(self, kind, symbol, slot, purpose):
        return {"configuration_identity": configuration_identity(self.config), "kind": kind,
                "symbol": symbol, "slot": slot.isoformat(), "purpose": purpose}

    def _verify_records(self):
        """Reconcile surviving evidence before accepting cache or spending a cap.

        A reservation-only crash is resumable. A send without a durable outcome
        is uncertain, and lost counters cannot be recreated from a zero default.
        This is local consistency checking, not a trusted rollback witness.
        """
        try:
            intents={p.stem:guarded._read(p) for p in (self.root/'intents').glob('*.json')}
            for key,row in intents.items():
                if set(row)!={'configuration_identity','kind','symbol','slot','purpose','payload_hash'}:
                    raise ValueError()
                identity={k:v for k,v in row.items() if k!='payload_hash'}
                if digest(identity)!=key or row['configuration_identity']!=configuration_identity(self.config): raise ValueError()
            budgets={}
            for stem in ('market_data','provider'):
                path=self.root/(stem+'_budget.jsonl')
                rows=AppendOnlyJsonl(path,'request_id').records() if path.exists() else []
                budgets[stem]={r['request_id'] for r in rows}
                if len(rows)>self.config[stem+'_cap']: raise ValueError()
                policy=self.root/(stem+'_budget_config.json')
                if rows and not policy.exists(): raise ValueError()
                if policy.exists() and json.loads(policy.read_bytes())!={'schema_version':'deepseek-request-budget-v1','max_total_requests':self.config[stem+'_cap']}:
                    raise ValueError()
                for row in rows:
                    stamp(row['reserved_at_utc'])
                    intent=intents[row['request_id']]
                    if (intent['kind']=='provider')!=(stem=='provider'): raise ValueError()
            def journal(name,key):
                path=self.root/name
                return AppendOnlyJsonl(path,key).records() if path.exists() else []
            sent={r['request_id']:r for r in journal('sent.jsonl','request_id')}
            failures={r['request_id']:r for r in journal('failures.jsonl','request_id')}
            physical=journal('physical_attempts.jsonl','attempt_id')
            attempts={}
            for row in physical:
                key=row['request_id'];sent[key];stamp(row['attempted_at'])
                index=row['retry_number']
                if type(index) is not int or index!=len(attempts.setdefault(key,[])) or row['attempt_id']!=key+':'+str(index+1): raise ValueError()
                attempts[key].append(row)
                if len(attempts[key])>(1 if sent[key]['kind']=='provider' else 3): raise ValueError()
            for key,row in sent.items():
                intent=intents[key];stamp(row['requested_at'])
                if row['kind']!=intent['kind'] or key not in budgets['provider' if row['kind']=='provider' else 'market_data']:
                    raise ValueError()
            results={p.stem:guarded._read(p) for p in (self.root/'results').glob('*.json')}
            for key,row in results.items():
                if (row['request_id']!=key or row['kind']!=sent[key]['kind'] or row['symbol']!=intents[key]['symbol']
                        or key not in attempts or key in failures): raise ValueError()
            for key,row in failures.items():
                sent[key];stamp(row['failed_at'])
                if row['code'] not in CODES: raise ValueError()
            if set(sent)-set(results)-set(failures): raise TransportError('REQUEST_INCOMPLETE')
        except TransportError: raise
        except Exception: raise TransportError('REQUEST_CONFLICT') from None

    def _request(self, kind, symbol, slot, purpose, call, normalize, payload=None):
        slot = stamp(slot)
        self._check(kind, slot, purpose)  # No directories/reservations before authorization.
        if symbol is not None and (type(symbol) is not str or symbol not in self.config["instrument_priority"]):
            raise TransportError("SOURCE_IDENTITY_MISMATCH")
        identity = self._identity(kind, symbol, slot, purpose)
        request_id = digest(identity)
        intent = {**identity, "payload_hash": None if payload is None else digest(payload)}
        with _experiment_lock(self.root):
            self._check(kind, slot, purpose)
            self._verify_records()
            intent_path = self.root / "intents" / (request_id + ".json")
            if intent_path.exists() and guarded._read(intent_path) != intent:
                raise TransportError("REQUEST_CONFLICT")
            guarded._write(intent_path, intent)
            result_path = self.root / "results" / (request_id + ".json")
            if result_path.exists():
                cached = guarded._read(result_path)
                if cached.get("request_id") != request_id:
                    raise TransportError("REQUEST_CONFLICT")
                return cached
            failures = AppendOnlyJsonl(self.root / "failures.jsonl", "request_id")
            if request_id in failures._records:
                code = json.loads(failures._records[request_id])["code"]
                raise TransportError(code if type(code) is str and code in CODES else "TRANSPORT_FAILURE")
            sent = AppendOnlyJsonl(self.root / "sent.jsonl", "request_id")
            if request_id in sent._records:
                raise TransportError("REQUEST_INCOMPLETE")
            stem = "provider" if kind == "provider" else "market_data"
            budget = PersistentRequestBudget(self.root / (stem + "_budget.jsonl"), self.config[stem + "_cap"])
            try:
                budget.reserve(request_id)  # Reservation-only interruption may safely resume.
            except RuntimeError:
                raise TransportError("BUDGET_EXHAUSTED") from None
            _sync(budget.ledger.path)
            _sync(budget.config_path)
            requested = self._check(kind, slot, purpose)
            deadline = min(slot + pd.Timedelta(minutes=10), requested + pd.Timedelta(
                seconds=self.config['transport']['total_deadline_seconds']))
            monotonic_deadline=time.monotonic()+(deadline-requested).total_seconds()
            sent.append({"request_id": request_id, "requested_at": requested.isoformat(), "kind": kind})
            _sync(sent.path)
            attempt_count = [0]

            def before_send():
                self._check(kind, slot, purpose)
                attempt_count[0] += 1
                if attempt_count[0] > (1 if kind == "provider" else 3):
                    raise TransportError("BUDGET_EXHAUSTED")
                attempts = AppendOnlyJsonl(self.root / "physical_attempts.jsonl", "attempt_id")
                attempts.append({"attempt_id": request_id + ":" + str(attempt_count[0]),
                    "request_id": request_id, "retry_number": attempt_count[0] - 1,
                    "attempted_at": stamp(self.clock()).isoformat()})
                _sync(attempts.path)
                at=self._check(kind,slot,purpose)
                if at>=deadline or time.monotonic()>=monotonic_deadline:
                    raise TransportError('RESPONSE_STALE')

            try:
                if kind == "provider":
                    response = call(before_send)
                else:
                    with upstox_client.request_window(end=deadline.to_pydatetime(),
                            before_attempt=lambda: self._check(kind, slot, purpose), before_send=before_send):
                        response = call()
                received = self._check(kind, slot, purpose)
                if received < requested or received >= deadline or time.monotonic()>=monotonic_deadline:
                    raise TransportError("RESPONSE_STALE")
                normalized, source_at = normalize(response, received)
                validated = self._check(kind, slot, purpose)
                if validated < received or validated >= deadline or time.monotonic()>=monotonic_deadline:
                    raise TransportError("RESPONSE_STALE")
                if kind == "quote":
                    try:
                        validate_quote(normalized, symbol, validated)
                    except Exception:
                        raise TransportError("QUOTE_INVALID") from None
                result = {"schema_version": "v22-transport-result-v1", "request_id": request_id,
                    "kind": kind, "symbol": symbol, "requested_at": requested.isoformat(),
                    "received_at": received.isoformat(), "validated_at": validated.isoformat(),
                    "source_at": source_at, "response_hash": digest(response.content if kind == "provider" else response),
                    "normalized": normalized}
                guarded._write(result_path, result)
                return result
            except Exception as exc:
                code = exc.code if isinstance(exc, TransportError) else "TRANSPORT_FAILURE"
                failures.append({"request_id": request_id, "code": code,
                                 "failed_at": stamp(self.clock()).isoformat()})
                _sync(failures.path)
                raise TransportError(code) from None

    def candles(self, symbol, slot, purpose="entry"):
        def normalize(response, received):
            try:
                if type(response) is not dict or response.get("status") != "success":
                    raise ValueError()
                data = response["data"]
                if type(data) is not dict:
                    raise ValueError()
                identities = [data[k] for k in ("instrument_key", "instrument_token") if k in data]
                if any(key != STOCKS[symbol] for key in identities):
                    raise TransportError("SOURCE_IDENTITY_MISMATCH")
                if "symbol" in data and data["symbol"] != symbol:
                    raise TransportError("SOURCE_IDENTITY_MISMATCH")
                if any(data[k] not in (5, "5", "5minute", "5minutes") for k in ("interval", "interval_minutes") if k in data):
                    raise ValueError()
                if "unit" in data and data["unit"] != "minutes":
                    raise ValueError()
                frame = candles_to_dataframe(data["candles"])
                if validate_candles(frame).errors or any(frame.timestamp > received):
                    raise ValueError()
                calendar = OfflineSessionCalendar(CALENDAR_PATH)
                completed = frame.loc[frame.timestamp + pd.Timedelta(minutes=5) <= received]
                for at in completed.timestamp:
                    calendar.candle_end(at)
                if completed.empty or completed.iloc[-1].timestamp < stamp(slot):
                    raise TransportError("RESPONSE_STALE")
                rows = [{"timestamp": row.timestamp.isoformat(), **{key: float(getattr(row, key))
                         for key in ("open", "high", "low", "close", "volume")}}
                        for row in completed.itertuples(index=False)]
                return {"symbol": symbol, "instrument_key": STOCKS[symbol], "observed_at": received.isoformat(),
                    "candles": rows, "excluded_incomplete_count": len(frame) - len(completed),
                    "identity_basis": "ECHOED" if identities else "REQUEST_BOUND_NOT_ECHOED"}, rows[-1]["timestamp"]
            except TransportError:
                raise
            except Exception:
                raise TransportError("CANDLE_INVALID") from None
        return self._request("candles", symbol, slot, purpose,
            lambda: upstox_client.get_intraday_candles(STOCKS[symbol], interval=5), normalize)

    def quote(self, symbol, slot, purpose="entry"):
        def normalize(response, received):
            try:
                if type(response) is not dict or response.get("status") != "success":
                    raise ValueError()
                data = response["data"]
                if type(data) is not dict or len(data) != 1:
                    raise ValueError()
                raw = next(iter(data.values()))
                stamp(raw["timestamp"])
                for side in ("buy", "sell"):
                    levels = raw["depth"][side]
                    if type(levels) is not list or not levels:
                        raise ValueError()
                    for level in levels:
                        if (type(level) is not dict or not _number(level.get("price"))
                                or type(level.get("quantity")) is not int or level["quantity"] <= 0):
                            raise ValueError()
                parsed = parse_upstox_full_quote(response, received, expected_instrument_key=STOCKS[symbol])
                if parsed["quote_reason"] == "instrument_identity_mismatch":
                    raise TransportError("SOURCE_IDENTITY_MISMATCH")
                if parsed["quote_valid"] is not True or parsed["spread_available"] is not True:
                    raise ValueError()
                last = raw.get("last_price", raw.get("last_traded_price"))
                if last is not None and not _number(last):
                    raise ValueError()
                result = {"instrument_key": STOCKS[symbol], "provider_timestamp": parsed["provider_timestamp"].isoformat(),
                    "received_at": received.isoformat(), "source_quote_timestamp": None,
                    "timestamp_kind": "response_generated_not_depth_timestamp", "last_price": last}
                result.update({key: parsed[key] for key in ("ask_price", "ask_quantity", "bid_price", "bid_quantity", "spread_bps")})
                return result, result["provider_timestamp"]
            except TransportError:
                raise
            except Exception:
                raise TransportError("QUOTE_INVALID") from None
        return self._request("quote", symbol, slot, purpose, lambda: upstox_client.get_quote(STOCKS[symbol]), normalize)

    def proposals(self, payload, *, frames, candidate_time):
        candidate = stamp(candidate_time)
        try:
            if type(payload) is not dict or set(payload) != {"system_prompt", "user_payload"} or payload["system_prompt"] != SYSTEM_PROMPT:
                raise ValueError()
            body = json.loads(payload["user_payload"])
            if (set(body) != {"schema_version", "candidate_timestamp", "decision_time", "instrument_priority", "market", "quotes"}
                    or body["schema_version"] != core.SCHEMA or stamp(body["candidate_timestamp"]) != candidate
                    or body["instrument_priority"] != self.config["instrument_priority"]
                    or set(body["market"]) != set(self.config["instrument_priority"])
                    or set(frames) != set(self.config["instrument_priority"])
                    or stamp(body["decision_time"]) > stamp(self.clock())
                    or core._encoded(body).decode() != payload["user_payload"]):
                raise ValueError()
        except Exception:
            raise TransportError("PROVIDER_INVALID") from None
        def call(before_send):
            settings = get_settings()
            provider = DeepSeekProvider(api_key=settings.deepseek_api_key, model_id=self.config["transport"]["model"],
                timeout_seconds=self.config["transport"]["request_timeout_seconds"])
            try:
                # SDK/client initialization performs no request; charge/check the
                # physical attempt only after it finishes, immediately before I/O.
                provider._get_client()
                before_send()
                return provider.complete(payload)
            finally:
                if provider._client is not None:
                    provider._client.close()
        def normalize(response, received):
            try:
                model = self.config["transport"]["model"]
                if response.provider_id != "deepseek" or response.model_id != model or response.requested_model_id != model:
                    raise ValueError()
                converted, reasons = guarded._proposal(response.content, self.config["instrument_priority"], frames,
                    candidate, guarded.REVIEWED_SCHEMA)
                result = core._parse(converted, self.config["instrument_priority"], frames)
                result["rationale_codes"] = reasons
                return result, candidate.isoformat()
            except Exception:
                raise TransportError("NO_VALID_PROPOSAL") from None
        return self._request("provider", None, candidate, "entry", call, normalize, payload)

    def request_counts(self):
        """Read logical reservations independently of optional health projection.

        This neither repairs journals nor authorizes a send. Request-time
        reconciliation remains authoritative; unreadable counters raise.
        """
        self._verify_records()
        counts = {}
        for stem in ("market_data", "provider"):
            path = self.root / (stem + "_budget.jsonl")
            counts[stem + "_requests"] = len(AppendOnlyJsonl(path, "request_id")._records) if path.exists() else 0
        return counts

    def health(self):
        policy = self.config.get("transport", {})
        result = {key: policy.get(key) is True for key in ("market_data_enabled", "provider_enabled", "production_start_enabled")}
        result.update(last_quote_at=None, last_candle_at=None, last_provider_at=None,
                      market_data_requests=0, provider_requests=0, status="DISABLED", last_error=None)
        for stem in ("market_data", "provider"):
            path = self.root / (stem + "_budget.jsonl")
            if path.exists():
                result[stem + "_requests"] = len(AppendOnlyJsonl(path, "request_id")._records)
        for path in (self.root / "results").glob("*.json"):
            row = guarded._read(path)
            field = {"quote": "last_quote_at", "candles": "last_candle_at", "provider": "last_provider_at"}[row["kind"]]
            result[field] = max(result[field] or row["received_at"], row["received_at"])
        errors = self.root / "failures.jsonl"
        if errors.exists():
            rows = AppendOnlyJsonl(errors, "request_id").records()
            if rows:
                code = rows[-1]["code"]
                result["last_error"] = code if type(code) is str and code in CODES else "TRANSPORT_FAILURE"
        result["status"] = "FAILED" if result["last_error"] else "READY" if any(
            result[k] for k in ("market_data_enabled", "provider_enabled")) else "DISABLED"
        return result
