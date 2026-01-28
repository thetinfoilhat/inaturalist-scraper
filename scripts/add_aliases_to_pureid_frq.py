#!/usr/bin/env python3
"""Add specimen aliases to pure_id FRQ answers in JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def _title_case(s: str) -> str:
    return " ".join(w.capitalize() for w in s.replace("_", " ").replace("-", " ").split())


def _load_aliases(data_root: Path) -> Dict[str, List[str]]:
    alias_map: Dict[str, List[str]] = {}
    for path in [
        data_root / "rocks_and_minerals" / "rocks.json",
        data_root / "entomology" / "entomology.json",
    ]:
        if not path.exists():
            continue
        items = json.loads(path.read_text(encoding="utf-8"))
        for item in items:
            specimen = str(item.get("specimen", "")).strip()
            aliases = item.get("aliases", []) or []
            if specimen and aliases:
                alias_map[specimen] = [str(a).strip() for a in aliases if str(a).strip()]
    return alias_map


def main() -> int:
    ap = argparse.ArgumentParser(description="Add aliases to pure_id FRQ answers in JSONL.")
    ap.add_argument("input", help="Path to JSONL questions file")
    ap.add_argument("--output", help="Output JSONL (defaults to input path)")
    ap.add_argument("--data-root", default="/Users/lm/Bugbo/data", help="Data root for specimen JSONs")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output) if args.output else in_path
    alias_map = _load_aliases(Path(args.data_root))

    updated = 0
    lines: List[str] = []
    with in_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("pure_id") is True:
                options = item.get("options", [])
                if isinstance(options, list) and len(options) == 0:
                    specimen = str(item.get("specimen", ""))
                    aliases = alias_map.get(specimen, [])
                    if aliases:
                        answers = item.get("answers", [])
                        if not isinstance(answers, list):
                            answers = [answers]
                        existing = {str(a).strip() for a in answers if str(a).strip()}
                        for alias in aliases:
                            alias_cap = _title_case(alias)
                            if alias_cap not in existing:
                                answers.append(alias_cap)
                                existing.add(alias_cap)
                                updated += 1
                        item["answers"] = answers
            lines.append(json.dumps(item, ensure_ascii=False))

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"updated {updated} alias answers -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
