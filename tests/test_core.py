import json

import pandas as pd
import pytest

from app import ai_engine, market_analysis, reversal_detector, upstox_client
from app.paper_trader import PaperTrader
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from app.trading_engine import TradingEngine
import backtester as backtester_module


def _candles(count=60):
    start = pd.Timestamp("2026-09-01T09:15:00+05:30")
    return [
        [(start + pd.Timedelta(minutes=5*i)).isoformat(), 100+i, 101+i, 99+i, 100+i, 1000+i, 0]
        for i in range(count)
    ]


def test_indicator_values_and_insufficient_data():
    frame = market_analysis.candles_to_dataframe(_candles())
    result = market_analysis.calculate_indicators(frame)
    assert {"ema_9", "ema_20", "rsi_14", "macd", "macd_signal", "macd_histogram", "volume_ma_20"} <= set(result)
    assert result.iloc[-1].volume_ma_20 == pytest.approx(sum(range(1040, 1060)) / 20)
    with pytest.raises(ValueError, match="Insufficient"):
        market_analysis.calculate_indicators(frame.iloc[:34])


def test_indicators_reject_nonfinite_data():
    frame = market_analysis.candles_to_dataframe(_candles())
    frame.loc[0, "close"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        market_analysis.calculate_indicators(frame)


@pytest.mark.parametrize(
    ("decline_pct", "expected"),
    [
        (-2.0001, False),
        (-2.0000, False),
        (-1.9999, True),
        (-1.0001, True),
        (-1.0000, True),
        (-0.9999, False),
    ],
)
def test_frozen_reversal_decline_boundaries(decline_pct, expected):
    row = {"decline_pct": decline_pct, "rsi_14": 22, "reversal_condition": True}
    assert reversal_detector.is_frozen_reversal(row) is expected


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"decline_pct": "bad", "rsi_14": 22, "reversal_condition": True},
        {"decline_pct": float("nan"), "rsi_14": 22, "reversal_condition": True},
        {"decline_pct": -1.5, "rsi_14": float("inf"), "reversal_condition": True},
    ],
)
def test_frozen_reversal_invalid_inputs_fail_closed(row):
    assert not reversal_detector.is_frozen_reversal(row)


def test_frozen_reversal_rules_and_invalid_inputs():
    assert reversal_detector.is_frozen_reversal({"decline_pct": -1.5, "rsi_14": 22, "reversal_condition": True})
    assert not reversal_detector.is_frozen_reversal({"decline_pct": -0.9, "rsi_14": 22, "reversal_condition": True})
    with pytest.raises(ValueError):
        reversal_detector.detect_reversal_candidates(pd.DataFrame(), decline_candles=0)


def test_risk_limits_and_position_size():
    config = RiskConfig(capital=100_000, max_risk_per_trade=0.01)
    size = calculate_position_size(config, 100, 99)
    assert size.quantity == 1000
    assert size.planned_risk == 1000
    assert check_trade_allowed(config, RiskState(daily_loss=2000)) == (False, "Daily loss limit reached")
    assert calculate_position_size(RiskConfig(capital=50), 100, 99).quantity == 0
    with pytest.raises(ValueError, match="below entry"):
        calculate_position_size(config, 100, 101)
    with pytest.raises(ValueError):
        RiskConfig(capital=-1)


def test_ai_response_parsing_and_failure_safe_hold():
    valid = ai_engine.parse_ai_response(json.dumps({"decision": "BUY", "confidence": 0.7, "reason": "context"}))
    assert valid["decision"] == "BUY"
    for content in ("", "not json", "[]", '{"decision":"BUY","confidence":4,"reason":"x"}'):
        assert ai_engine.parse_ai_response(content)["decision"] == "HOLD"

    class BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise TimeoutError("timeout")

    assert ai_engine.analyze_market({}, client=BrokenClient())["decision"] == "HOLD"


def test_upstox_auth_error_is_explicit(monkeypatch):
    monkeypatch.setattr(upstox_client, "get_settings", lambda: type("S", (), {"upstox_access_token": "token", "request_timeout_seconds": 1})())

    class Response:
        status_code = 401
        def raise_for_status(self):
            raise AssertionError("401 should be handled before raise_for_status")

    class Session:
        def mount(self, *args): pass
        def get(self, *args, **kwargs): return Response()

    monkeypatch.setattr(upstox_client.requests, "Session", Session)
    with pytest.raises(PermissionError, match="refresh"):
        upstox_client.get_profile()


def test_pipeline_requires_strategy_risk_and_ai_before_paper_entry(monkeypatch):
    trader = PaperTrader(config=RiskConfig(capital=10_000), state=RiskState())
    monkeypatch.setattr("app.paper_trader.log_trade", lambda trade: None)
    calls = []
    engine = TradingEngine(paper_trader=trader, ai_analyzer=lambda data: calls.append(data) or {"decision": "BUY", "confidence": 0.9, "reason": "supportive context"})
    market = {"symbol": "TEST", "close": 100.0, "stop_loss": 99.0}
    assert engine.process_market(market, strategy_signal=False)["decision"] == "HOLD"
    assert calls == []
    invalid_stop = {**market, "stop_loss": 101.0}
    assert engine.process_market(invalid_stop, strategy_signal=True)["decision"] == "REJECTED"
    assert calls == []
    trader.state.daily_loss = 200
    rejected = engine.process_market(market, strategy_signal=True)
    assert rejected["decision"] == "REJECTED"
    assert calls == []
    trader.state.daily_loss = 0
    accepted = engine.process_market(market, strategy_signal=True)
    assert accepted["decision"] == "BUY"
    assert trader.position.quantity == 100
    closed = trader.close_position(101, "test exit")
    assert closed["pnl"] == 100
    assert trader.config.capital == 10_100


def test_backtest_uses_next_bar_entry_and_reports_risk_metrics(monkeypatch):
    from backtester import Backtester

    candles = _candles(40)
    candles[3][1:5] = [100, 101, 99, 100]
    candles[4][1:5] = [100, 101, 99.5, 100]
    candles[5][1:5] = [99.5, 100, 98, 99]
    bt = Backtester(candles)
    monkeypatch.setattr(backtester_module, "calculate_indicators", lambda df: df)

    def mark_one_signal(df, **kwargs):
        df = df.copy()
        df["_signal"] = False
        df.loc[3, "_signal"] = True
        return df

    monkeypatch.setattr(backtester_module, "detect_reversal_candidates", mark_one_signal)
    monkeypatch.setattr(backtester_module, "is_frozen_reversal", lambda row: bool(row["_signal"]))
    report = bt.run_reversal_backtest(RiskConfig(capital=10_000), stop_pct=1, horizon=3)
    assert report["total_trades"] == 1
    trade = report["trades"].iloc[0]
    assert trade.entry_timestamp == bt.df.iloc[4].timestamp
    assert trade.entry_price == 100
    assert trade.exit_reason == "stop_exit"
    assert report["net_pnl"] == pytest.approx(-100)
    assert report["losing_trades"] == 1
