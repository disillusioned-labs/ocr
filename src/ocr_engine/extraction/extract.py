"""Field extraction from scored lines, driven by the schema matchers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from dateparser import parse as parse_date

from ..ocr.base import Line
from .money import parse_money
from .schema import FieldSchema, Schema


@dataclass
class ExtractedField:
    name: str
    value: str | None = None
    amount: int | None = None
    currency: str | None = None
    confidence: float = 0.0
    source_line: Line | None = None

    @property
    def has_value(self) -> bool:
        return self.value is not None or self.amount is not None


def extract_fields(schema: Schema, lines: list[Line]) -> list[ExtractedField]:
    results: list[ExtractedField] = []
    ranked = sorted(enumerate(lines), key=lambda pair: (pair[1].bbox[1], pair[1].bbox[0]))
    for fschema in schema.fields:
        results.append(_extract_one(fschema, ranked))
    return results


def _extract_one(fschema: FieldSchema, ranked_lines: list[tuple[int, Line]]) -> ExtractedField:
    for matcher in fschema.matchers:
        found = _apply_matcher(fschema, matcher, ranked_lines)
        if found is not None:
            return found
    return ExtractedField(name=fschema.name)


def _apply_matcher(
    fschema: FieldSchema, matcher, ranked_lines: list[tuple[int, Line]]
) -> ExtractedField | None:
    candidates = ranked_lines
    if matcher.max_top_n is not None:
        candidates = ranked_lines[: matcher.max_top_n]

    if matcher.kind == "positional":
        pool = [
            line
            for _, line in candidates
            if matcher.x_min_ratio is None or line.bbox[0] >= matcher.x_min_ratio * _page_width(candidates)
        ]
        for line in pool:
            value = _parse_typed(fschema, line.text)
            if value is not None:
                return _field(fschema, line, value)

    for _, line in candidates:
        if matcher.kind == "label":
            for label in matcher.labels:
                text = line.text.lower()
                if _label_matches(text, label, matcher.label_fuzzy_min):
                    value_part = _value_after_label(line.text, label)
                    if matcher.near_right:
                        right = _line_to_the_right(line, ranked_lines)
                        for candidate_text in (value_part, right.text if right else None):
                            value = _parse_typed(fschema, candidate_text or "")
                            if value is not None:
                                src = line if candidate_text == value_part else right
                                return _field(fschema, src, value)
                    else:
                        value = _parse_typed(fschema, value_part)
                        if value is not None:
                            return _field(fschema, line, value)
        elif matcher.kind == "regex":
            compiled = re.compile(matcher.pattern, re.IGNORECASE)
            match = compiled.search(line.text)
            if match:
                raw = match.group(1) if match.groups() else match.group(0)
                value = _parse_typed(fschema, raw)
                if value is not None:
                    return _field(fschema, line, value)
    return None


def _label_matches(text: str, label: str, min_score: int) -> bool:
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(label, text) >= min_score


def _value_after_label(text: str, label: str) -> str:
    lowered = text.lower()
    idx = lowered.find(label[: min(len(label), 6)])
    if idx == -1:
        return text
    after = text[idx + len(label) :]
    return after.lstrip(" ：:=-")


def _line_to_the_right(line: Line, ranked_lines: list[tuple[int, Line]]) -> Line | None:
    best: Line | None = None
    for _, other in ranked_lines:
        if other is line:
            continue
        if other.bbox[0] >= line.bbox[2] and _vertically_overlaps(line, other):
            if best is None or other.bbox[0] < best.bbox[0]:
                best = other
    return best


def _vertically_overlaps(a: Line, b: Line) -> bool:
    a_mid = (a.bbox[1] + a.bbox[3]) / 2
    return b.bbox[1] <= a_mid <= b.bbox[3]


def _page_width(ranked_lines: list[tuple[int, Line]]) -> int:
    return max((line.bbox[2] for _, line in ranked_lines), default=1)


def _parse_typed(fschema: FieldSchema, text: str) -> str | int | None:
    text = (text or "").strip()
    if not text:
        return None
    if fschema.type == "money":
        return parse_money(text)
    if fschema.type == "date":
        parsed = parse_date(
            text,
            settings={"DATE_ORDER": "DMY", "STRICT_PARSING": False, "RETURN_AS_TIMEZONE_AWARE": False},
        )
        return parsed.strftime("%Y-%m-%d") if parsed else None
    return text or None


def _field(fschema: FieldSchema, line: Line, value) -> ExtractedField:
    if fschema.type == "money":
        return ExtractedField(
            name=fschema.name,
            amount=value,
            currency=fschema.currency,
            confidence=line.score,
            source_line=line,
        )
    return ExtractedField(name=fschema.name, value=str(value), confidence=line.score, source_line=line)
