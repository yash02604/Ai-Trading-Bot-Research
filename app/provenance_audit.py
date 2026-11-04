from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

IST = ZoneInfo("Asia/Kolkata")
STATUSES = {"VERIFIED_VALID", "VERIFIED_INVALID", "UNRESOLVED", "INVALIDATED"}


def parse_explicit_timestamp(value: Any) -> datetime | None:
    """Parse only explicit-offset timestamps and normalize them to IST.

    Naive timestamps are rejected because their source timezone is not encoded.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(IST)


def classify_ohlc(row: dict[str, Any]) -> tuple[bool, str]:
    """Check structural OHLC ordering; says nothing about feed authenticity."""
    try:
        o, h, low, close = (float(row[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return False, "missing_or_non_numeric_ohlc"
    if not all(map(lambda x: x == x and abs(x) != float("inf"), (o, h, low, close))):
        return False, "non_finite_ohlc"
    if low > min(o, close) or h < max(o, close) or low > h:
        return False, "inconsistent_ohlc_order"
    try:
        volume = float(row["volume"])
    except (KeyError, TypeError, ValueError):
        return False, "missing_or_non_numeric_volume"
    if volume < 0 or volume != volume or abs(volume) == float("inf"):
        return False, "invalid_volume"
    return True, "structurally_valid"


def make_finding(finding_id: str, topic: str, status: str, evidence: list[str],
                 affected_records: Any, proposed_handling: str,
                 limitations: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError("unsupported finding status")
    if not evidence or not proposed_handling or not limitations:
        raise ValueError("evidence, handling, and limitations are required")
    return {
        "finding_id": finding_id,
        "topic": topic,
        "status": status,
        "evidence": evidence,
        "affected_records": affected_records,
        "proposed_handling": proposed_handling,
        "limitations": limitations,
    }


def verify_file_binding(dataset_path, binding_path, *, dataset_relative_path: str,
                        previous_pinned_sha256: str | None = None) -> dict[str, Any]:
    """Pin a dataset's first observed bytes and detect later changes.

    This establishes byte identity only. Existing bindings are never refreshed;
    changing the data produces INVALIDATED while preserving the original hash.
    """
    dataset = Path(dataset_path)
    record_path = Path(binding_path)
    digest = hashlib.sha256()
    size = 0
    with dataset.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    observed = digest.hexdigest()

    if not record_path.exists() and previous_pinned_sha256:
        return {"status": "INVALIDATED", "dataset_path": dataset_relative_path,
                "sha256": previous_pinned_sha256, "observed_sha256": observed,
                "observed_bytes": size,
                "reason": "persistent binding record is missing after a prior audit pin"}

    if record_path.exists():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {"status": "INVALIDATED", "dataset_path": dataset_relative_path,
                    "observed_sha256": observed, "observed_bytes": size,
                    "reason": f"existing binding record is unreadable ({type(exc).__name__})"}
        pinned = record.get("sha256") if isinstance(record, dict) else None
        pinned_path = record.get("dataset_path") if isinstance(record, dict) else None
        if pinned_path != dataset_relative_path or pinned != observed:
            return {"status": "INVALIDATED", "dataset_path": dataset_relative_path,
                    "sha256": pinned, "observed_sha256": observed,
                    "bytes": record.get("bytes") if isinstance(record, dict) else None,
                    "observed_bytes": size,
                    "reason": "dataset path or bytes differ from the original binding; original hash retained"}
        return {**record, "status": "VERIFIED", "observed_sha256": observed,
                "observed_bytes": size,
                "scope": "byte identity only; does not authenticate source or correctness"}

    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "dataset-byte-binding-v1",
        "dataset_path": dataset_relative_path,
        "sha256": observed,
        "bytes": size,
        "binding_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "VERIFIED",
        "scope": "byte identity only; does not authenticate source or correctness",
    }
    record_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    return {**record, "observed_sha256": observed, "observed_bytes": size}
