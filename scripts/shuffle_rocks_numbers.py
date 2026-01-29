#!/usr/bin/env python3
"""Randomize the numbers in rock image filenames (e.g. specimen(1).webp) per specimen."""

import argparse
import random
import re
from pathlib import Path


PAREN_PAT = re.compile(r"^(.+?)\((\d+)\)\.([a-zA-Z0-9]+)$")
UNDERSCORE_PAT = re.compile(r"^(.+)_(\d+)\.([a-zA-Z0-9]+)$")


def parse_filename(name: str) -> tuple[str, str, str] | None:
    """Return (base, num, ext) or None."""
    m = PAREN_PAT.match(name)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = UNDERSCORE_PAT.match(name)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def shuffle_rocks_numbers(rocks_dir: Path, seed: int | None = None, dry_run: bool = True) -> int:
    """
    Per specimen base name, shuffle the numbers in filenames.
    E.g. actinolite(1).webp, actinolite(2).webp -> actinolite(2).webp, actinolite(1).webp (or any permutation).
    """
    rocks_dir = rocks_dir.resolve()
    if not rocks_dir.is_dir():
        raise SystemExit(f"Not a directory: {rocks_dir}")

    if seed is not None:
        random.seed(seed)

    # Group files by (base, ext) -> list of (path, num)
    groups: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    for f in rocks_dir.iterdir():
        if not f.is_file():
            continue
        parsed = parse_filename(f.name)
        if not parsed:
            continue
        base, num, ext = parsed
        key = (base, ext)
        groups.setdefault(key, []).append((f, num))

    renamed = 0
    for (base, ext), files in groups.items():
        if len(files) <= 1:
            continue
        # Sort by current number (as int) for deterministic assignment
        files.sort(key=lambda x: (int(x[1]), x[0].name))
        nums = [p[1] for p in files]
        new_nums = nums.copy()
        random.shuffle(new_nums)
        # old_num -> new_num for each file (by index)
        plan = [(path, num, new_nums[i]) for i, (path, num) in enumerate(files)]

        # Two-phase rename to avoid collisions: first to temp, then to final
        temp_suffix = ".tmp_shuffle"
        for path, old_num, new_num in plan:
            if old_num == new_num:
                continue
            new_name = f"{base}({new_num}).{ext}"
            temp_name = f"{base}({old_num}){temp_suffix}.{ext}"
            if dry_run:
                print(f"Would rename: {path.name} -> {new_name}")
            else:
                path.rename(path.parent / temp_name)
            renamed += 1

        if not dry_run and plan:
            for path, old_num, new_num in plan:
                if old_num == new_num:
                    continue
                temp_path = path.parent / f"{base}({old_num}){temp_suffix}.{ext}"
                final_path = path.parent / f"{base}({new_num}).{ext}"
                temp_path.rename(final_path)

    return renamed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Randomize the numbers in rock image filenames per specimen (e.g. specimen(1).webp)."
    )
    ap.add_argument(
        "rocks_dir",
        nargs="?",
        default=Path(__file__).resolve().parents[1] / "images" / "rocks",
        type=Path,
        help="Directory containing rock images (default: repo images/rocks)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually rename files (default is dry-run)",
    )
    args = ap.parse_args()

    n = shuffle_rocks_numbers(args.rocks_dir, seed=args.seed, dry_run=not args.execute)
    if n == 0:
        print("No renames needed (or no matching files).")
    elif args.execute:
        print(f"Shuffled numbers for {n} file(s).")
    else:
        print(f"Would rename {n} file(s). Run with --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
