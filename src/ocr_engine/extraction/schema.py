"""Extraction schema: YAML definitions drive everything (fields, matchers, thresholds)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .money import parse_money  # noqa: F401 - re-exported for extractor convenience

KNOWN_TYPES = {"money", "text", "date"}


class Matcher(BaseModel):
    kind: Literal["label", "regex", "positional"]
    labels: list[str] = Field(default_factory=list)
    pattern: str | None = None
    near_right: bool = False
    x_min_ratio: float | None = None
    max_top_n: int | None = None
    label_fuzzy_min: int = 82

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v

    @model_validator(mode="after")
    def _validate(self) -> Matcher:
        if self.kind == "label" and not self.labels:
            raise ValueError("label matcher needs labels")
        if self.kind == "regex" and not self.pattern:
            raise ValueError("regex matcher needs pattern")
        if self.kind == "positional" and self.x_min_ratio is None and self.max_top_n is None:
            raise ValueError("positional matcher needs x_min_ratio or max_top_n")
        return self


@dataclass(frozen=True)
class FieldSchema:
    name: str
    type: str
    required: bool
    threshold: float
    currency: str
    matchers: tuple[Matcher, ...]


@dataclass(frozen=True)
class Schema:
    schema_id: str
    doc_type: str
    fields: tuple[FieldSchema, ...]

    def field(self, name: str) -> FieldSchema | None:
        return next((f for f in self.fields if f.name == name), None)


def load_schemas(schema_dir: Path) -> dict[str, Schema]:
    """Load and validate every YAML schema; any inconsistency must fail the boot."""
    schemas: dict[str, Schema] = {}
    for path in sorted(Path(schema_dir).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = _parse_schema(raw, source=path.name)
        if schema.schema_id in schemas:
            raise ValueError(f"duplicate schema_id {schema.schema_id!r} in {path.name}")
        schemas[schema.schema_id] = schema
    if not schemas:
        raise ValueError(f"no extraction schemas found in {schema_dir}")
    return schemas


def _parse_schema(raw: dict, source: str) -> Schema:
    if not isinstance(raw, dict) or "schema_id" not in raw or "fields" not in raw:
        raise ValueError(f"{source}: schema must define schema_id and fields")
    fields: list[FieldSchema] = []
    for f in raw["fields"]:
        if "name" not in f:
            raise ValueError(f"{source}: field without name")
        matchers = tuple(
            Matcher(
                kind=m["kind"],
                labels=m.get("labels", []),
                pattern=m.get("pattern"),
                near_right=m.get("near_right", False),
                x_min_ratio=m.get("x_min_ratio"),
                max_top_n=m.get("max_top_n"),
                label_fuzzy_min=m.get("label_fuzzy_min", 82),
            )
            for m in f.get("matchers", [])
        )
        if not matchers:
            raise ValueError(f"{source}: field {f['name']!r} has no matchers")
        threshold = float(f.get("threshold", 0.7))
        if not 0 <= threshold <= 1:
            raise ValueError(f"{source}: threshold for {f['name']!r} outside 0-1")
        ftype = f.get("type", "text")
        if ftype not in KNOWN_TYPES:
            raise ValueError(f"{source}: unknown type {ftype!r} for {f['name']!r}")
        fields.append(
            FieldSchema(
                name=f["name"],
                type=ftype,
                required=bool(f.get("required", True)),
                threshold=threshold,
                currency=f.get("currency", "IDR"),
                matchers=matchers,
            )
        )
    if not fields:
        raise ValueError(f"{source}: schema has no fields")
    return Schema(
        schema_id=raw["schema_id"],
        doc_type=raw.get("doc_type", raw["schema_id"].split("@")[0]),
        fields=tuple(fields),
    )
