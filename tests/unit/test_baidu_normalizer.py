"""Normalizer: recorded Baidu responses must map to scored Lines."""

from __future__ import annotations

import json
from pathlib import Path

from ocr_engine.ocr.baidu import normalize_layout_parsing

FIXTURE = Path(__file__).parent.parent / "fixtures" / "baidu_sync_response.json"


def test_normalize_from_recorded_response():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lines = normalize_layout_parsing(body)

    assert lines, "recorded response must yield lines"
    by_text = {line.text: line for line in lines}
    assert "Indomaret" in by_text
    assert by_text["Indomaret"].score >= 0.9
    assert by_text["Indomaret"].bbox[0] > 0

    total_line = next(line for text, line in by_text.items() if text.startswith("TOTAL"))
    assert total_line.score > 0.9


def test_normalize_drops_empty_and_noise_lines():
    body = {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "overall_ocr_res": {
                            "rec_texts": ["TOTAL:", "", "一", "27,800", " "],
                            "rec_scores": [0.95, 0.9, 0.38, 0.93, 0.0],
                            "rec_boxes": [
                                [10, 10, 100, 30],
                                [10, 40, 100, 60],
                                [10, 70, 100, 90],
                                [10, 100, 100, 120],
                                [10, 130, 100, 150],
                            ],
                        }
                    }
                }
            ]
        }
    }
    lines = normalize_layout_parsing(body)
    texts = [line.text for line in lines]
    assert texts == ["TOTAL:", "27,800"]


def test_normalize_assigns_page_numbers():
    body = {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "overall_ocr_res": {
                            "rec_texts": ["p1"],
                            "rec_scores": [0.9],
                            "rec_boxes": [[0, 0, 10, 10]],
                        }
                    }
                },
                {
                    "prunedResult": {
                        "overall_ocr_res": {
                            "rec_texts": ["p2"],
                            "rec_scores": [0.9],
                            "rec_boxes": [[0, 0, 10, 10]],
                        }
                    }
                },
            ]
        }
    }
    lines = normalize_layout_parsing(body)
    assert [line.page for line in lines] == [1, 2]
