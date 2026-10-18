"""Small transactional SQLite ledger for the manual paper-only runner."""
import json
import sqlite3
from datetime import date
from pathlib import Path


DEFAULT_DB = Path(__file__).resolve().parents[1] / "logs" / "paper_state.sqlite3"


class PaperStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
              signal_id TEXT PRIMARY KEY, signal_time TEXT NOT NULL, symbol TEXT NOT NULL,
              outcome TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS position (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trades (
              trade_id TEXT PRIMARY KEY, signal_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS daily (
              day TEXT PRIMARY KEY, loss REAL NOT NULL DEFAULT 0, trades INTEGER NOT NULL DEFAULT 0,
              capital REAL NOT NULL, day_start_capital REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS account (
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), capital REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_time TEXT NOT NULL,
              event_type TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(daily)")}
        if "day_start_capital" not in columns:
            self.db.execute("ALTER TABLE daily ADD COLUMN day_start_capital REAL NOT NULL DEFAULT 100000")
        self.db.commit()

    def close(self):
        self.db.close()

    def record_decision(self, signal_id, signal_time, symbol, outcome, reason, payload):
        return self.db.execute(
            "INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?,?)",
            (signal_id, signal_time, symbol, outcome, reason, json.dumps(payload, default=str)),
        ).rowcount == 1

    def record_audit(self, event_time, event_type, reason, payload=None):
        self.db.execute("INSERT INTO audit(event_time,event_type,reason,payload) VALUES(?,?,?,?)",
                        (str(event_time), event_type, reason, json.dumps(payload or {}, default=str)))

    def get_position(self):
        row = self.db.execute("SELECT payload FROM position WHERE singleton=1").fetchone()
        return json.loads(row[0]) if row else None

    def set_position(self, payload):
        if payload is None:
            self.db.execute("DELETE FROM position WHERE singleton=1")
        else:
            self.db.execute("INSERT OR REPLACE INTO position VALUES(1,?)", (json.dumps(payload, default=str),))

    def daily_state(self, day: date, initial_capital):
        key = day.isoformat()
        self.db.execute("INSERT OR IGNORE INTO account(singleton,capital) VALUES(1,?)", (initial_capital,))
        account = self.db.execute("SELECT capital FROM account WHERE singleton=1").fetchone()[0]
        self.db.execute("INSERT OR IGNORE INTO daily(day,loss,trades,capital,day_start_capital) VALUES(?,0,0,?,?)", (key, account, account))
        row = self.db.execute("SELECT * FROM daily WHERE day=?", (key,)).fetchone()
        return dict(row)

    def save_trade(self, trade_id, signal_id, payload):
        self.db.execute("INSERT INTO trades VALUES(?,?,?)", (trade_id, signal_id, json.dumps(payload, default=str)))

    def update_daily(self, day, loss, trades, capital):
        self.db.execute("UPDATE daily SET loss=?,trades=?,capital=? WHERE day=?", (loss, trades, capital, day))
        self.db.execute("UPDATE account SET capital=? WHERE singleton=1", (capital,))

    def write_daily_summary(self, day):
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        row = self.db.execute("SELECT * FROM daily WHERE day=?", (key,)).fetchone()
        if row is None:
            return None
        trades = [json.loads(item[0]) for item in self.db.execute("SELECT payload FROM trades")]
        closed = [trade for trade in trades if str(trade.get("exit_time", "")).startswith(key)]
        summary = {
            "date": key, "paper_only": True, "opened_trades": row["trades"],
            "closed_trades": len(closed), "daily_loss": row["loss"],
            "ending_capital": row["capital"],
            "gross_pnl": sum(float(trade.get("gross_pnl", 0)) for trade in closed),
            "estimated_costs": sum(float(trade.get("estimated_costs", 0)) for trade in closed),
            "net_pnl": sum(float(trade.get("net_pnl", 0)) for trade in closed),
        }
        report_dir = self.path.parent / "paper_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / f"{key}_summary.json"
        target.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return target

    def commit(self):
        self.db.commit()
