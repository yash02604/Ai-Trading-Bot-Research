"""Evidence-bound CM calendar extension; old calendars and evidence stay immutable."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = "data/evidence/nse_calendar_2026_20261030"
REGISTRY_SHA256 = "7b5f72e6d804c4900d52cabc8a6822669cd0f5e04d6d18a2cf176fbae1076b59"
BASE_SHA256 = "5012edfdee4a6db80a531b9c77486448dd305aa641920a6696d9f63a9ea55cbc"
MANIFEST_SHA256 = "4f0524db0b16c2dfedf354704cac7954b14af3936cbbff76f53d1848b4938d03"
VERSION = "nse-cm-2023-12-01_2026-12-31-v3-asof-20261030"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_calendar_evidence(root=ROOT):
    root = Path(root)
    directory = root / EVIDENCE
    encoded = (directory / "evidence_registry.json").read_bytes()
    if _sha(encoded) != REGISTRY_SHA256:
        raise ValueError("NSE evidence registry hash mismatch")
    registry = json.loads(encoded)
    reviewed = datetime.fromisoformat(registry["reviewed_at_utc"])
    if reviewed.tzinfo is None or reviewed.utcoffset() != timedelta(0):
        raise ValueError("NSE evidence review timestamp must be UTC")
    for source in registry["documents"]:
        if (urlsplit(source["official_url"]).scheme != "https"
                or urlsplit(source["official_url"]).hostname not in
                {"www.nseindia.com", "nsearchives.nseindia.com"}
                or _sha((directory / source["file"]).read_bytes()) != source["sha256"]
                or _sha((directory / source["retrieval_record"]).read_bytes()) != source["retrieval_record_sha256"]):
            raise ValueError("official NSE retained source/hash mismatch")
        stamp = datetime.fromisoformat(source["retrieved_at_utc"])
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError("NSE retrieval timestamp must be UTC")
        if reviewed < stamp or (source["publication_date"] is not None
                                and date.fromisoformat(source["publication_date"]) > stamp.date()):
            raise ValueError("NSE evidence publication/retrieval/review chronology is invalid")
        if source.get("archive_file"):
            with zipfile.ZipFile(directory / source["archive_file"]) as archive:
                if archive.read(source["archive_member"]) != (directory / source["file"]).read_bytes():
                    raise ValueError("retained NSE archive member identity mismatch")
    return registry


def _expected(root=ROOT):
    root = Path(root)
    registry = verify_calendar_evidence(root)
    base_path = root / "data/audits/nse_cm_session_manifest_v2.json"
    raw = base_path.read_bytes()
    if _sha(raw) != BASE_SHA256:
        raise ValueError("historical v2 calendar changed")
    manifest = deepcopy(json.loads(raw))
    manifest.update(schema_version=3, manifest_version=VERSION,
                    coverage={"start": "2023-12-01", "end": "2026-12-31"},
                    derived_from={"path": "data/audits/nse_cm_session_manifest_v2.json", "sha256": BASE_SHA256},
                    evidence_registry={"path": EVIDENCE + "/evidence_registry.json", "sha256": REGISTRY_SHA256},
                    amendment_review=registry["amendment_review"])
    manifest["timestamp_label_convention"] = "START required by offline replay; provider-specific historical labels remain unverified"
    source_ids = {}
    for source in registry["documents"]:
        source_id = "RETAINED_" + source["file"].replace(".", "_")
        source_ids[source["file"]] = source_id
        manifest["sources"].append({"source_id": source_id, "url": source["official_url"],
                                     "effective_dates": source["applicable_dates"],
                                     "source_content_sha256": source["sha256"],
                                     "retained_path": EVIDENCE + "/" + source["file"],
                                     "publication_date": source["publication_date"],
                                     "retrieved_at_utc": source["retrieved_at_utc"],
                                     "circular_number": source["circular_number"],
                                     "relationship": source["amendment_relationship"]})
    holiday_data = json.loads((root / EVIDENCE / "nse_holiday_master_trading.json").read_bytes())["CM"]
    holidays = {datetime.strptime(row["tradingDate"], "%d-%b-%Y").date(): row["description"]
                for row in holiday_data}
    # Explicit annual circular plus the retained January amendment corroborate
    # the dynamic CM list. Other market-segment lists never determine dates.
    expected_holidays = {date.fromisoformat(day) for day in (
        "2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21",
        "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01",
        "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14", "2026-10-02",
        "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25")}
    if set(holidays) != expected_holidays:
        raise ValueError("retained CM holiday list disagrees with reviewed circulars")
    cas_source = source_ids["CMTR74466-member-0.pdf"]
    december_source = source_ids["CMTR76963-member-1.pdf"]
    manifest["execution_phase_policy"] = {
        "scope": "common continuous session across CAS and non-CAS securities; auctions never simulated",
        "2026-08-03/2026-12-06": "09:15/15:15",
        "2026-12-07/2026-12-31": "09:15/15:30",
        "special_sessions": "explicit date schedule plus execution eligibility required",
        "no_2027_extrapolation": True}
    manifest["explicit_v2_corrections"] = {
        "date_range": "2026-08-03/2026-10-01",
        "treatment": "regular-session execution cutoff conservatively shortened for unclassified CAS eligibility",
        "basis": cas_source, "original_v2_bytes_preserved": True}
    for entry in manifest["sessions"]:
        if entry["session_type"] == "REGULAR" and entry["date"] >= "2026-08-03":
            entry["prior_v2_normal_market_intervals"] = deepcopy(entry["normal_market_intervals"])
            entry["normal_market_intervals"] = [{"start": "09:15", "end": "15:15"}]
            entry["source_ids"].append(cas_source)
            entry["v3_correction"] = "common CTS cutoff; non-CAS later interval not enabled without classification"
    common_sources = [source_ids[n] for n in ("CMTR71775.pdf", "nse_holiday_master_trading.json", "nse_market_timings.html")]
    day = date(2026, 10, 2)
    while day <= date(2026, 12, 31):
        holiday = day in holidays
        session_type = "HOLIDAY" if holiday else "WEEKEND_CLOSED" if day.weekday() >= 5 else "REGULAR"
        entry = {"date": day.isoformat(), "segment": "CM", "timezone": "Asia/Kolkata",
                 "session_type": session_type, "special_session": False,
                 "normal_market_intervals": [{"start": "09:15", "end": "15:30"}] if session_type == "REGULAR" else [],
                 "verification_status": "VERIFIED", "source_ids": list(common_sources),
                 "note": holidays.get(day, "Regular CM schedule or closed weekend under retained NSE evidence"),
                 "execution_phase": "NORMAL_CONTINUOUS_ONLY"}
        if session_type == "REGULAR":
            end = "15:15" if day < date(2026, 12, 7) else "15:30"
            entry["normal_market_intervals"] = [{"start": "09:15", "end": end}]
            entry["source_ids"].append(cas_source if day < date(2026, 12, 7) else december_source)
            entry["phase_policy"] = "conservative common CTS; CAS and other auctions excluded"
        if day == date(2026, 11, 8):
            entry.update(session_type="SPECIAL", special_session=True,
                         normal_market_intervals=[{"start": "14:20", "end": "15:15"}],
                         source_ids=[source_ids[n] for n in ("CMTR71775.pdf", "CMTR76941.pdf", "ISC77098.pdf")],
                         execution_supported=False,
                         execution_block_reason="security-class/UCC evidence absent; special phases unsupported; 35-bar requirement retained",
                         security_specific_continuous_intervals={
                             "CAS": {"start": "14:20", "end": "15:15"},
                             "NON_CAS": {"start": "14:20", "end": "15:30"},
                             "IPO_RELISTED_SPECIAL_PREOPEN": {"start": "15:05", "end": "15:30"}},
                         excluded_phases=[
                             {"phase": "BLOCK_DEAL", "start": "13:50", "end": "14:05"},
                             {"phase": "PREOPEN", "start": "14:05", "end": "14:20"},
                             {"phase": "SPECIAL_PREOPEN", "start": "14:05", "end": "14:50"},
                             {"phase": "ILLIQUID_CALL_AUCTION", "start": "14:25", "end": "15:10"},
                             {"phase": "CAS_TRANSITION", "start": "15:15", "end": "15:20"},
                             {"phase": "CAS_ORDER_ENTRY", "start": "15:20", "end": "15:30"},
                             {"phase": "CAS_MATCHING", "start": "15:30", "end": "15:35"},
                             {"phase": "CAS_POST_TRANSITION", "start": "15:35", "end": "15:50"},
                             {"phase": "POST_CLOSE", "start": "15:50", "end": "16:00"}],
                         ucc_compliance_cutoff="2026-11-07T22:00:00+05:30",
                         note="Published Muhurat schedule retained separately from execution permission")
        manifest["sessions"].append(entry)
        day += timedelta(days=1)
    return manifest


def build_session_manifest_v3(output_path, root=ROOT):
    manifest = _expected(root)
    with Path(output_path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return manifest


def load_session_manifest_v3(path, root=ROOT):
    raw = Path(path).read_bytes()
    if _sha(raw) != MANIFEST_SHA256:
        raise ValueError("v3 NSE calendar hash mismatch")
    manifest = json.loads(raw)
    if manifest != _expected(root):
        raise ValueError("v3 NSE calendar/source derivation mismatch")
    return manifest
