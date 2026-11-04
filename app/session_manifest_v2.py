"""Explicit, reviewable v1-to-v2 NSE calendar correction. No network access."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from app.offline_market_data_v1 import sha256_file, validate_session_manifest

EXPECTED_V1_SHA256 = "2ac081ab4ad4e07891ae10727c2d9247f3ed3f55b329274a933aade491d56e16"
SOURCE_ID = "NSE_2024_NOV20"
SOURCE = {
    "source_id": SOURCE_ID,
    "source": "NSE/CMTR/64960",
    "effective_dates": "2024-11-20/2024-11-20",
    "url": "https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf",
    "retrieved": "2026-10-19",
    "supports": "NSE Capital Market trading holiday on 2024-11-20; date-specific amendment to NSE/CMTR/59722.",
    "document_id": "NSE/CMTR/64960",
    "issue_date": "2024-11-08",
    "effective_date": "2024-11-20",
    "segment": "CM",
    "verification_status": "VERIFIED",
    "verification_metadata": {
        "verification_method": "Official NSE archive circular reviewed; circular text identifies CM segment and effective trading holiday.",
        "verified_at": "2026-10-19",
        "primary_source": True,
        "source_content_sha256": None,
        "source_content_sha256_note": "Source PDF was not downloaded or hashed in this offline task.",
    },
}


def build_session_manifest_v2(source_path: str | Path, output_path: str | Path) -> dict:
    source_path, output_path = Path(source_path), Path(output_path)
    if sha256_file(source_path) != EXPECTED_V1_SHA256:
        raise ValueError("v1 manifest hash differs from the reviewed baseline; refuse to build")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {output_path}")
    manifest = deepcopy(json.loads(source_path.read_text(encoding="utf-8")))
    manifest["schema_version"] = 2
    manifest["manifest_version"] = "nse-cm-2023-12-01_2026-10-01-v2"
    if any(s.get("source_id") == SOURCE_ID for s in manifest["sources"]):
        raise ValueError("correction source already exists in source registry")
    manifest["sources"].append(deepcopy(SOURCE))
    matches = [entry for entry in manifest["sessions"] if entry.get("date") == "2024-11-20"]
    if len(matches) != 1:
        raise ValueError("expected exactly one 2024-11-20 session")
    day = matches[0]
    day["session_type"] = "HOLIDAY"
    day["normal_market_intervals"] = []
    day["special_session"] = False
    day["verification_status"] = "VERIFIED"
    day["source_ids"] = ["NSE_2024", SOURCE_ID]
    day["note"] = "Trading holiday per NSE/CMTR/64960 dated 2024-11-08, amending NSE/CMTR/59722."
    validate_session_manifest(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest