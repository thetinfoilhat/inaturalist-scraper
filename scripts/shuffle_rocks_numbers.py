#!/usr/bin/env python3
"""Randomize the numbers in rock image filenames (e.g. specimen(1).webp) per specimen."""

import argparse
import logging
import random
import re
from pathlib import Path

LOG = logging.getLogger(__name__)

PAREN_PAT = re.compile(r"^(.+?)\((\d+)\)\.([a-zA-Z0-9]+)$")
UNDERSCORE_PAT = re.compile(r"^(.+)_(\d+)\.([a-zA-Z0-9]+)$")
# Plain base.ext (no number) - only if name doesn't end with (N) or _N
PLAIN_PAT = re.compile(r"^(.+)\.([a-zA-Z0-9]+)$")
PLAIN_EXCLUDE = re.compile(r"\(\d+\)$|_\d+$")


def count_by_specimen(rocks_dir: Path) -> tuple[int, dict[str, int]]:
    """Return (total_count, {specimen_label: count}). Specimen_label is base.ext for grouping."""
    total = 0
    by_specimen: dict[str, int] = {}
    for f in rocks_dir.iterdir():
        if not f.is_file():
            continue
        parsed = parse_filename(f.name)
        if not parsed:
            continue
        base, _num, ext = parsed
        key = f"{base}.{ext}"
        by_specimen[key] = by_specimen.get(key, 0) + 1
        total += 1
    return total, by_specimen


def parse_filename(name: str) -> tuple[str, str | None, str] | None:
    """Return (base, num, ext). num is None for unnumbered (e.g. actinolite.webp)."""
    m = PAREN_PAT.match(name)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = UNDERSCORE_PAT.match(name)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = PLAIN_PAT.match(name)
    if m and not PLAIN_EXCLUDE.search(m.group(1)):
        return m.group(1), None, m.group(2)
    return None


def shuffle_rocks_numbers(rocks_dir: Path, seed: int | None = None, dry_run: bool = True) -> tuple[int, int]:
    """
    Per specimen base name, shuffle the numbers in filenames.
    E.g. actinolite(1).webp, actinolite(2).webp -> actinolite(2).webp, actinolite(1).webp (or any permutation).
    """
    rocks_dir = rocks_dir.resolve()
    if not rocks_dir.is_dir():
        raise SystemExit(f"Not a directory: {rocks_dir}")

    initial_total, initial_by_specimen = count_by_specimen(rocks_dir)
    LOG.info("Initial file count: %d total (%d specimens)", initial_total, len(initial_by_specimen))

    if seed is not None:
        random.seed(seed)

    # Group files by (base, ext) -> list of (path, num). num is None for unnumbered (e.g. actinolite.webp)
    groups: dict[tuple[str, str], list[tuple[Path, str | None]]] = {}
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
        # Sort: unnumbered (None) first as 0, then by int(num), then by path
        files.sort(key=lambda x: (0 if x[1] is None else int(x[1]), x[0].name))
        # Shuffle; 0th element will become unnumbered (base.ext), 1st -> base(1).ext, etc.
        shuffled = list(files)
        random.shuffle(shuffled)
        # plan: (path, old_num, new_index). new_index 0 -> base.ext, i>0 -> base(i).ext
        plan = [(path, old_num, new_index) for new_index, (path, old_num) in enumerate(shuffled)]

        # Two-phase rename: first to unique temp names, then to final (0th = unnumbered)
        temp_prefix = f"{base}.__shuffle_"
        for path, old_num, new_index in plan:
            new_name = f"{base}.{ext}" if new_index == 0 else f"{base}({new_index}).{ext}"
            old_name = path.name
            if old_name == new_name:
                continue
            temp_name = f"{temp_prefix}{new_index}.{ext}"
            if dry_run:
                print(f"Would rename: {old_name} -> {new_name}")
            else:
                path.rename(path.parent / temp_name)
            renamed += 1

        if not dry_run:
            for _path, old_num, new_index in plan:
                # Skip if file was already in the right place (we didn't temp-rename it)
                if (old_num is None and new_index == 0) or (
                    old_num is not None and new_index > 0 and int(old_num) == new_index
                ):
                    continue
                temp_path = _path.parent / f"{temp_prefix}{new_index}.{ext}"
                final_path = _path.parent / (
                    f"{base}.{ext}" if new_index == 0 else f"{base}({new_index}).{ext}"
                )
                temp_path.rename(final_path)

    if not dry_run and renamed > 0:
        final_total, final_by_specimen = count_by_specimen(rocks_dir)
        LOG.info("Final file count: %d total", final_total)
        if final_total != initial_total:
            LOG.warning(
                "Total file count changed: before=%d, after=%d",
                initial_total,
                final_total,
            )
        for spec, before in initial_by_specimen.items():
            after = final_by_specimen.get(spec, 0)
            if after != before:
                LOG.warning(
                    "Specimen %s count changed: before=%d, after=%d",
                    spec,
                    before,
                    after,
                )
        for spec in final_by_specimen:
            if spec not in initial_by_specimen:
                LOG.warning("Specimen %s appeared (was not in initial count)", spec)

    return renamed, initial_total


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
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

    n, total_files = shuffle_rocks_numbers(args.rocks_dir, seed=args.seed, dry_run=not args.execute)
    if n == 0:
        print("No renames needed (or no matching files).")
    elif args.execute:
        print(f"Shuffled numbers for {n} file(s).")
    else:
        print(f"Would rename {n} file(s). Run with --execute to apply.")
    print(f"Total files: {total_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
