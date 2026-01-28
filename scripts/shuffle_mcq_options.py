#!/usr/bin/env python3
"""Shuffle MCQ options and fix answer index in a JSON array file."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List


def _is_mcq(item: Dict[str, Any]) -> bool:
    options = item.get("options")
    answers = item.get("answers")
    if not isinstance(options, list) or len(options) < 2:
        return False
    if not isinstance(answers, list) or len(answers) != 1:
        return False
    return isinstance(answers[0], int)


def _shuffle_item(item: Dict[str, Any], rng: random.Random) -> bool:
    if not _is_mcq(item):
        return False
    options: List[str] = list(item["options"])
    correct_idx = item["answers"][0]
    if not (0 <= correct_idx < len(options)):
        return False

    indices = list(range(len(options)))
    rng.shuffle(indices)

    new_options = [options[i] for i in indices]
    new_correct_idx = indices.index(correct_idx)

    item["options"] = new_options
    item["answers"] = [new_correct_idx]
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Shuffle MCQ options and update answers in a JSON array file.")
    ap.add_argument("input", help="Path to input JSON array")
    ap.add_argument("--output", help="Path to output file (defaults to input path)")
    ap.add_argument("--seed", type=int, default=None, help="Random seed for deterministic shuffling")
    ap.add_argument(
        "--format",
        choices=["jsonl", "array"],
        default="jsonl",
        help="Output format: jsonl (one object per line) or array",
    )
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path

    text = in_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            raise SystemExit("Input JSON must be an array or JSONL.")
    except json.JSONDecodeError:
        data = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            data.append(json.loads(line))

    rng = random.Random(args.seed)
    updated = 0
    for item in data:
        if isinstance(item, dict) and _shuffle_item(item, rng):
            updated += 1

    with out_path.open("w", encoding="utf-8") as f:
        if args.format == "array":
            f.write("[\n")
            for i, item in enumerate(data):
                if i:
                    f.write(",\n")
                f.write(json.dumps(item, ensure_ascii=False))
            f.write("\n]\n")
        else:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False))
                f.write("\n")
    print(f"shuffled {updated} MCQ items -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
