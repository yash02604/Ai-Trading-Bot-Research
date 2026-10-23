"""Fail-closed timing checks for research-only historical candle execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlparse

import numpy as np
import pandas as pd

BAR = pd.Timedelta(minutes=5)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExecutionIntegrityError(ValueError):
    """An execution cannot be justified by the supplied data/timing evidence."""


@dataclass(frozen=True)
class TimestampSemanticsEvidence:
    """Reviewed evidence binding START-label semantics to exact normalized files."""

    label_semantics: str
    verification_status: str
    provider_documentation_url: str
    acquisition_manifest_sha256: str
    acquisition_request_ids: tuple[str, ...]
    dataset_sha256: Mapping[str, str]
    evidence_file_sha256: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict) -> "TimestampSemanticsEvidence":
        required = {
            "schema_version", "label_semantics", "verification_status",
            "provider_documentation_url", "acquisition_manifest_sha256",
            "acquisition_request_ids", "dataset_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            raise ExecutionIntegrityError("timestamp evidence has an unexpected schema")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ExecutionIntegrityError("unsupported timestamp evidence version")
        ids = raw["acquisition_request_ids"]
        hashes = raw["dataset_sha256"]
        if not isinstance(ids, list) or not ids or not all(isinstance(value, str) and value.strip() for value in ids):
            raise ExecutionIntegrityError("timestamp evidence requires acquisition request IDs")
        if not isinstance(hashes, dict) or not all(
            isinstance(name, str) and isinstance(value, str) and SHA256_RE.fullmatch(value)
            for name, value in hashes.items()
        ):
            raise ExecutionIntegrityError("timestamp evidence dataset hashes are invalid")
        if not isinstance(raw["label_semantics"], str) or not isinstance(raw["verification_status"], str):
            raise ExecutionIntegrityError("timestamp evidence classifications must be strings")
        if not isinstance(raw["provider_documentation_url"], str):
            raise ExecutionIntegrityError("provider documentation URL must be a string")
        if not isinstance(raw["acquisition_manifest_sha256"], str):
            raise ExecutionIntegrityError("acquisition manifest hash must be a string")
        return cls(
            label_semantics=raw["label_semantics"],
            verification_status=raw["verification_status"],
            provider_documentation_url=raw["provider_documentation_url"],
            acquisition_manifest_sha256=raw["acquisition_manifest_sha256"],
            acquisition_request_ids=tuple(ids),
            dataset_sha256=dict(hashes),
        )

    @classmethod
    def from_file(cls, path: Path) -> "TimestampSemanticsEvidence":
        try:
            content = path.read_bytes()
            raw = json.loads(content)
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutionIntegrityError("timestamp evidence file is unavailable or malformed") from exc
        evidence = cls.from_mapping(raw)
        return replace(evidence, evidence_file_sha256=hashlib.sha256(content).hexdigest())

    def require_verified_start_labels(self, expected_dataset_hashes: Mapping[str, str] | None = None) -> None:
        if self.verification_status != "VERIFIED":
            raise ExecutionIntegrityError("timestamp label semantics are not verified")
        if self.label_semantics != "START":
            raise ExecutionIntegrityError("historical execution supports only verified START labels")
        parsed_url = urlparse(self.provider_documentation_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ExecutionIntegrityError("authoritative provider documentation reference is required")
        if not isinstance(self.acquisition_manifest_sha256, str) or not SHA256_RE.fullmatch(self.acquisition_manifest_sha256):
            raise ExecutionIntegrityError("acquisition manifest SHA-256 is required")
        if not self.acquisition_request_ids:
            raise ExecutionIntegrityError("acquisition request lineage is required")
        if self.evidence_file_sha256 is not None and not SHA256_RE.fullmatch(self.evidence_file_sha256):
            raise ExecutionIntegrityError("timestamp evidence file SHA-256 is invalid")
        if expected_dataset_hashes is not None and dict(self.dataset_sha256) != dict(expected_dataset_hashes):
            raise ExecutionIntegrityError("timestamp evidence does not bind the exact input dataset hashes")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_simulation_event(event: dict, evidence: TimestampSemanticsEvidence) -> None:
    """Check causal next-bar timing and same-session contiguous holding labels."""
    evidence.require_verified_start_labels()
    try:
        frame = event["frame"]
        signal_index = event["signal_index"]
        entry_index = event["entry_index"]
        last_index = event["last_index"]
        if any(type(value) is not int for value in (signal_index, entry_index, last_index)):
            raise ExecutionIntegrityError("signal, entry, and exit indexes must be integers")
        if signal_index < 0 or entry_index != signal_index + 1 or last_index < entry_index or last_index >= len(frame):
            raise ExecutionIntegrityError("event does not use an available next candle and valid exit range")
        labels = frame["timestamp"]
        signal_label = pd.Timestamp(labels.iloc[signal_index])
        entry_label = pd.Timestamp(labels.iloc[entry_index])
        if signal_label.tzinfo is None or entry_label.tzinfo is None or any(
            stamp.utcoffset() != pd.Timedelta(hours=5, minutes=30) for stamp in (signal_label, entry_label)
        ):
            raise ExecutionIntegrityError("execution timestamps must be normalized to Asia/Kolkata")
        if event.get("signal_timestamp") != signal_label or event.get("entry_timestamp") != entry_label:
            raise ExecutionIntegrityError("event timestamps do not match normalized candle rows")
        if entry_label - signal_label != BAR or entry_label.date() != signal_label.date():
            raise ExecutionIntegrityError("entry must be the contiguous next bar in the same session")
        session_start = pd.Timestamp("2000-01-01T09:15:00").time()
        session_last_start = pd.Timestamp("2000-01-01T15:25:00").time()
        if signal_label.time() < session_start or signal_label.time() > session_last_start:
            raise ExecutionIntegrityError("signal bar label is outside the supported regular session")
        holding = [pd.Timestamp(value) for value in labels.iloc[entry_index:last_index + 1]]
        if not holding or any(
            stamp.tzinfo is None or stamp.utcoffset() != pd.Timedelta(hours=5, minutes=30)
            or stamp.date() != signal_label.date() for stamp in holding
        ):
            raise ExecutionIntegrityError("holding bars are timezone-invalid or cross a session boundary")
        if any(right - left != BAR for left, right in zip(holding, holding[1:])):
            raise ExecutionIntegrityError("holding bars contain a gap")
        if any(stamp.time() < session_start or stamp.time() > session_last_start for stamp in holding):
            raise ExecutionIntegrityError("holding bar label is outside the supported regular session")
        bars = frame.iloc[entry_index:last_index + 1]
        prices = bars[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        if prices.isna().any().any() or not np.isfinite(prices.to_numpy(dtype=float)).all() or (prices <= 0).any().any():
            raise ExecutionIntegrityError("execution OHLC values must be finite and positive")
        if (prices["low"] > prices[["open", "close"]].min(axis=1)).any() or (
            prices["high"] < prices[["open", "close"]].max(axis=1)
        ).any() or (prices["low"] > prices["high"]).any():
            raise ExecutionIntegrityError("execution OHLC relationship is invalid")
    except ExecutionIntegrityError:
        raise
    except Exception as exc:
        raise ExecutionIntegrityError("simulation event is incomplete or malformed") from exc
