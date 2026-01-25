#!/usr/bin/env python3
"""
Shuffle MCQ options in rocks_id_questions.json, water_id_questions.json, and
entomology_id_questions.json. For each MCQ: cache the correct answer from
options[answers[0]], shuffle the options array, find the new index of the
correct answer, and set answers=[new_index]. FRQs (options=[] or
answers=[string]) are unchanged. Overwrites the JSON files in place.
"""
import argparse
import json
import random
from pathlib import Path


def is_mcq(item: dict) -> bool:
    opts = item.get("options") or []
    ans = item.get("answers") or []
    if len(opts) == 0 or len(ans) == 0:
        return False
    a0 = ans[0]
    return isinstance(a0, int) and 0 <= a0 < len(opts)


def shuffle_mcq_options(item: dict) -> None:
    opts = item["options"]
    ans = item["answers"]
    correct = opts[ans[0]]
    random.shuffle(opts)
    item["answers"] = [opts.index(correct)]

def bellcurve_difficulty() -> float:
    val = random.gauss(0.5, 0.18)
    return max(0.0, min(1.0, val))


def process(items: list, shuffle_difficulty: bool) -> int:
    n = 0
    for q in items:
        if is_mcq(q):
            shuffle_mcq_options(q)
            if shuffle_difficulty:
                q["difficulty"] = round(bellcurve_difficulty(), 2)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Shuffle MCQ options in rocks, water, and entomology ID question JSONs."
    )
    ap.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    ap.add_argument(
        "--shuffle-difficulty",
        action="store_true",
        help="Randomize MCQ difficulty with a bell-curve-ish distribution",
    )
    ap.add_argument(
        "--water-freshwater",
        action="store_true",
        help='Set Water Quality event name to "Water Quality - Freshwater"',
    )
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    bugbo = Path(__file__).resolve().parent
    paths = [
        bugbo / "rocks_id_questions.json",
        bugbo / "water_id_questions.json",
        bugbo / "entomology_id_questions.json",
    ]
    for p in paths:
        with p.open("r", encoding="utf-8") as f:
            items = json.load(f)
        count = process(items, args.shuffle_difficulty)
        if args.water_freshwater and p.name == "water_id_questions.json":
            for q in items:
                if q.get("event") in {"Water Quality", "Water Quality - Freshwater"}:
                    q["event"] = "Water Quality - Freshwater"
        with p.open("w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        print(f"{p.name}: shuffled {count} MCQs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
