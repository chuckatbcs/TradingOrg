from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_lesson(review_path: Path, lessons_path: Path) -> dict[str, Any]:
    review = _read_json(review_path)
    lesson = {
        "schema_version": "tradingorg.lesson.v1",
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": review.get("ticker"),
        "analysis_date": review.get("analysis_date"),
        "original_rating": review.get("original_rating"),
        "direction_correct": review.get("direction_correct"),
        "outcome": review.get("outcome"),
        "lesson": review.get("lesson"),
        "next_prompt_hint": review.get("next_prompt_hint"),
        "source_review": str(review_path),
    }
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    with lessons_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(lesson, sort_keys=True) + "\n")
    return lesson


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append a post-game review lesson to lessons.jsonl.")
    parser.add_argument("--review", type=Path, required=True, help="Post-game review JSON path.")
    parser.add_argument("--lessons", type=Path, default=Path("memory/lessons.jsonl"), help="Lessons JSONL path.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    lesson = append_lesson(args.review, args.lessons)
    print(json.dumps(lesson, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
