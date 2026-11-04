"""Explicit local NSE CM session intervals; never consults a network calendar."""
from datetime import date, time
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.offline_market_data_v1 import load_session_manifest_v2


class OfflineSessionCalendar:
    def __init__(self, path, evidence_kind="OFFLINE_NSE_REFERENCES"):
        if evidence_kind not in {"OFFLINE_NSE_REFERENCES", "SYNTHETIC_FIXTURE"}:
            raise ValueError("explicit offline calendar evidence kind required")
        path = Path(path)
        if json.loads(path.read_text(encoding="utf-8")).get("schema_version") == 3:
            from app.session_manifest_v3 import load_session_manifest_v3
            self.manifest = load_session_manifest_v3(path)
        else:
            self.manifest = load_session_manifest_v2(path)
        self.identity = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "manifest_version": self.manifest["manifest_version"],
                         "evidence_kind": evidence_kind,
                         "verification": "local references; no independent online re-verification"}
        self.sessions = {s["date"]: s for s in self.manifest["sessions"]}
        self.sources = {s["source_id"]: s for s in self.manifest["sources"]}

    def intervals(self, stamp):
        stamp = pd.Timestamp(stamp)
        if stamp.tzinfo is None:
            raise ValueError("calendar requires an explicit timezone")
        stamp = stamp.tz_convert("Asia/Kolkata")
        day = stamp.date().isoformat()
        entry = self.sessions.get(day)
        if not entry or entry.get("verification_status") != "VERIFIED":
            raise ValueError("offline calendar date absent or unresolved")
        ids = entry.get("source_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("offline calendar session has no evidence")
        for source_id in ids:
            source = self.sources.get(source_id, {})
            bounds = source.get("effective_dates", "").split("/")
            if len(bounds) == 1:
                bounds *= 2
            if (len(bounds) != 2 or not source.get("url")
                    or not date.fromisoformat(bounds[0]) <= stamp.date() <= date.fromisoformat(bounds[1])):
                raise ValueError("offline calendar source does not cover session")
        if entry["session_type"] not in {"REGULAR", "SPECIAL"}:
            return []
        if entry.get("execution_supported") is False:
            return []
        result = []
        for item in entry["normal_market_intervals"]:
            start = pd.Timestamp(f"{day}T{time.fromisoformat(item['start']).isoformat()}", tz="Asia/Kolkata")
            end = pd.Timestamp(f"{day}T{time.fromisoformat(item['end']).isoformat()}", tz="Asia/Kolkata")
            if start.minute % 5 or end.minute % 5 or start.second or end.second:
                raise ValueError("offline session must align with five-minute bars")
            result.append((start, end))
        if any(b[0] < a[1] for a, b in zip(result, result[1:])):
            raise ValueError("offline session intervals overlap or are unordered")
        return result

    def candle_end(self, stamp):
        stamp = pd.Timestamp(stamp)
        for start, end in self.intervals(stamp):
            if start <= stamp and stamp + pd.Timedelta(minutes=5) <= end:
                return end
        raise ValueError("candle falls outside explicit offline session")

    def entry_allowed(self, now):
        now = pd.Timestamp(now)
        expected = now.floor("5min") + pd.Timedelta(minutes=5)
        return any(start <= now < end and expected + pd.Timedelta(minutes=5) <= end
                   for start, end in self.intervals(now))
