import unittest
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
from app.provenance_audit import (
    parse_explicit_timestamp, classify_ohlc, make_finding, verify_file_binding,
)

TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest-tmp-provenance-fixtures"

class ProvenanceAuditTests(unittest.TestCase):
    def test_explicit_utc_timestamp_converts_to_ist(self):
        result = parse_explicit_timestamp("2023-12-01T03:45:00Z")
        self.assertEqual(result.isoformat(), "2023-12-01T09:15:00+05:30")

    def test_naive_or_invalid_timestamp_is_rejected(self):
        self.assertIsNone(parse_explicit_timestamp("2023-12-01 09:15:00"))
        self.assertIsNone(parse_explicit_timestamp("not-a-time"))

    def test_ohlc_structural_classification(self):
        good = {"open": 10, "high": 12, "low": 9, "close": 11, "volume": 0}
        self.assertEqual(classify_ohlc(good), (True, "structurally_valid"))
        bad = dict(good, high=8)
        self.assertEqual(classify_ohlc(bad), (False, "inconsistent_ohlc_order"))

    def test_finding_requires_allowed_status_and_evidence(self):
        finding = make_finding("x", "topic", "UNRESOLVED", ["evidence"], [], "exclude", "limits")
        self.assertEqual(finding["status"], "UNRESOLVED")
        with self.assertRaises(ValueError):
            make_finding("x", "topic", "MAYBE", ["evidence"], [], "exclude", "limits")
        with self.assertRaises(ValueError):
            make_finding("x", "topic", "UNRESOLVED", [], [], "exclude", "limits")

    def test_file_binding_pins_sha256_and_detects_post_verification_change(self):
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            root = Path(temp)
            dataset = root / "candles.csv"
            binding = root / "audit" / "dataset_binding.json"
            original = b"timestamp,close\\n2025-01-01T09:15:00+05:30,100\\n"
            dataset.write_bytes(original)

            verified = verify_file_binding(dataset, binding,
                                           dataset_relative_path="data/candles.csv")
            self.assertEqual(verified["status"], "VERIFIED")
            self.assertEqual(verified["sha256"],
                             __import__("hashlib").sha256(original).hexdigest())

            changed = original + b"2025-01-01T09:20:00+05:30,101\\n"
            dataset.write_bytes(changed)
            invalidated = verify_file_binding(dataset, binding,
                                              dataset_relative_path="data/candles.csv")
            self.assertEqual(invalidated["status"], "INVALIDATED")
            self.assertEqual(invalidated["sha256"], verified["sha256"])
            self.assertNotEqual(invalidated["observed_sha256"], verified["sha256"])
            self.assertEqual(json.loads(binding.read_text())["sha256"], verified["sha256"])

    def test_missing_binding_after_prior_audit_pin_is_not_rebound(self):
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            root = Path(temp)
            dataset = root / "candles.csv"
            binding = root / "binding.json"
            dataset.write_bytes(b"original")
            first = verify_file_binding(dataset, binding,
                                       dataset_relative_path="data/candles.csv")
            binding.unlink()
            dataset.write_bytes(b"replacement")
            missing = verify_file_binding(
                dataset, binding, dataset_relative_path="data/candles.csv",
                previous_pinned_sha256=first["sha256"])
            self.assertEqual(missing["status"], "INVALIDATED")
            self.assertEqual(missing["sha256"], hashlib.sha256(b"original").hexdigest())
            self.assertFalse(binding.exists())

    def test_file_binding_does_not_rebind_to_a_different_path(self):
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=TEST_TMP_ROOT) as temp:
            root = Path(temp)
            dataset = root / "candles.csv"
            binding = root / "binding.json"
            dataset.write_bytes(b"immutable bytes")
            first = verify_file_binding(dataset, binding,
                                       dataset_relative_path="data/candles.csv")
            changed_path = verify_file_binding(dataset, binding,
                                               dataset_relative_path="data/renamed.csv")
            self.assertEqual(first["status"], "VERIFIED")
            self.assertEqual(changed_path["status"], "INVALIDATED")
            self.assertEqual(changed_path["sha256"], first["sha256"])

if __name__ == "__main__":
    unittest.main()
