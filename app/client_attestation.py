"""Verify distinct historical and reviewed-current client byte identities offline.

An attestation is evidence of a scoped source review, never trading permission or
proof that this client produced historical candle datasets.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_ATTESTATION = "data/attestations/upstox_client_v1/attestation.json"
V2_ATTESTATION = "data/attestations/upstox_client_v2/attestation.json"
ATTESTATION = "data/attestations/upstox_client_v3/attestation.json"
HISTORICAL_ATTESTATION_SHA256 = "7fca59d485bcf44fd4eddfef00f52062ed2f4ea9f980c7653f1361fdde9556ef"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _verify_v1(root=ROOT):
    root = Path(root)
    path = root / HISTORICAL_ATTESTATION
    data = path.read_bytes()
    if _sha(data) != HISTORICAL_ATTESTATION_SHA256:
        raise ValueError("current-client attestation hash mismatch; review required")
    record = json.loads(data)
    if record["schema_version"] != "upstox-client-source-attestation-v1":
        raise ValueError("unsupported client attestation")
    audit = record["historical_audit"]
    if _sha((root / audit["path"]).read_bytes()) != audit["sha256"]:
        raise ValueError("historical audit was changed")
    old, current = record["historical_source"], record["current_source"]
    historical = (path.parent / old["snapshot"]).read_bytes()
    saved_current = (path.parent / current["snapshot"]).read_bytes()
    working = saved_current  # v1 binds its preserved reviewed source, never new bytes.
    if _sha(historical) != old["sha256"]:
        raise ValueError("historical client snapshot hash mismatch")
    lf_blob = historical.replace(b"\r\n", b"\n")
    blob_oid = hashlib.sha1(b"blob " + str(len(lf_blob)).encode() + b"\0" + lf_blob).hexdigest()
    if _sha(lf_blob) != old["git_blob_lf_sha256"] or blob_oid != old["git_blob_oid"]:
        raise ValueError("historical Git blob identity mismatch")
    if (working != saved_current or _sha(working) != current["sha256"]
            or len(working) != current["bytes"]
            or working.count(b"\r\n") != current["crlf_count"]
            or b"\n" in working.replace(b"\r\n", b"")
            or _sha(working.replace(b"\r\n", b"\n")) != current["lf_normalized_sha256"]):
        raise ValueError("current client differs from reviewed source bytes; re-attestation required")
    patch = record["reviewed_diff"]
    if _sha((path.parent / patch["path"]).read_bytes()) != patch["sha256"]:
        raise ValueError("reviewed client diff hash mismatch")
    review = record["review"]
    if (not review["reviewer"] or not review["findings"] or not review["requirements"]
            or review["source_authentication"] is not False
            or review["independent_review_approval"] is not False
            or review["operational_trading_approval"] is not False):
        raise ValueError("client review scope or approval claims invalid")
    # Independently recheck the specific unchanged behavior claimed by the review.
    previous, present = ast.parse(historical), ast.parse(working)
    previous_functions = {n.name: ast.dump(n, include_attributes=False)
                          for n in previous.body if isinstance(n, ast.FunctionDef)}
    present_functions = {n.name: ast.dump(n, include_attributes=False)
                         for n in present.body if isinstance(n, ast.FunctionDef)}
    endpoints = {"get_profile", "get_quote", "get_intraday_candles", "get_historical_candles"}
    if set(previous_functions) != set(present_functions) or any(
            previous_functions[n] != present_functions[n] for n in endpoints):
        raise ValueError("client endpoint surface changed")
    for name in ("Retry", "get"):
        def calls(tree):
            return [ast.dump(n, include_attributes=False) for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and
                    ((name == "Retry" and isinstance(n.func, ast.Name) and n.func.id == name)
                     or (name == "get" and isinstance(n.func, ast.Attribute)
                         and isinstance(n.func.value, ast.Name) and n.func.value.id == "session"
                         and n.func.attr == name))]
        if not calls(previous) or calls(previous) != calls(present):
            raise ValueError("client retry or request contract changed")
    return {"historical_sha256": _sha(historical), "current_sha256": _sha(working),
            "current_source_verified": True, "historical_source_verified": True,
            "source_authentication": False, "operational_trading_approval": False,
            "independent_review_approval": False, "scope": review["scope"]}


V2_ATTESTATION_SHA256 = "9402ac71a85ed9ab7df6120260b21851a27bcf0b8381d692a94bb7da5c676709"


def _verify_v2(root=ROOT):
    """Verify preserved v1 history and the separately reviewed v2 deadline client."""
    try:
        root = Path(root)
        legacy = _verify_v1(root)
        path = root / V2_ATTESTATION
        raw = path.read_bytes()
        if _sha(raw) != V2_ATTESTATION_SHA256:
            raise ValueError("v2 attestation hash mismatch")
        record = json.loads(raw)
        if (record["schema_version"] != "upstox-client-source-attestation-v2"
                or record["parent_attestation"] != {"path": HISTORICAL_ATTESTATION,
                                                    "sha256": HISTORICAL_ATTESTATION_SHA256}
                or record["parent_source_sha256"] != legacy["current_sha256"]):
            raise ValueError("v2 parent identity mismatch")
        current = record["current_source"]
        saved = (path.parent / current["snapshot"]).read_bytes()
        working = saved  # v2 remains pinned to its preserved source; v3 verifies live bytes.
        if (saved != working or _sha(working) != current["sha256"]
                or len(working) != current["bytes"]
                or working.count(b"\r\n") != current["crlf_count"]):
            raise ValueError("current client differs from reviewed v2 source")
        previous = json.loads((root / HISTORICAL_ATTESTATION).read_bytes())["current_source"]
        old = (root / Path(HISTORICAL_ATTESTATION).parent / previous["snapshot"]).read_bytes()
        import difflib
        patch = ''.join(difflib.unified_diff(old.decode().splitlines(True), working.decode().splitlines(True),
                                            fromfile="reviewed-v1", tofile="reviewed-v2")).encode()
        if ((path.parent / record["reviewed_diff"]["path"]).read_bytes() != patch
                or _sha(patch) != record["reviewed_diff"]["sha256"]):
            raise ValueError("v2 reviewed diff does not match preserved source versions")
        old_tree, tree = ast.parse(old), ast.parse(working)
        functions = lambda t: {n.name: ast.dump(n, include_attributes=False) for n in t.body if isinstance(n, ast.FunctionDef)}
        a, b = functions(old_tree), functions(tree)
        for name in ("get_headers", "get_profile", "get_quote", "get_intraday_candles", "get_historical_candles"):
            if a[name] != b[name]:
                raise ValueError("read-only endpoint/auth contract changed")
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in {"post", "put", "patch", "delete"} for n in ast.walk(tree)):
            raise ValueError("non-GET transport surface")
        review = record["review"]
        if (not review["reviewer"] or not review["changes"] or not review["tests"]
                or review["operational_trading_approval"] is not False
                or review["independent_review_approval"] is not False):
            raise ValueError("v2 review scope invalid")
        return {**legacy, "current_sha256": _sha(working), "reviewed_parent_sha256": legacy["current_sha256"],
                "attestation_version": 2, "current_source_verified": True}
    except Exception:
        raise ValueError("client attestation verification failed; reviewed source/evidence required") from None



ATTESTATION_SHA256 = "89d15e3d74d16a020f13ff18aca7a6768db006d7d9565fbd5a09017d3bb30083"


def verify_client_attestation(root=ROOT):
    """Verify preserved v1/v2 evidence and the one-attempt-capable v3 client."""
    try:
        root = Path(root)
        legacy = _verify_v2(root)
        path = root / ATTESTATION
        raw = path.read_bytes()
        if _sha(raw) != ATTESTATION_SHA256:
            raise ValueError("v3 attestation hash mismatch")
        record = json.loads(raw)
        if (record["schema_version"] != "upstox-client-source-attestation-v3"
                or record["parent_attestation"] != {"path": V2_ATTESTATION, "sha256": V2_ATTESTATION_SHA256}
                or record["parent_source_sha256"] != legacy["current_sha256"]):
            raise ValueError("v3 parent identity mismatch")
        current = record["current_source"]
        working = (root / "app/upstox_client.py").read_bytes()
        if ((path.parent / current["snapshot"]).read_bytes() != working
                or _sha(working) != current["sha256"] or len(working) != current["bytes"]
                or working.count(b"\r\n") != current["crlf_count"]):
            raise ValueError("current client differs from v3 source")
        previous = json.loads((root / V2_ATTESTATION).read_bytes())["current_source"]
        old = (root / Path(V2_ATTESTATION).parent / previous["snapshot"]).read_bytes()
        import difflib
        patch = ''.join(difflib.unified_diff(old.decode().splitlines(True), working.decode().splitlines(True),
                                           fromfile="reviewed-v2", tofile="reviewed-v3")).encode()
        if ((path.parent / record["reviewed_diff"]["path"]).read_bytes() != patch
                or _sha(patch) != record["reviewed_diff"]["sha256"]):
            raise ValueError("v3 diff mismatch")
        functions = lambda data: {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(data).body
                                  if isinstance(n, ast.FunctionDef)}
        before, after = functions(old), functions(working)
        if any(before[n] != after[n] for n in ("get_headers", "get_profile", "get_quote",
                                               "get_intraday_candles", "get_historical_candles")):
            raise ValueError("v3 read-only endpoint/auth contract changed")
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in {"post", "put", "patch", "delete"} for n in ast.walk(ast.parse(working))):
            raise ValueError("v3 non-GET surface")
        review = record["review"]
        if review["operational_trading_approval"] is not False or review["independent_review_approval"] is not False:
            raise ValueError("v3 review does not grant authorization")
        return {**legacy, "current_sha256": _sha(working), "attestation_version": 3,
                "reviewed_v2_sha256": legacy["current_sha256"], "current_source_verified": True}
    except Exception:
        raise ValueError("client attestation verification failed; reviewed source/evidence required") from None


def verify_historical_audit_inputs(root=ROOT):
    """Check original audit pins and require separate evidence for client evolution."""
    root = Path(root)
    client = verify_client_attestation(root)
    audit = json.loads((root / "backtests/provenance_integrity_v1/input_hashes.json").read_text())
    results = {}
    for item in audit["inputs"]:
        name = item["path"]
        if name == "app/upstox_client.py":
            # The old audit binds historical bytes, not a newly substituted pin.
            observed = client["historical_sha256"]
            current_verified = client["current_source_verified"]
        else:
            observed = _sha((root / name).read_bytes())
            current_verified = observed == item["sha256"]
        if observed != item["sha256"] or not current_verified:
            raise ValueError("historical audit input or reviewed current source mismatch")
        results[name] = {"historical_observed_sha256": observed,
                         "current_source_verified": current_verified}
    return results
