from datetime import datetime, timedelta

from app.mock_ai import MockAI
from app.paper_trader import PaperTrader


def build_test_market_data(price, previous_price, timestamp):
    return {
        "symbol": "SIM",
        "timestamp": timestamp,
        "close": price,
        "ema_9": price - 0.5,
        "ema_20": price - 1.5,
        "rsi_14": 45.0,
        "macd": 0.5,
        "macd_signal": 0.8,
        "macd_histogram": -0.3,
        "volume": 120000,
        "volume_ma_20": 100000,
        "open": previous_price,
        "high": price + 1.0,
        "low": price - 1.0,
        "prev_close": previous_price,
        "stop_loss": price - 5.0,
    }


def run_stop_loss_simulation():
    paper_trader = PaperTrader()

    mock_ai = MockAI(
        decisions=[
            "BUY",
            "HOLD",
            "HOLD",
            "HOLD",
        ]
    )

    start_time = datetime.now()

    # BUY at 100.
    # Initial stop-loss will be 95.
    # Price then falls to 94, triggering the stop-loss.
    prices = [100, 99, 97, 94]

    print("===== STOP-LOSS SIMULATION =====")
    print("Candles:", len(prices))
    print()

    previous_price = prices[0]

    for index, price in enumerate(prices):
        timestamp = start_time + timedelta(minutes=5 * index)

        market_data = build_test_market_data(
            price=price,
            previous_price=previous_price,
            timestamp=timestamp,
        )

        ai_result = mock_ai.analyze(market_data)

        print(f"===== CANDLE {index + 1} =====")
        print("Time:", timestamp)
        print("Price:", price)
        print("Mock AI decision:", ai_result["decision"])

        # Stop-loss always has priority over AI decisions.
        stop_result = paper_trader.check_stop_loss(
            current_price=price,
            context={
                "ai_decision": ai_result["decision"],
                "ai_confidence": ai_result["confidence"],
                "ai_reason": ai_result["reason"],
                "ema_9": market_data["ema_9"],
                "ema_20": market_data["ema_20"],
                "rsi_14": market_data["rsi_14"],
                "macd": market_data["macd"],
                "macd_signal": market_data["macd_signal"],
                "macd_histogram": market_data["macd_histogram"],
                "volume": market_data["volume"],
                "volume_ma_20": market_data["volume_ma_20"],
            },
        )

        if stop_result is not None:
            print()
            break

        if ai_result["decision"] == "BUY":
            paper_trader.open_position(
                symbol=market_data["symbol"],
                entry_price=price,
                stop_loss=market_data["stop_loss"],
            )

        else:
            print("No trade.")

        print()
        previous_price = price

    print("===== STOP-LOSS TEST COMPLETE =====")
    print("Trades recorded:", len(paper_trader.trades))
    print("Daily loss:", paper_trader.state.daily_loss)
    print("Trades today:", paper_trader.state.trades_today)
    print("Open position:", paper_trader.position)

    if paper_trader.trades:
        trade = paper_trader.trades[-1]
        print("Recorded P&L:", trade["pnl"])
        print("Exit reason:", trade["reason"])


if __name__ == "__main__":
    run_stop_loss_simulation()
