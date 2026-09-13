"""Schema-driven extraction against synthetic scored lines."""

from __future__ import annotations

from pathlib import Path

from ocr_engine.extraction.extract import extract_fields
from ocr_engine.extraction.schema import load_schemas
from ocr_engine.extraction.validate import build_result
from ocr_engine.ocr.base import Line

SCHEMAS = load_schemas(Path(__file__).parent.parent.parent / "schemas")


def _indomaret_lines() -> list[Line]:
    # Real line shapes from the recorded Indomaret receipt (bbox x grows right, y grows down).
    return [
        Line("FTECKMAPCO FR", 0.74, (157, 20, 476, 61)),
        Line("Indomaret", 0.94, (817, 70, 1124, 162)),
        Line("RAYA B0G0R KM 32/0000218742390", 0.94, (247, 282, 999, 354)),
        Line("04.01.17-21:17", 0.98, (129, 539, 477, 601)),
        Line("JHNSON COL. HP/B 125", 0.94, (127, 667, 614, 735)),
        Line("24500", 0.999, (768, 677, 905, 739)),
        Line("24,500", 0.97, (988, 677, 1145, 742)),
        Line("DISKON:", 0.96, (699, 807, 900, 866)),
        Line("(3,500)", 0.94, (977, 807, 1147, 873)),
        Line("HARGA JUAL:", 0.86, (601, 932, 910, 1004)),
        Line("27.800", 0.93, (997, 939, 1163, 1006)),
        Line("TOTAL:", 0.95, (725, 1071, 910, 1132)),
        Line("27,800", 0.94, (1001, 1073, 1173, 1144)),
        Line("TUNAI:", 0.92, (725, 1135, 908, 1196)),
        Line("100,000", 0.98, (984, 1140, 1178, 1211)),
        Line("KEMBALI:", 0.97, (674, 1198, 909, 1263)),
        Line("72,200", 0.97, (1004, 1207, 1183, 1281)),
        Line("PPN", 0.94, (119, 1308, 209, 1369)),
        Line("：DPP=28,455 PPN=2.845", 0.92, (318, 1312, 910, 1402)),
        Line("LAYANAN KONSUMEN INDOMARET", 0.97, (313, 1439, 961, 1530)),
    ]


def test_receipt_schema_loaded() -> None:
    schema = SCHEMAS["receipt@1"]
    assert {f.name for f in schema.fields} >= {"merchant", "date", "total"}
    total = schema.field("total")
    assert total is not None and total.type == "money"


def test_extracts_total_via_label_near_right() -> None:
    schema = SCHEMAS["receipt@1"]
    extracted = extract_fields(schema, _indomaret_lines())
    by_name = {f.name: f for f in extracted}

    assert by_name["total"].amount == 27800
    assert by_name["total"].currency == "IDR"
    assert by_name["total"].confidence >= 0.9


def test_result_completed_when_required_fields_pass() -> None:
    schema = SCHEMAS["receipt@1"]
    extracted = extract_fields(schema, _indomaret_lines())
    result, status = build_result(schema, extracted)

    assert status in ("completed", "needs_review")  # low-quality header may miss merchant/date
    total = next(f for f in result.to_dict()["fields"] if f["name"] == "total")
    assert total["amount"] == 27800
    assert total["status"] == "extracted"


def test_low_confidence_total_needs_review() -> None:
    schema = SCHEMAS["receipt@1"]
    lines = [
        Line("TOTAL:", 0.95, (725, 1071, 910, 1132)),
        Line("27,800", 0.40, (1001, 1073, 1173, 1144)),
    ]
    extracted = extract_fields(schema, lines)
    result, status = build_result(schema, extracted)

    assert status == "needs_review"
    total = next(f for f in result.to_dict()["fields"] if f["name"] == "total")
    assert total["status"] == "low_confidence"
    assert result.to_dict()["issues"][0]["code"] == "LOW_CONFIDENCE"


def test_missing_total_flags_needs_review() -> None:
    schema = SCHEMAS["receipt@1"]
    extracted = extract_fields(schema, [Line("kopi susu", 0.99, (0, 0, 100, 20))])
    result, status = build_result(schema, extracted)

    assert status == "needs_review"
    total = next(f for f in result.to_dict()["fields"] if f["name"] == "total")
    assert total["status"] == "missing"
    assert total["amount"] == 0
