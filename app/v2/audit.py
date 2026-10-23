"""Append-only SQLite decision/audit ledger for paper-only V2 records."""
import json
from pathlib import Path
import re
import sqlite3

from .contracts import to_jsonable

_SECRET_KEY = re.compile(r"(api[_-]?key|access[_-]?token|password|secret|authorization|cookie)", re.I)


def _safe_payload(value):
    converted = to_jsonable(value)
    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if _SECRET_KEY.search(str(key)):
                    raise ValueError("audit payload contains a prohibited credential-like field")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(converted)
    return json.dumps(converted, sort_keys=True, separators=(",", ":"), allow_nan=False)


class V2AuditLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level="IMMEDIATE")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS decisions (
            signal_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL,
            payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT NOT NULL,
            event_type TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE UNIQUE INDEX IF NOT EXISTS events_paper_idempotency
            ON events(signal_id,event_type) WHERE event_type IN ('PAPER_FILL','PAPER_OUTCOME');
          CREATE UNIQUE INDEX IF NOT EXISTS events_ai_research_idempotency
            ON events(signal_id,event_type)
            WHERE event_type IN ('AI_HYPOTHESIS_DISCOVERY','AI_STRATEGY_HYPOTHESIS');
          CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
            BEGIN SELECT RAISE(ABORT, 'V2 decisions are append-only'); END;
          CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
            BEGIN SELECT RAISE(ABORT, 'V2 decisions are append-only'); END;
          CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'V2 audit events are append-only'); END;
          CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'V2 audit events are append-only'); END;
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def append_decision(self, signal_id: str, recorded_at: str, envelope, gate_result,
                        final_decision: str | None = None) -> bool:
        recommendation = envelope.recommendation.action.value
        final_decision = final_decision or (recommendation if gate_result.approved else "HOLD")
        payload = _safe_payload({"envelope": envelope, "risk_result": gate_result,
                                 "final_decision": final_decision, "paper_only": True})
        try:
            self.db.execute("INSERT INTO decisions VALUES(?,?,?)", (signal_id, recorded_at, payload))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def append_ai_research_decision(self, signal_id: str, recorded_at: str, envelope,
                                   gate_result, final_decision: str) -> bool:
        """Append a versioned AI-led research decision without enabling paper execution."""
        risk_record = {key: getattr(gate_result, key) for key in
                       ("approved", "reason", "quantity", "allocation", "planned_risk")}
        payload = _safe_payload({"envelope": envelope, "risk_result": risk_record,
                                 "final_decision": final_decision, "paper_only": True,
                                 "execution_enabled": False})
        try:
            self.db.execute("INSERT INTO decisions VALUES(?,?,?)", (signal_id, recorded_at, payload))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def append_event(self, signal_id: str, event_type: str, recorded_at: str, payload) -> bool:
        body = _safe_payload(payload)
        try:
            self.db.execute("INSERT INTO events(signal_id,event_type,recorded_at,payload) VALUES(?,?,?,?)",
                            (signal_id, event_type, recorded_at, body))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def events_by_type(self, event_type: str) -> tuple[tuple[str, str, str], ...]:
        rows = self.db.execute(
            "SELECT signal_id,recorded_at,payload FROM events WHERE event_type=? ORDER BY event_id",
            (event_type,),
        ).fetchall()
        return tuple(rows)

    def unclosed_paper_fills(self) -> dict[str, dict]:
        """Validate ledger fill/outcome pairs; do not silently recover partial/corrupt state."""
        open_fills: dict[str, dict] = {}
        rows = self.db.execute(
            "SELECT signal_id,event_type,payload FROM events WHERE event_type IN ('PAPER_FILL','PAPER_OUTCOME') ORDER BY event_id"
        ).fetchall()
        for signal_id, event_type, payload in rows:
            try:
                item = json.loads(payload)
                if not isinstance(item, dict) or item.get("paper_only") is not True:
                    raise ValueError
                if event_type == "PAPER_FILL":
                    if signal_id in open_fills:
                        raise ValueError
                    open_fills[signal_id] = item
                elif signal_id not in open_fills:
                    raise ValueError
                else:
                    del open_fills[signal_id]
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("paper ledger contains malformed or contradictory records") from error
        return open_fills

    def seen(self, signal_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM decisions WHERE signal_id=?", (signal_id,)).fetchone() is not None
