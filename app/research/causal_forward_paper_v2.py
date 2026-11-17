"""Offline causal V2: replayed inputs, sealed event extent, derived checkpoints."""
from contextlib import closing, contextmanager
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3

from app.research import causal_forward_paper as v1
from app.research.deepseek_prospective import _experiment_lock
from app.risk_engine import RiskConfig, RiskState, check_trade_allowed, calculate_position_size

MODE = "CAUSAL_FORWARD_PAPER_V2"
POLICY = "causal-forward-candle-v2"
encode = v1.core._encoded
digest = v1.core._digest
QUARANTINE = ".cfv2-q"
_TOKEN = r"([A-Za-z0-9_-]{43})"
_SEQUENCE = r"(0|[1-9][0-9]{0,19})"
_ANCHOR_STAGE = re.compile(r"\.cfv2-a-" + _SEQUENCE + "-" + _TOKEN + r"-[0-9a-f]{8}\.(writing|ready)")
_PENDING_STAGE = re.compile(r"\.cfv2-p-" + _SEQUENCE + "-" + _TOKEN + "-" + _SEQUENCE + "-" + _TOKEN
                            + r"-[0-9a-f]{8}\.(writing|ready)")


def _check_head(head):
    if (type(head) is not dict or set(head) != {"config", "seq", "hash"}
            or type(head["seq"]) is not int or not 0 <= head["seq"] < 10**20
            or any(type(head[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", head[key]) is None
                   for key in ("config", "hash"))):
        raise ValueError("invalid publication head")


def _hash_token(value):
    return base64.urlsafe_b64encode(bytes.fromhex(value)).decode().rstrip("=")


def _token_hash(value):
    raw = base64.urlsafe_b64decode(value + "=")
    if len(raw) != 32 or _hash_token(raw.hex()) != value:
        raise ValueError("noncanonical staging identity")
    return raw.hex()


def _stage_name(path, value):
    nonce = secrets.token_hex(4)
    if path.name == "anchor.json":
        _check_head(value)
        label = f"a-{value['seq']}-{_hash_token(value['hash'])}"
    elif path.name == "pending.json":
        if type(value) is not dict or set(value) != {"base", "final"}:
            raise ValueError("invalid publication intent")
        base, final = value["base"], value["final"]
        _check_head(base)
        _check_head(final)
        label = f"p-{base['seq']}-{_hash_token(base['hash'])}-{final['seq']}-{_hash_token(final['hash'])}"
    else:
        raise ValueError("unsupported atomic publication")
    return path.parent / f".cfv2-{label}-{nonce}.writing"


def _sync_directory(directory):
    """Request OS metadata persistence; unsupported filesystems fail explicitly.

    Windows requires a writable directory handle with backup semantics, rather
    than a CRT file descriptor. Success is an OS flush acknowledgement, not a
    guarantee about a device's power-loss protection or truthful write cache.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        kernel.FlushFileBuffers.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # GENERIC_WRITE; share read/write/delete; OPEN_EXISTING; BACKUP_SEMANTICS.
        handle = kernel.CreateFileW(str(directory), 0x40000000, 7, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.FlushFileBuffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sync_file(path):
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def _atomic(path, value):
    temporary = _stage_name(path, value)
    # Incomplete writes remain unsealed. Recovery verifies their expected prefix
    # from the owned name, then retains them in quarantine without consuming them.
    with temporary.open("xb") as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    ready = temporary.with_suffix(".ready")
    if ready.exists():
        raise ValueError("ambiguous staging publication")
    os.replace(temporary, ready)
    _sync_directory(path.parent)
    with ready.open("rb") as stream:
        if stream.read(len(encode(value)) + 1) != encode(value):
            raise ValueError("corrupt ready publication")
    os.replace(ready, path)
    _sync_file(path)
    _sync_directory(path.parent)


def _json(path):
    return json.loads(path.read_bytes())


def _public(value):
    value = json.loads(encode(value))
    value.update(mode=MODE, policy=POLICY, trade_placement_enabled=False)
    return value


def _equity(db, initial, day):
    account = v1._account(db, initial, day)
    balance = account["balance"]
    if not math.isfinite(balance):
        raise ValueError("nonfinite reconstructed equity")
    account.update(eligible_equity=max(0.0, balance),
                   available_cash=max(0.0, balance - account["reserved_cash"]))
    return account


class _Replay(v1.CausalForwardPaper):
    """Ephemeral V1 causal mechanics; no stored V1 state is accepted as input."""
    def __init__(self, capital, kind):
        self._capital, self._kind = capital, kind
        self._calendar = v1.OfflineSessionCalendar(v1.RETAINED_CALENDAR)
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE evidence(sha256 TEXT PRIMARY KEY,body BLOB NOT NULL);
            CREATE TABLE journal(seq INTEGER PRIMARY KEY,decision TEXT,slot TEXT,evidence_sha256 TEXT,
                                 body BLOB,body_sha256 TEXT,UNIQUE(decision,slot));
        """)

    @contextmanager
    def _transaction(self):
        with self.db:
            yield self.db

    def select_transport(self, *, decision_id, decision_ready_at, proposal, instrument_key, as_of, evidence):
        """Quantity-free selection. All financial sizing waits for the causal close."""
        ready, at = v1._stamp(decision_ready_at), v1._stamp(as_of)
        frame = v1.pd.DataFrame([{'close': proposal.get('entry_price')}])
        v1.core._parse(encode({'schema_version':v1.core.SCHEMA,'action':'BUY','proposals':[proposal]}).decode(),
                       list(v1.core.STOCKS), {proposal.get('symbol'):frame})
        if type(decision_id) is not str or not decision_id.strip(): raise ValueError('decision ID required')
        request = dict(decision_id=decision_id,decision_ready_at=ready.isoformat(),proposal=proposal,
                       instrument_key=instrument_key,quantity=None,four_index_down_count=None,transport_evidence=evidence)
        with self._transaction() as db:
            sha = v1._freeze(db,encode(request))
            conflict=v1._quarantine(db,decision_id,'selection',sha)
            if conflict: return conflict
            prior=db.execute("SELECT * FROM journal WHERE decision=? AND slot='selection'",(decision_id,)).fetchone()
            if prior:
                return v1._decode(prior) if prior['evidence_sha256']==sha else v1._conflict(db,decision_id,at,prior,sha)
            v1._clock(db,at)
            if db.execute("SELECT 1 FROM journal WHERE slot='selection' AND json_extract(CAST(body AS TEXT),'$.decision_ready_at')=? "
                          "AND json_extract(CAST(body AS TEXT),'$.instrument_key')=?",(ready.isoformat(),instrument_key)).fetchone():
                raise ValueError('duplicate instrument decision at this time')
            state={**request,'policy':v1.POLICY,'mode':v1.MODE,'provenance':self._kind,'recorded_at':at.isoformat(),
                   'decision_sha256':sha,'state':'PROPOSED','bars_held':0,'trade_placement_enabled':False}
            v1._append(db,decision_id,'proposal',sha,state)
            start=ready.floor('5min')+v1.BAR
            reason=None
            try:
                if not self._entry_allowed(at) or self._calendar.candle_end(start)!=self._calendar.candle_end(ready.floor('5min')):
                    reason='unsupported_session_or_entry_cutoff'
            except ValueError: reason='unsupported_session_or_entry_cutoff'
            if v1.core.STOCKS[proposal['symbol']]!=instrument_key: reason='instrument_mismatch'
            state.update(state='ENTRY_REJECTED' if reason else 'ENTRY_PENDING',reason=reason,next_start=None if reason else start.isoformat())
            return v1._append(db,decision_id,'selection',sha,state)

    def _enter(self, db, state, price, at, start):
        proposal = state["proposal"]
        account = _equity(db, self._capital, at.date())
        # The daily base retains V1 semantics. Trade risk uses current equity.
        allowed, reason = check_trade_allowed(
            RiskConfig(capital=self._capital),
            RiskState(account["daily_loss"], account["open_positions"], account["trades_today"]))
        check = None
        if not self._entry_allowed(at) or at >= self._calendar.candle_end(start):
            reason = "entry_cutoff"
        elif not v1._prices_valid(price, proposal["stop_loss"], proposal["target_price"]):
            reason = "invalid_causal_entry_prices"
        elif not allowed:
            pass
        elif account["eligible_equity"] <= 0 or account["available_cash"] <= 0:
            reason = "insufficient_available_cash"
        else:
            if state['quantity'] is None:
                # Existing RiskEngine maximum, followed by unchanged cost-inclusive
                # admission. Do not increase/reduce quantity to evade rejection.
                state['quantity']=calculate_position_size(RiskConfig(capital=max(.01,min(account['eligible_equity'],account['available_cash']))),
                                                          price,proposal['stop_loss']).quantity
            if state['quantity'] < 1:
                reason='insufficient_available_cash'
            else:
                check = v1.core._check(
                    {"config": {"initial_capital": account["eligible_equity"]}, "cash": account["available_cash"]},
                    price, proposal["stop_loss"], proposal["target_price"], state["quantity"], at)
                reason = None if check["eligible"] else check["reason"]
        state["entry_account"] = account
        if check is not None:
            state["entry_check"] = check
        if reason:
            state.update(state="ENTRY_REJECTED", reason=reason, causal_reference_close=price)
            return
        state.update(state="OPEN", entry_price=price, entry_accepted_at=at.isoformat(),
                     entry_reference_at=(start + v1.BAR).isoformat(), next_start=(start + v1.BAR).isoformat(),
                     entry_cash_required=check["entry_cash_required"],
                     expected_net_target_pnl=check["estimated_net_at_target"],
                     expected_gross_target_pnl=(proposal["target_price"] - price) * state["quantity"],
                     mfe_price=0.0, mae_price=0.0, mfe_upper_price=0.0, mae_lower_price=0.0,
                     partial_first_bar=False, same_bar_ambiguous=False)


def _identity(command):
    args = dict(command["args"])
    args.pop("as_of", None)  # Identical observation retries retain their first acceptance clock.
    return digest({"operation": command["operation"], "args": args})


def _event(seq, previous, command_id, kind, trade, payload):
    body = {"seq": seq, "previous": previous, "event_id": digest([command_id, kind]),
            "kind": kind, "trade": trade, "payload": payload}
    return {**body, "hash": digest(body)}


def _settlement(state):
    entry = digest([state["decision_sha256"], state["observation_hashes"][0],
                    state["entry_accepted_at"], state["entry_price"], state["quantity"]])
    exit_id = digest([entry, state["observation_hashes"][-1], state["exit_time"], state["exit_price"]])
    return {"settlement_id": digest([POLICY, state["decision_id"], entry, exit_id]),
            "trade_id": state["decision_id"], "entry_id": entry, "exit_id": exit_id,
            "exit_time": state["exit_time"], "settled_at": state["recorded_at"], "quantity": state["quantity"],
            "gross_pnl": state["gross_pnl"], "modeled_costs": state["modeled_costs"],
            "cost_components": state["cost_components"], "net_pnl": state["net_pnl"]}


class CausalForwardPaperV2:
    def __init__(self, directory, *, capital, provenance="UNVERIFIED_CALLER_SUPPLIED", transport_binding=None):
        from app.research.v2_transport_contract import TRANSPORT_KIND, validate_binding
        if not v1.core._number(capital) or capital <= 0 or provenance not in (*v1.OFFLINE_KINDS,TRANSPORT_KIND):
            raise ValueError("invalid offline V2 configuration")
        if provenance == TRANSPORT_KIND:
            transport_binding=validate_binding(transport_binding)
        elif transport_binding is not None: raise ValueError('transport binding in offline ledger')
        self.directory = Path(directory).resolve()
        self.capital, self.provenance = capital, provenance
        self.path = self.directory / "causal_forward_v2.sqlite3"
        self.anchor = self.directory / "anchor.json"
        self.pending = self.directory / "pending.json"
        app = Path(__file__).resolve().parents[1]
        sources = ["research/causal_forward_paper_v2.py", "research/causal_forward_paper.py",
                   "research/ai_led_offline.py", "research/deepseek_prospective.py",
                   "research/prospective_paper_fills.py", "research/offline_session_calendar.py",
                   "risk_engine.py", "cost_model.py", "candle_integrity.py",
                   "session_manifest_v3.py", "offline_market_data_v1.py"]
        self.config = {"mode": MODE, "policy": POLICY, "initial_capital": capital, "provenance": provenance,
                       "calendar": v1.OfflineSessionCalendar(v1.RETAINED_CALENDAR).identity,
                       "instruments": dict(v1.core.STOCKS), "daily_loss_base": "initial_experiment_capital",
                       "risk_base": "reconstructed_realized_equity_including_profits",
                       "sources": {p: hashlib.sha256((app / p).read_bytes()).hexdigest() for p in sources}}
        if transport_binding is not None: self.config['transport_binding']=transport_binding
        self.genesis = digest(self.config)
        with _experiment_lock(self.directory), self._connection() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                initial_files = {self.path.name, self.path.name + "-journal", ".prospective.lock"}
                if any(path.name not in initial_files for path in self.directory.iterdir()):
                    raise ValueError("missing anchored database")
                with db:
                    db.execute("CREATE TABLE config(body BLOB)")
                    db.execute("INSERT INTO config VALUES (?)", (encode(self.config),))
                    db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY,event_id TEXT UNIQUE,body BLOB)")
                    db.execute("CREATE TABLE checkpoints(seq INTEGER PRIMARY KEY,body BLOB,sha256 TEXT)")
                    db.execute("CREATE TABLE settlements(id TEXT PRIMARY KEY,trade TEXT UNIQUE,body BLOB)")
                    for table in ("config", "events", "checkpoints", "settlements"):
                        for operation in ("UPDATE", "DELETE"):
                            db.execute(f"""CREATE TRIGGER {table}_{operation} BEFORE {operation} ON {table}
                                BEGIN SELECT RAISE(ABORT,'immutable V2 ledger'); END""")
                _atomic(self.anchor, self._head([]))
            self._verified(db)

    @contextmanager
    def _connection(self):
        allowed = {self.path.name, self.path.name + "-journal", "anchor.json", "pending.json", ".prospective.lock"}
        for path in self.directory.iterdir():
            if path.name == QUARANTINE:
                if path.is_symlink() or path.is_junction() or not path.is_dir():
                    raise ValueError("invalid staging quarantine")
            elif path.name not in allowed:
                if (path.is_symlink() or not path.is_file()
                        or not (_ANCHOR_STAGE.fullmatch(path.name) or _PENDING_STAGE.fullmatch(path.name))):
                    raise ValueError("dedicated V2 directory required")
        if not self.path.exists() and any(path.name != ".prospective.lock" for path in self.directory.iterdir()):
            raise ValueError("missing anchored database")
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            yield db

    def _head(self, events):
        return {"config": self.genesis, "seq": len(events), "hash": events[-1]["hash"] if events else self.genesis}

    def _intent(self, pending):
        if type(pending) is not dict or set(pending) != {"base", "final"}:
            raise ValueError("invalid publication intent")
        base, final = pending["base"], pending["final"]
        _check_head(base)
        _check_head(final)
        if (base["config"] != self.genesis or final["config"] != self.genesis
                or final["seq"] - base["seq"] not in (1, 3) or final["hash"] == base["hash"]):
            raise ValueError("nonmonotonic or invalid publication intent")
        return base, final

    def _publication(self, events, anchor, pending):
        """Validate the entire transition before any witness or artifact changes."""
        head = self._head(events)
        base, final = self._intent(pending)
        if base["seq"] > len(events) or self._head(events[:base["seq"]]) != base:
            raise ValueError("publication base ancestry mismatch")
        if head == base and anchor == base:
            # No SQLite commit occurred. Retain the exact committed base.
            return False
        if head != final or anchor not in (base, final):
            raise ValueError("incomplete or conflicting publication")
        # The verified final chain contains the exact base prefix above.
        return anchor == base

    def _staging(self, events, anchor, pending):
        paths = sorted(path for path in self.directory.iterdir()
                       if _ANCHOR_STAGE.fullmatch(path.name) or _PENDING_STAGE.fullmatch(path.name))
        if len(paths) > 1:
            raise ValueError("ambiguous staging artifacts")
        head = self._head(events)
        for path in paths:
            match = _ANCHOR_STAGE.fullmatch(path.name)
            if match:
                seq, token, phase = match.groups()
                value = {"config": self.genesis, "seq": int(seq), "hash": _token_hash(token)}
                if (value != head or (pending is not None and head != pending["final"])
                        or (pending is None and anchor not in (None, head))):
                    raise ValueError("staged anchor contradicts verified state")
            else:
                base_seq, base_token, final_seq, final_token, phase = _PENDING_STAGE.fullmatch(path.name).groups()
                value = {"base": {"config": self.genesis, "seq": int(base_seq), "hash": _token_hash(base_token)},
                         "final": {"config": self.genesis, "seq": int(final_seq), "hash": _token_hash(final_token)}}
                self._intent(value)
                if pending is not None or value["base"] != head or anchor != head:
                    raise ValueError("staged intent contradicts verified state")
            expected = encode(value)
            with path.open("rb") as stream:
                actual = stream.read(len(expected) + 1)
            if (phase == "ready" and actual != expected) or not expected.startswith(actual):
                raise ValueError("corrupt staging artifact")
        return paths

    def _quarantine_staging(self, paths):
        if not paths:
            return
        quarantine = self.directory / QUARANTINE
        quarantine.mkdir(exist_ok=True)
        _sync_directory(self.directory)
        for path in paths:
            target = quarantine / (hashlib.sha256(path.name.encode()).hexdigest() + path.suffix)
            if target.exists():
                raise ValueError("duplicate quarantine artifact")
            _sync_file(path)
            os.replace(path, target)
            _sync_file(target)
            _sync_directory(quarantine)
            _sync_directory(self.directory)

    def _finish_publication(self):
        # Also re-establish boundaries after a prior flush/acknowledgement loss.
        # Call only after all ledger, ancestry and staging checks have passed.
        _sync_file(self.anchor)
        quarantine = self.directory / QUARANTINE
        if quarantine.exists():
            _sync_directory(quarantine)
        _sync_directory(self.directory)
        if self.pending.exists():
            self.pending.unlink()
            _sync_directory(self.directory)

    def _execute(self, engine, command):
        from app.research.v2_transport_contract import TRANSPORT_KIND, validate_proposal_evidence, validate_observation_evidence
        op, args = command["operation"], command["args"]
        if op == "propose":
            if self.provenance==TRANSPORT_KIND: raise ValueError('transport selection API required')
            result = engine.propose(**args)
            at = v1._stamp(args["decision_ready_at"])
        elif op == "observe":
            if self.provenance==TRANSPORT_KIND and args['observation'] is not None: raise ValueError('transport observation API required')
            decision = args["decision_id"]
            result = engine.observe(decision, **{k: v for k, v in args.items() if k != "decision_id"})
            at = v1._stamp(args["as_of"])
        elif op == 'select_transport':
            if self.provenance!=TRANSPORT_KIND: raise ValueError('transport command in offline ledger')
            validate_proposal_evidence(args['evidence'],args['decision_ready_at'],args['as_of'])
            result=engine.select_transport(**args);at=v1._stamp(args['as_of'])
        elif op == 'observe_transport':
            if self.provenance!=TRANSPORT_KIND: raise ValueError('transport command in offline ledger')
            validate_observation_evidence(args['evidence'],args['observation'],args['as_of'])
            decision=args['decision_id']
            result=engine.observe(decision,**{k:v for k,v in args.items() if k not in {'decision_id','evidence'}})
            if result['state']=='OBSERVATION_MISSING':
                raise ValueError('invalid transport candle observation')
            at=v1._stamp(args['as_of'])
        else:
            raise ValueError("unknown causal command")
        return _public(result), at

    def _checkpoint(self, engine, result, at, settlements):
        latest = {}
        histories = []
        for row in engine.db.execute("SELECT * FROM journal ORDER BY seq"):
            value = _public(v1._decode(row))
            histories.append(value)
            latest[row["decision"]] = value
        counts = {s: sum(r["state"] == s for r in latest.values()) for s in
                  ("ENTRY_PENDING", "ENTRY_REJECTED", "OPEN", "OBSERVATION_MISSING", "INVALID_OR_CONFLICTING")}
        net = math.fsum(s["net_pnl"] for s in settlements.values())
        summary = {"settled_trades": len(settlements), "realized_pnl": net,
                   "wins": sum(s["net_pnl"] > 0 for s in settlements.values()),
                   "win_rate": sum(s["net_pnl"] > 0 for s in settlements.values()) / len(settlements) if settlements else None,
                   "expectancy": net / len(settlements) if settlements else None, "states": counts,
                   "unresolved": counts["OBSERVATION_MISSING"]}
        return {"result": result, "history": histories, "account": _equity(engine.db, self.capital, at.date()),
                "settlements": list(settlements.values()), "performance": summary, "as_of": at.isoformat()}

    def _replay(self, commands):
        engine = _Replay(self.capital, self.provenance)
        events, checkpoints, settlements, seen = [], {}, {}, set()
        try:
            for command in commands:
                command_id = _identity(command)
                if command_id in seen:
                    raise ValueError("duplicate authoritative command")
                seen.add(command_id)
                result, at = self._execute(engine, command)
                trade = command["args"]["decision_id"]
                roles = [("COMMAND", command)]
                if result["state"].startswith("CLOSED_"):
                    settlement = _settlement(result)
                    if trade in settlements:
                        raise ValueError("duplicate economic closure")
                    settlements[trade] = settlement
                    roles += [("EXIT", {"result": result, "exit_id": settlement["exit_id"]}),
                              ("SETTLEMENT", settlement)]
                for kind, payload in roles:
                    previous = events[-1]["hash"] if events else self.genesis
                    events.append(_event(len(events)+1, previous, command_id, kind, trade, payload))
                checkpoints[len(events)] = self._checkpoint(engine, result, at, settlements)
            return events, checkpoints, settlements
        finally:
            engine.db.close()

    def _verified(self, db):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables != {"config", "events", "checkpoints", "settlements"}:
            raise ValueError("invalid V2 schema")
        if [r[0] for r in db.execute("SELECT body FROM config")] != [encode(self.config)]:
            raise ValueError("immutable V2 configuration mismatch")
        stored = []
        for row in db.execute("SELECT * FROM events ORDER BY seq"):
            event = json.loads(row["body"])
            if row["seq"] != event.get("seq") or row["event_id"] != event.get("event_id"):
                raise ValueError("event row identity mismatch")
            stored.append(event)
        commands = [e["payload"] for e in stored if e.get("kind") == "COMMAND"]
        events, checkpoints, settlements = self._replay(commands)
        if encode(events) != encode(stored):
            raise ValueError("event chain or causal replay mismatch")
        rows = list(db.execute("SELECT * FROM checkpoints ORDER BY seq"))
        if len(rows) != len(checkpoints):
            raise ValueError("checkpoint extent mismatch")
        for row in rows:
            expected = checkpoints.get(row["seq"])
            if expected is None or row["body"] != encode(expected) or row["sha256"] != digest(expected):
                raise ValueError("semantic checkpoint mismatch")
        expected_settlements = sorted((s["settlement_id"], t, encode(s)) for t, s in settlements.items())
        actual_settlements = sorted(tuple(row) for row in db.execute("SELECT id,trade,body FROM settlements"))
        if expected_settlements != actual_settlements:
            raise ValueError("settlement identity or arithmetic mismatch")
        head = self._head(events)
        anchor = _json(self.anchor) if self.anchor.exists() else None
        pending = _json(self.pending) if self.pending.exists() else None
        if anchor is not None:
            _check_head(anchor)
        if pending is not None:
            publish = self._publication(events, anchor, pending)
        else:
            publish = False
        # Inspect owned staging only after semantic ledger and intent validation.
        if anchor is None and (events or pending is not None):
            raise ValueError("missing committed head anchor")
        if pending is None and anchor is not None and anchor != head:
            raise ValueError("committed event extent mismatch")
        stages = self._staging(events, anchor, pending)
        if anchor is None:
            if not stages or _ANCHOR_STAGE.fullmatch(stages[0].name) is None:
                raise ValueError("missing committed head anchor")
            publish = True  # Verified empty genesis with an interrupted owned anchor.
        self._quarantine_staging(stages)
        if publish:
            _atomic(self.anchor, head)
        self._finish_publication()
        return commands, events, checkpoints, settlements

    def _insert(self, db, events, checkpoints, settlements):
        for event in events:
            db.execute("INSERT INTO events VALUES (?,?,?)", (event["seq"], event["event_id"], encode(event)))
        for seq, checkpoint in checkpoints.items():
            db.execute("INSERT INTO checkpoints VALUES (?,?,?)", (seq, encode(checkpoint), digest(checkpoint)))
        for trade, settlement in settlements.items():
            db.execute("INSERT INTO settlements VALUES (?,?,?)",
                       (settlement["settlement_id"], trade, encode(settlement)))

    def _apply(self, command):
        command = json.loads(encode(command))
        with _experiment_lock(self.directory), self._connection() as db:
            commands, old_events, old_checkpoints, old_settlements = self._verified(db)
            for old in commands:
                if _identity(old) == _identity(command):
                    # An identical retry never creates another event or settlement.
                    old_at = old["args"].get("as_of")
                    if old_at and v1._stamp(command["args"]["as_of"]) < v1._stamp(old_at):
                        raise ValueError("retry precedes original acceptance")
                    index = commands.index(old)
                    _, prefix, _ = self._replay(commands[:index+1])
                    result = prefix[max(prefix)]["result"]
                    if (old_checkpoints[max(old_checkpoints)]["performance"]["states"]["INVALID_OR_CONFLICTING"]
                            and result["state"] != "INVALID_OR_CONFLICTING"):
                        raise ValueError("quarantined ledger")
                    return result
            events, checkpoints, settlements = self._replay(commands + [command])
            added = events[len(old_events):]
            new_checkpoints = {k:v for k,v in checkpoints.items() if k not in old_checkpoints}
            new_settlements = {k:v for k,v in settlements.items() if k not in old_settlements}
            intent = {"base": self._head(old_events), "final": self._head(events)}
            if not self._publication(events, intent["base"], intent):
                raise ValueError("publication must advance committed state")
            _atomic(self.pending, intent)
            with db:
                self._insert(db, added, new_checkpoints, new_settlements)
            _atomic(self.anchor, self._head(events))
            self._finish_publication()
            return checkpoints[max(checkpoints)]["result"]

    def propose(self, **arguments):
        arguments = dict(arguments)
        arguments["decision_ready_at"] = v1._stamp(arguments["decision_ready_at"]).isoformat()
        return self._apply({"operation": "propose", "args": arguments})

    def select_transport(self, *, decision_id, decision_ready_at, as_of, proposal, instrument_key, evidence):
        return self._apply({'operation':'select_transport','args':dict(decision_id=decision_id,
            decision_ready_at=v1._stamp(decision_ready_at).isoformat(),as_of=v1._stamp(as_of).isoformat(),
            proposal=proposal,instrument_key=instrument_key,evidence=evidence)})

    def observe_transport(self, decision_id, *, expected_start, as_of, observation, evidence):
        return self._apply({'operation':'observe_transport','args':dict(decision_id=decision_id,
            expected_start=v1._stamp(expected_start).isoformat(),as_of=v1._stamp(as_of).isoformat(),
            observation=observation,evidence=evidence)})

    def observe(self, decision_id, *, expected_start, as_of, observation):
        return self._apply({"operation": "observe", "args": {
            "decision_id": decision_id, "expected_start": v1._stamp(expected_start).isoformat(),
            "as_of": v1._stamp(as_of).isoformat(), "observation": observation}})

    def _view(self):
        with _experiment_lock(self.directory), self._connection() as db:
            _, _, checkpoints, _ = self._verified(db)
            return checkpoints[max(checkpoints)] if checkpoints else None

    def history(self, decision_id):
        view = self._view()
        return [r for r in view["history"] if r["decision_id"] == decision_id] if view else []

    def account(self, as_of):
        at = v1._stamp(as_of)
        with _experiment_lock(self.directory), self._connection() as db:
            commands, _, checkpoints, _ = self._verified(db)
            if checkpoints:
                view = checkpoints[max(checkpoints)]
                if at < v1._stamp(view["as_of"]):
                    raise ValueError("account query predates committed state")
                if view["performance"]["states"]["INVALID_OR_CONFLICTING"]:
                    raise ValueError("quarantined ledger")
            engine = _Replay(self.capital, self.provenance)
            try:
                for command in commands:
                    self._execute(engine, command)
                return _equity(engine.db, self.capital, at.date())
            finally:
                engine.db.close()

    def performance(self):
        view = self._view()
        if view and view["performance"]["states"]["INVALID_OR_CONFLICTING"]:
            raise ValueError("quarantined ledger")
        return view["performance"] if view else {"settled_trades": 0, "realized_pnl": 0.0,
                                                "wins": 0, "win_rate": None, "expectancy": None,
                                                "states": {}, "unresolved": 0}
