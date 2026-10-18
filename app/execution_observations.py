"""Read-only parsing helpers for Upstox quote observations."""

from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
PROVIDER_TIMESTAMP_KIND = "response_generated"


def _aware(value):
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError("timestamp must be a datetime or ISO-8601 string")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return stamp.astimezone(IST)


def _best_level(levels):
    if levels is None:
        return None, None
    if not isinstance(levels, list):
        raise TypeError("depth side must be a list")
    for level in levels:
        if not isinstance(level, dict):
            raise TypeError("depth level must be an object")
        price = float(level.get("price", 0))
        quantity = int(level.get("quantity", 0))
        if math.isfinite(price) and price > 0 and quantity > 0:
            return price, quantity
    return None, None


def parse_upstox_full_quote(response, received_at, max_age_seconds=5.0):
    """Parse V3 depth without treating local receipt time as a source timestamp.

    Upstox V3's ``timestamp`` is documented as response-generated. The endpoint
    does not expose an independent timestamp for the order-book levels, so the
    returned source_quote_timestamp remains None.
    """
    received = _aware(received_at)
    result = {
        "price": None,
        "quantity": None,
        "observed_at": received,
        "snapshot_at": None,
        "provider_timestamp": None,
        "provider_timestamp_kind": PROVIDER_TIMESTAMP_KIND,
        "source_quote_timestamp": None,
        "source_timestamp_status": "not_exposed_by_quote_endpoint",
        "bid_price": None,
        "bid_quantity": None,
        "ask_price": None,
        "ask_quantity": None,
        "spread_price": None,
        "spread_bps": None,
        "spread_available": False,
        "spread_reason": "unavailable",
        "quote_age_ms": None,
        "quote_valid": False,
        "quote_reason": "malformed_response",
    }
    try:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict) or len(data) != 1:
            result["quote_reason"] = "missing_or_ambiguous_quote_data"
            return result
        quote = next(iter(data.values()))
        if not isinstance(quote, dict):
            result["quote_reason"] = "malformed_quote_object"
            return result

        raw_timestamp = quote.get("timestamp")
        if raw_timestamp is None:
            timestamp_error = "missing_provider_timestamp"
        else:
            try:
                provider_timestamp = _aware(raw_timestamp)
                result["provider_timestamp"] = provider_timestamp
                result["snapshot_at"] = provider_timestamp
                result["quote_age_ms"] = (received - provider_timestamp).total_seconds() * 1000
                if provider_timestamp > received:
                    timestamp_error = "future_provider_timestamp"
                elif result["quote_age_ms"] > max_age_seconds * 1000:
                    timestamp_error = "stale_provider_timestamp"
                else:
                    timestamp_error = None
            except (TypeError, ValueError, OverflowError):
                timestamp_error = "malformed_provider_timestamp"

        depth = quote.get("depth")
        if not isinstance(depth, dict):
            result["quote_reason"] = "malformed_depth"
            result["spread_reason"] = "malformed_depth"
            return result
        try:
            result["bid_price"], result["bid_quantity"] = _best_level(depth.get("buy"))
            result["ask_price"], result["ask_quantity"] = _best_level(depth.get("sell"))
        except (TypeError, ValueError, OverflowError):
            result["quote_reason"] = "malformed_depth_level"
            result["spread_reason"] = "malformed_depth_level"
            return result

        if result["ask_price"] is not None:
            result["price"] = result["ask_price"]
            result["quantity"] = result["ask_quantity"]
        if result["bid_price"] is not None and result["ask_price"] is not None:
            if result["ask_price"] < result["bid_price"]:
                result["spread_reason"] = "crossed_market"
            else:
                spread = result["ask_price"] - result["bid_price"]
                midpoint = (result["ask_price"] + result["bid_price"]) / 2
                result["spread_price"] = spread
                result["spread_bps"] = spread / midpoint * 10_000 if midpoint > 0 else None
                result["spread_available"] = result["spread_bps"] is not None
                result["spread_reason"] = "available" if result["spread_available"] else "invalid_midpoint"
        elif result["bid_price"] is None:
            result["spread_reason"] = "missing_bid_depth"
        else:
            result["spread_reason"] = "missing_ask_depth"

        if timestamp_error:
            result["quote_reason"] = timestamp_error
        elif result["ask_price"] is None:
            result["quote_reason"] = "missing_ask_depth"
        else:
            result["quote_valid"] = True
            result["quote_reason"] = "valid"
        return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return result
