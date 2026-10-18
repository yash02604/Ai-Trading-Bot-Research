"""Create isolated, reproducible NSE CM timestamp/coverage audit outputs."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from app.nse_session_audit import load_manifest, validate_frame

MANIFEST_PATH = ROOT / "data/audits/nse_cm_session_manifest_v1.json"
OUT = ROOT / "backtests/data_validation_v1"
INPUTS = [
    ROOT / "data/research/extended_historical_robustness_candles.csv",
    ROOT / "data/research/extended_historical_robustness_oos_candles.csv",
]


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(MANIFEST_PATH)
    hashes = []
    summary = []
    for path in INPUTS:
        frame = pd.read_csv(path)
        for convention in ("start", "end"):
            result = validate_frame(frame, manifest, instrument="stock", timestamp_convention=convention)
            stem = path.stem + "_" + convention
            result.rows.to_csv(OUT / f"{stem}_rows.csv.gz", index=False, compression="gzip")
            result.coverage.to_csv(OUT / f"{stem}_coverage.csv.gz", index=False, compression="gzip")
            summary.append({
                "input": str(path.relative_to(ROOT)), "timestamp_convention": convention,
                "rows": len(result.rows), "classifications": result.rows.classification.value_counts().to_dict(),
                "coverage_days": len(result.coverage),
                "coverage_invalid_days": int(result.coverage.classification.eq("VERIFIED_INVALID").sum()) if len(result.coverage) else 0,
            })
        hashes.append({"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "bytes": path.stat().st_size})
    (OUT / "input_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps({
        "manifest_sha256": sha256(MANIFEST_PATH), "generated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "classification_scope": "NSE CM timestamp/session/coverage only; does not authenticate feed values",
        "results": summary,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
