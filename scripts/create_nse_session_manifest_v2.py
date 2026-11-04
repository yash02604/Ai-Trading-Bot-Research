from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.session_manifest_v2 import build_session_manifest_v2

source = ROOT / "data/audits/nse_cm_session_manifest_v1.json"
target = ROOT / "data/audits/nse_cm_session_manifest_v2.json"
manifest = build_session_manifest_v2(source, target)
print(f"Created {target.relative_to(ROOT)} with {len(manifest['sessions'])} session entries")