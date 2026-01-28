#!/usr/bin/env python3
"""Validate count of pure_id questions per specimen from JSONL output."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def main() -> int:
    ap = argparse.ArgumentParser(description="Count pure_id questions per specimen in JSONL.")
    ap.add_argument("input", help="Path to JSONL file")
    ap.add_argument("--sort", choices=["name", "count"], default="name")
    args = ap.parse_args()

    path = Path(args.input)
    counts: Dict[str, int] = defaultdict(int)

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("pure_id") is True:
                specimen = item.get("specimen", "")
                counts[specimen] += 1

    items = list(counts.items())
    if args.sort == "count":
        items.sort(key=lambda x: (x[1], x[0]))
    else:
        items.sort(key=lambda x: x[0])

    for specimen, count in items:
        print(f"{specimen}\t{count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
