import json
import sqlite3

import pytest

from scripts.compare_index_filter import INDICES, compare, read_paper


def test_compares_recorded_fills_and_rejects_missing_context(tmp_path):
    path = tmp_path / "paper.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE trades (payload TEXT)")
    trade = {"symbol": "A", "signal_time": "2026-11-05T12:00:00+05:30",
             "context": dict.fromkeys(INDICES, "flat"), "gross_pnl": 2, "estimated_costs": 3, "net_pnl": -1}
    db.execute("INSERT INTO trades VALUES (?)", (json.dumps(trade),))
    trade.update(symbol="B", gross_pnl=10, net_pnl=7)
    trade["context"]["SENSEX"] = "down"
    db.execute("INSERT INTO trades VALUES (?)", (json.dumps(trade),))
    db.commit()
    result = compare(read_paper(path))
    assert result["baseline"]["net_inr"] == 6
    assert result["zero_down_indices"]["net_per_trade_inr"] == -1
    del trade["context"]["SENSEX"]
    db.execute("INSERT INTO trades VALUES (?)", (json.dumps(trade),))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="index directions"):
        compare(read_paper(path))
