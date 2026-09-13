"""Turn extracted fields into the contract's DocumentResult and terminal status."""

from __future__ import annotations

from dataclasses import dataclass

from .extract import ExtractedField
from .schema import Schema

SCHEMA_VERSION = "1.0"


@dataclass
class DocumentResult:
    schema_version: str
    avg_confidence: float
    fields: list[dict]
    issues: list[dict]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "avg_confidence": round(self.avg_confidence, 4),
            "fields": self.fields,
            "issues": self.issues,
        }


def build_result(schema: Schema, extracted: list[ExtractedField]) -> tuple[DocumentResult, str]:
    """Returns (DocumentResult, terminal_status) - completed or needs_review."""
    fields: list[dict] = []
    issues: list[dict] = []
    confidences: list[float] = []

    for fschema, item in zip(schema.fields, extracted, strict=True):
        if not item.has_value:
            fields.append(_missing(fschema))
            if fschema.required:
                issues.append({"code": "VALIDATION_FAILED", "field": fschema.name, "detail": "not found"})
            continue

        if item.confidence < fschema.threshold:
            fields.append(_low_confidence(fschema, item))
            confidences.append(item.confidence)
            issues.append(
                {
                    "code": "LOW_CONFIDENCE",
                    "field": fschema.name,
                    "detail": f"{item.confidence:.2f} < threshold {fschema.threshold:.2f}",
                }
            )
            continue

        fields.append(_extracted(fschema, item))
        confidences.append(item.confidence)

    avg = sum(confidences) / len(confidences) if confidences else 0.0
    blocking = any(
        f["status"] in ("missing", "low_confidence", "invalid")
        for f, fs in zip(fields, schema.fields, strict=True)
        if fs.required
    )
    status = "needs_review" if blocking or issues else "completed"
    return DocumentResult(SCHEMA_VERSION, avg, fields, issues), status


def _extracted(fschema, item: ExtractedField) -> dict:
    field: dict = {"name": fschema.name, "confidence": round(item.confidence, 4), "status": "extracted"}
    if fschema.type == "money":
        field["amount"] = item.amount
        field["currency"] = item.currency
    else:
        field["value"] = item.value
    if item.source_line is not None:
        x1, y1, x2, y2 = item.source_line.bbox
        field["bbox"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        field["page"] = item.source_line.page
    return field


def _low_confidence(fschema, item: ExtractedField) -> dict:
    field = _extracted(fschema, item)
    field["status"] = "low_confidence"
    return field


def _missing(fschema) -> dict:
    field: dict = {"name": fschema.name, "confidence": 0.0, "status": "missing"}
    if fschema.type == "money":
        field["amount"] = 0
        field["currency"] = fschema.currency
    else:
        field["value"] = ""
    return field
