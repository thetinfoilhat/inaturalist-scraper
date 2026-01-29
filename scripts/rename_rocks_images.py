#!/usr/bin/env python3
"""Rename rock image files to name(number).ext (hyphens in name → underscores)."""

import argparse
import re
from pathlib import Path


def rename_rocks_images(rocks_dir: Path, pattern: str | None = None, dry_run: bool = True) -> int:
    """
    Rename to base(number).ext with hyphens → underscores in base.
    Handles: base(N).ext (e.g. tigers-eye(147).webp) and base_N.ext (e.g. tigers_eye_147.webp).
    If pattern is set, only rename files whose base name matches (e.g. 'tigers-eye' or 'tigers_eye').
    """
    rocks_dir = rocks_dir.resolve()
    if not rocks_dir.is_dir():
        raise SystemExit(f"Not a directory: {rocks_dir}")

    # Match name(123).ext -> base, num, ext
    paren_pat = re.compile(r"^(.+?)\((\d+)\)\.([a-zA-Z0-9]+)$")
    # Match name_123.ext -> base, num, ext
    underscore_pat = re.compile(r"^(.+)_(\d+)\.([a-zA-Z0-9]+)$")
    renamed = 0

    for f in sorted(rocks_dir.iterdir()):
        if not f.is_file():
            continue
        base, num, ext = None, None, None
        m = paren_pat.match(f.name)
        if m:
            base, num, ext = m.group(1), m.group(2), m.group(3)
            new_base = base.replace("-", "_")
            new_name = f"{new_base}({num}).{ext}"
        else:
            m = underscore_pat.match(f.name)
            if m:
                base, num, ext = m.group(1), m.group(2), m.group(3)
                new_name = f"{base}({num}).{ext}"
        if base is None:
            continue
        if pattern is not None and base.replace("-", "_") != pattern.replace("-", "_"):
            continue
        if new_name == f.name:
            continue
        dest = f.parent / new_name
        if dest.exists():
            print(f"Skip (dest exists): {f.name} -> {new_name}")
            continue
        if dry_run:
            print(f"Would rename: {f.name} -> {new_name}")
        else:
            f.rename(dest)
            print(f"Renamed: {f.name} -> {new_name}")
        renamed += 1

    return renamed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rename rocks images to name(number).ext (hyphens → underscores in name)."
    )
    ap.add_argument(
        "rocks_dir",
        nargs="?",
        default=Path(__file__).resolve().parents[1] / "images" / "rocks",
        type=Path,
        help="Directory containing rock images (default: repo images/rocks)",
    )
    ap.add_argument(
        "--pattern",
        default=None,
        help="Only rename files with this base name (e.g. 'tigers-eye')",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually rename files (default is dry-run)",
    )
    args = ap.parse_args()

    n = rename_rocks_images(args.rocks_dir, pattern=args.pattern, dry_run=not args.execute)
    if n == 0:
        print("No files to rename.")
    elif args.execute:
        print(f"Renamed {n} file(s).")
    else:
        print(f"Would rename {n} file(s). Run with --execute to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
