"""Environment-backed configuration for the trading assistant."""

import os
import math
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    upstox_access_token: str | None
    openrouter_api_key: str | None
    openrouter_model: str
    request_timeout_seconds: float
    paper_trading: bool
    paper_initial_capital: float = 100_000.0

    @classmethod
    def from_env(cls):
        timeout = float(os.getenv("API_TIMEOUT_SECONDS", "10"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("API_TIMEOUT_SECONDS must be positive")
        return cls(
            upstox_access_token=os.getenv("UPSTOX_ACCESS_TOKEN") or None,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL", "openrouter/free"),
            request_timeout_seconds=timeout,
            paper_trading=os.getenv("PAPER_TRADING", "").strip().lower()
            in {"1", "true", "yes", "on"},
            paper_initial_capital=float(os.getenv("PAPER_INITIAL_CAPITAL", "100000")),
        )


def get_settings():
    return Settings.from_env()
