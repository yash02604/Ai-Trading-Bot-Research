"""Strict UTF-8 append policy on copied synthetic legacy exports."""
import copy
import csv
import io
from pathlib import Path

import pytest

from app import trade_logger as log
from app import paper_trader


def csv_bytes(rows=(), encoding="utf-8"):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=log.FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def read_rows():
    raw = Path(log.LOG_FILE).read_bytes()
    return raw, list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize("value", ["café", "₹", "ASCII", "=SUM(A1:A2)", "=café", "   =café", "@café"])
def test_verified_cp1252_regression_is_rejected_without_any_append(value):
    path = Path(log.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = csv_bytes([{"reason": "café"}], encoding="cp1252")
    path.write_bytes(original)
    trade = {"reason": value, "pnl": -1.25}
    before = copy.deepcopy(trade)
    for _ in range(2):
        with pytest.raises(ValueError, match="UTF-8"):
            log.log_trade(trade)
        assert path.read_bytes() == original
        assert b"caf\xe9" in path.read_bytes()
        assert b"caf\xc3\xa9" not in path.read_bytes()
        assert trade == before


@pytest.mark.parametrize("initial", [None, b"", csv_bytes(), b"\xef\xbb\xbf" + csv_bytes(), csv_bytes([{"reason": "ASCII"}]), csv_bytes([{"reason": "café"}])])
def test_new_empty_header_ascii_and_utf8_files_have_one_consistent_encoding(initial):
    path = Path(log.LOG_FILE)
    if initial is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(initial)
    expected = [] if not initial else [r["reason"] for r in csv.DictReader(io.StringIO(initial.decode("utf-8-sig"), newline=""))]
    for value in ["ASCII", "café", '₹, "नमस्ते"\nnext', ""]:
        log.log_trade({"reason": value, "quantity": 2, "pnl": -1.25})
        expected.append(value)
        raw, rows = read_rows()
        assert [r["reason"] for r in rows] == expected
        assert raw.decode("utf-8-sig").count(",ai_reason,") == 1
        assert raw.startswith(initial or b"")
        assert raw.endswith(b"\r\n") and b"\r\r\n" not in raw
        assert rows[-1]["pnl"] == "-1.25"
        assert rows[-1]["quantity"] == "2"
    raw, _ = read_rows()
    assert b"caf\xc3\xa9" in raw
    assert "₹".encode("utf-8") in raw


@pytest.mark.parametrize("value", ["=SUM(A1:A2)", "=café", "   =café", "@café", "+café", "-café", "\t=café", "\ufeff=café", '=₹, "note"\nnext'])
def test_formula_protection_and_utf8_append_work_together(value):
    path = Path(log.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = csv_bytes([{"reason": "café"}])
    path.write_bytes(prefix)
    trade = {"ai_reason": value, "quantity": 1, "pnl": -2}
    original = copy.deepcopy(trade)
    log.log_trade(trade)
    raw, rows = read_rows()
    assert raw.startswith(prefix)
    assert rows[-1]["ai_reason"] == "'" + value
    assert rows[-1]["pnl"] == "-2"
    assert trade == original


@pytest.mark.parametrize("content", [b"\xff\xfe", "symbol,reason\r\nTEST,café\r\n".encode("utf-16"), b"\xff", b"foreign,header\r\n", b"\n", csv_bytes().rstrip(b"\r\n")])
def test_unsupported_or_ambiguous_existing_files_are_rejected_unchanged(content):
    path = Path(log.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with pytest.raises(ValueError, match="CSV|UTF-8"):
        log.log_trade({"reason": "café"})
    assert path.read_bytes() == content


@pytest.mark.parametrize("initial", [None, b"", csv_bytes([{"reason": "café"}])])
def test_unencodable_row_fails_before_header_or_partial_row_write(initial):
    path = Path(log.LOG_FILE)
    if initial is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(initial)
    with pytest.raises(UnicodeEncodeError):
        log.log_trade({"ai_reason": "unpaired surrogate \ud800"})
    if initial is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == initial


def test_normal_paper_closure_rejects_legacy_file_without_changing_export(monkeypatch):
    path = Path(log.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = csv_bytes([{"reason": "café"}], encoding="cp1252")
    path.write_bytes(original)
    monkeypatch.setattr(paper_trader, "log_trade", log.log_trade)
    trader = paper_trader.PaperTrader()
    assert trader.open_position("SYNTHETIC", 100, 99) is not None
    with pytest.raises(ValueError, match="UTF-8"):
        trader.close_position(100, "café")
    assert path.read_bytes() == original
