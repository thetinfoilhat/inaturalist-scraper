#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Optional, Tuple

import cloudinary
import cloudinary.api
import cloudinary.uploader

log = logging.getLogger("cloudinary_rename_prefix")


def _iter_resources(prefix: str, resource_type: str = "image") -> Iterable[dict]:
    next_cursor: Optional[str] = None
    while True:
        log.info("Listing resources with prefix=%s, resource_type=%s", prefix, resource_type)
        res = cloudinary.api.resources(
            type="upload",
            prefix=prefix,
            resource_type=resource_type,
            max_results=500,
            next_cursor=next_cursor,
        )
        for item in res.get("resources", []):
            yield item
        next_cursor = res.get("next_cursor")
        if not next_cursor:
            break


def _compute_new_id(public_id: str, from_prefix: str, to_prefix: str) -> Optional[str]:
    if not public_id.startswith(from_prefix):
        return None
    rest = public_id[len(from_prefix) :].lstrip("/")
    if not rest:
        return to_prefix
    return f"{to_prefix}/{rest}"


def _rename_batch(
    from_prefix: str,
    to_prefix: str,
    resource_type: str,
    apply: bool,
    invalidate: bool,
    async_rename: bool,
    workers: int,
    limit: Optional[int],
) -> List[tuple[str, str]]:
    moved: List[tuple[str, str]] = []
    count = 0
    futures = []
    executor = ThreadPoolExecutor(max_workers=workers) if apply and workers > 1 else None
    for item in _iter_resources(from_prefix, resource_type=resource_type):
        public_id = item.get("public_id")
        if not public_id:
            continue
        log.info("Processing %s", public_id)
        new_id = _compute_new_id(public_id, from_prefix, to_prefix)
        if not new_id or new_id == public_id:
            continue
        log.info("Planned rename: %s -> %s", public_id, new_id)
        moved.append((public_id, new_id))
        count += 1
        if limit and count >= limit:
            break
        if apply:
            options = {
                "overwrite": True,
                "invalidate": invalidate,
                "resource_type": resource_type,
            }
            if async_rename:
                options["async"] = True
            if executor:
                log.info("Queue rename %s -> %s", public_id, new_id)
                futures.append(
                    executor.submit(cloudinary.uploader.rename, public_id, new_id, **options)
                )
            else:
                log.info("Renaming %s -> %s", public_id, new_id)
                cloudinary.uploader.rename(public_id, new_id, **options)
    if executor:
        log.info("Waiting for %d rename tasks...", len(futures))
        for fut in as_completed(futures):
            fut.result()
        executor.shutdown(wait=True)
    return moved


def _parse_keys_file(path: str, profile: str) -> Tuple[str, str, str]:
    want = "rocks" if profile == "rocks" else "ento/water"
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines()]
    except OSError as e:
        raise SystemExit(f"Cannot read keys file {path}: {e}")

    idx = None
    for i, ln in enumerate(lines):
        if ln.lower() == f"{want}:":
            idx = i
            break
    if idx is None:
        raise SystemExit(f"Profile {profile} not found in {path}.")

    vals: List[str] = []
    for ln in lines[idx + 1 :]:
        if not ln:
            if vals:
                break
            continue
        if ln.lower().endswith(":"):
            break
        vals.append(ln)
        if len(vals) >= 3:
            break
    if len(vals) < 3:
        raise SystemExit(f"Profile {profile} in {path} is missing credentials.")

    url_line, api_key, api_secret = vals[0], vals[1], vals[2]
    cloud_name = ""
    if "@" in url_line:
        cloud_name = url_line.split("@", 1)[1].strip()
    if not cloud_name:
        raise SystemExit(f"Could not parse cloud name from {path} for {profile}.")
    return cloud_name, api_key, api_secret


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename Cloudinary public_ids by moving a prefix."
    )
    parser.add_argument("--cloud-name", default=os.getenv("CLOUDINARY_CLOUD_NAME"))
    parser.add_argument("--api-key", default=os.getenv("CLOUDINARY_API_KEY"))
    parser.add_argument("--api-secret", default=os.getenv("CLOUDINARY_API_SECRET"))
    parser.add_argument(
        "--profile",
        choices=["rocks", "water", "entomology"],
        help="Use credentials from a local keys file instead of env vars.",
    )
    parser.add_argument(
        "--keys-file",
        default="cloudinarykeys.txt",
        help="Path to local keys file (default: cloudinarykeys.txt).",
    )
    parser.add_argument("--from-prefix", required=True)
    parser.add_argument("--to-prefix", required=True)
    parser.add_argument(
        "--resource-type",
        default="image",
        choices=["image", "raw", "video"],
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable info-level logging.",
    )
    parser.add_argument(
        "--no-invalidate",
        action="store_true",
        help="Skip CDN invalidation for faster renames.",
    )
    parser.add_argument(
        "--async",
        dest="async_rename",
        action="store_true",
        help="Use Cloudinary async rename (faster, no immediate confirmation).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel rename workers when applying.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform renames (default is dry-run).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    global log
    log = logging.getLogger("cloudinary_rename_prefix")

    if args.profile:
        profile = "rocks" if args.profile == "rocks" else "ento/water"
        cloud_name, api_key, api_secret = _parse_keys_file(args.keys_file, profile)
        log.info("Using profile %s (cloud_name=%s)", args.profile, cloud_name)
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )
    elif args.cloud_name or args.api_key or args.api_secret:
        log.info("Using explicit Cloudinary credentials from flags/env.")
        cloudinary.config(
            cloud_name=args.cloud_name,
            api_key=args.api_key,
            api_secret=args.api_secret,
            secure=True,
        )
    elif os.getenv("CLOUDINARY_URL"):
        log.info("Using Cloudinary credentials from CLOUDINARY_URL.")
        cloudinary.config(secure=True)
    else:
        raise SystemExit(
            "Missing Cloudinary credentials; set CLOUDINARY_URL or pass flags."
        )

    cfg = cloudinary.config()
    if not (cfg.cloud_name and cfg.api_key and cfg.api_secret):
        raise SystemExit(
            "Cloudinary config incomplete; set CLOUDINARY_URL or pass flags."
        )

    moved = _rename_batch(
        args.from_prefix.strip("/"),
        args.to_prefix.strip("/"),
        args.resource_type,
        args.apply,
        not args.no_invalidate,
        args.async_rename,
        max(1, args.workers),
        args.limit,
    )

    label = "Renamed" if args.apply else "Would rename"
    print(f"{label} {len(moved)} assets:")
    for old, new in moved:
        print(f"- {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
