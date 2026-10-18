class MockAI:
    """
    Deterministic AI replacement for simulation/backtesting.

    This does NOT predict the market.
    It only provides controlled decisions so that
    the trading engine can be tested repeatedly.
    """

    def __init__(self, decisions=None):
        self.decisions = decisions or []
        self.index = 0

    def analyze(self, market_data):
        if self.index >= len(self.decisions):
            decision = "HOLD"
        else:
            decision = self.decisions[self.index]

        self.index += 1

        return {
            "decision": decision,
            "confidence": 1.0,
            "reason": "Deterministic simulation decision",
        }
