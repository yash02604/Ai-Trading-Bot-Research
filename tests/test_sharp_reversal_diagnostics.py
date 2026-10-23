import pytest

from app.research.sharp_reversal_diagnostics import audit_rows, grouped, max_closed_drawdown, summarize
from scripts.run_sharp_reversal_diagnostics import sha256


def trade(**updates):
    row = {
        "strategy_id": "frozen_reversal_v1", "symbol": "TEST",
        "signal_timestamp": "2026-01-02T09:15:00+05:30",
        "entry_timestamp": "2026-01-02T09:20:00+05:30",
        "exit_timestamp": "2026-01-02T09:45:00+05:30",
        "entry_price": "100", "exit_price": "99", "stop_loss": "99",
        "quantity": "10", "planned_risk": "10", "capital_exposure": "1000",
        "gross_pnl": "-10", "brokerage": "2", "stt": "0.1", "exchange": "0.1",
        "ipft": "0.01", "sebi": "0.001", "stamp_duty": "0.01", "gst": "0.4",
        "slippage": "0.2", "total_cost": "2.821", "net_pnl": "-12.821",
        "exit_reason": "horizon_exit", "trend_regime": "sideways", "volatility_regime": "low_volatility",
    }
    row.update(updates)
    return row


def test_summary_reconciles_costs_and_net_pf():
    rows = [trade(), trade(symbol="OTHER", gross_pnl="20", net_pnl="17.179", exit_timestamp="2026-01-02T10:10:00+05:30")]
    result = summarize(rows, 100_000)
    assert result["trade_count"] == 2
    assert result["gross_pnl"] == 10
    assert result["costs"] == pytest.approx(5.642)
    assert result["net_pnl"] == pytest.approx(4.358)
    assert result["net_profit_factor"] == pytest.approx(17.179 / 12.821)


def test_drawdown_uses_closed_trade_equity_and_prior_peak():
    assert max_closed_drawdown([200, -180, 50], 1000) == pytest.approx(-15.0)


def test_grouped_entry_labels_and_label_durations_are_explicit():
    rows = [trade(), trade(symbol="OTHER", exit_timestamp="2026-01-02T09:50:00+05:30")]
    assert grouped(rows, "entry_time_label", 100_000)[0]["group"] == "09:20"
    holding = grouped(rows, "holding_label_minutes", 100_000)
    assert [(item["group"], item["trade_count"]) for item in holding] == [("25", 1), ("30", 1)]


def test_audit_flags_accounting_and_timing_inconsistencies():
    bad = trade(total_cost="1", net_pnl="-10", entry_timestamp="2026-01-02T09:25:00+05:30")
    result = audit_rows([bad], 100_000)
    assert result["errors"]["itemized_cost_sum_mismatch"] == 1
    assert result["errors"]["net_not_gross_less_cost"] == 1
    assert result["errors"]["entry_not_next_5m_label"] == 1


def test_missing_numeric_fields_fail_instead_of_being_imputed():
    incomplete = trade()
    del incomplete["slippage"]
    with pytest.raises(ValueError, match="missing required field: slippage"):
        audit_rows([incomplete], 100_000)


def test_duplicate_strategy_signal_and_fill_keys_are_reported():
    row = trade()
    result = audit_rows([row, row.copy()], 100_000)
    assert result["errors"]["duplicate_strategy_symbol_signal_keys"] == 1
    assert result["errors"]["duplicate_strategy_symbol_entry_keys"] == 1


def test_source_hash_is_repeatable(tmp_path):
    artifact = tmp_path / "source.txt"
    artifact.write_text("immutable diagnostic source\n", encoding="utf-8")
    assert sha256(artifact) == sha256(artifact)
