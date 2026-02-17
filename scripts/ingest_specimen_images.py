#!/usr/bin/env python3
"""Upload specimen images to Cloudinary and upsert into specimen_pictures."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import cloudinary
    import cloudinary.uploader
except Exception as exc:
    raise SystemExit("Please install: pip install cloudinary") from exc

try:
    import psycopg2
except Exception as exc:
    raise SystemExit("Please install: pip install psycopg2-binary") from exc


CLOUDINARY_ENV = {
    "Rocks and Minerals": {
        "cloud_name": "CLOUDINARY_ROCKS_CLOUD_NAME",
        "api_key": "CLOUDINARY_ROCKS_API_KEY",
        "api_secret": "CLOUDINARY_ROCKS_API_SECRET",
    },
    "Entomology": {
        "cloud_name": "CLOUDINARY_EW_CLOUD_NAME",
        "api_key": "CLOUDINARY_EW_API_KEY",
        "api_secret": "CLOUDINARY_EW_API_SECRET",
    },
    "Water Quality - Freshwater": {
        "cloud_name": "CLOUDINARY_EW_CLOUD_NAME",
        "api_key": "CLOUDINARY_EW_API_KEY",
        "api_secret": "CLOUDINARY_EW_API_SECRET",
    },
}

EVENT_SOURCES = {
    "Rocks and Minerals": {
        "data_path": "data/rocks_and_minerals/rocks.json",
        "specimen_key": "specimen",
        "images_dir": "images/rocks",
    },
    "Entomology": {
        "data_path": "data/entomology/entomology.json",
        "specimen_key": "specimen",
        "images_dir": "images/ento",
    },
    "Water Quality - Freshwater": {
        "data_path": "data/water_quality/water.json",
        "specimen_key": "common_name",
        "images_dir": "images/water",
    },
}

NAMESPACE = uuid.UUID("5fdce5ef-3770-4b3e-88c4-3f0b0b2c8f2f")
LOGGER = logging.getLogger("image-ingest")


def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="[%(levelname)s] %(message)s")

def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _normalize(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\(\d+\)$", "", name)
    name = name.replace("-", "_").replace(" ", "_")
    name = re.sub(r"_+", "_", name)
    return name


def _load_specimens(data_path: Path, specimen_key: str) -> Tuple[Dict[str, str], List[str]]:
    items = json.loads(data_path.read_text(encoding="utf-8"))
    lookup: Dict[str, str] = {}
    originals: List[str] = []
    for item in items:
        specimen = str(item.get(specimen_key, "")).strip()
        if not specimen:
            continue
        originals.append(specimen)
        lookup[_normalize(specimen)] = specimen
    return lookup, originals


def _image_map(images_dir: Path) -> Dict[str, List[Path]]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    out: Dict[str, List[Path]] = {}
    for path in images_dir.iterdir():
        if path.suffix.lower() not in exts:
            continue
        key = _normalize(path.stem)
        out.setdefault(key, []).append(path)
    # sort each specimen's images by filename to ensure stable order
    for key in out:
        out[key].sort(key=lambda p: p.name.lower())
    return out


def _deterministic_uuid(event: str, specimen: str, filename: str) -> str:
    key = f"{event}|{specimen}|{filename}"
    return str(uuid.uuid5(NAMESPACE, key))


def _pick_distractors(pool: List[str], specimen: str, count: int = 3) -> List[str]:
    candidates = [p for p in pool if p != specimen]
    if not candidates:
        return []
    rng = random.SystemRandom()
    if len(candidates) <= count:
        rng.shuffle(candidates)
        return candidates
    return rng.sample(candidates, count)


def _connect_db() -> "psycopg2.extensions.connection":
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Missing DATABASE_URL for DB connection.")
    rootcert = os.environ.get("PGSSLROOTCERT")
    if rootcert and "sslrootcert=" not in dsn:
        cert_path = rootcert
        if not os.path.isfile(cert_path):
            # If PGSSLROOTCERT is PEM or base64, write to a temp file.
            temp_path = "/tmp/bugbo_pgrootcert.pem"
            try:
                if "BEGIN CERTIFICATE" in rootcert:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(rootcert)
                else:
                    import base64

                    data = base64.b64decode(rootcert)
                    with open(temp_path, "wb") as f:
                        f.write(data)
                cert_path = temp_path
                LOGGER.info("Wrote PGSSLROOTCERT to %s", cert_path)
            except Exception as exc:
                raise SystemExit(f"Invalid PGSSLROOTCERT value: {exc}") from exc
        joiner = "&" if "?" in dsn else "?"
        dsn = f"{dsn}{joiner}sslrootcert={cert_path}"
    return psycopg2.connect(dsn)


def _upsert(cur, row: Dict[str, object]) -> None:
    cur.execute(
        """
        INSERT INTO public.specimen_pictures (
            id, specimen, cloudinary_link, event_name, distractors
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            specimen = EXCLUDED.specimen,
            cloudinary_link = EXCLUDED.cloudinary_link,
            event_name = EXCLUDED.event_name,
            distractors = EXCLUDED.distractors,
            updated_at = now()
        """,
        (
            row["id"],
            row["specimen"],
            row["cloudinary_link"],
            row["event_name"],
            row["distractors"],
        ),
    )
    LOGGER.info(
        "DB upsert | id=%s | specimen=%s | event=%s | url=%s | distractors=%s",
        row["id"],
        row["specimen"],
        row["event_name"],
        row["cloudinary_link"],
        row["distractors"],
    )


def _load_existing_ids(cur, event: str) -> set[str]:
    cur.execute(
        """
        SELECT id
        FROM public.specimen_pictures
        WHERE event_name = %s
        """,
        (event,),
    )
    return {str(row[0]) for row in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload specimen images and upsert specimen_pictures.")
    ap.add_argument("--rocks", action="store_true")
    ap.add_argument("--entomology", action="store_true")
    ap.add_argument("--water", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true", help="Skip rows that already exist in specimen_pictures by deterministic id.")
    ap.add_argument("--log-level", default="DEBUG")
    args = ap.parse_args()

    _setup_logging(args.log_level)
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")

    selected = []
    if args.rocks:
        selected.append("Rocks and Minerals")
    if args.entomology:
        selected.append("Entomology")
    if args.water:
        selected.append("Water Quality - Freshwater")
    if not selected:
        selected = ["Rocks and Minerals", "Entomology", "Water Quality - Freshwater"]

    root = Path("/Users/lm/Bugbo")

    conn = None if args.dry_run else _connect_db()
    cur = None if args.dry_run else conn.cursor()

    try:
        for event in selected:
            src = EVENT_SOURCES[event]
            data_path = root / src["data_path"]
            images_dir = root / src["images_dir"]
            lookup, specimen_list = _load_specimens(data_path, src["specimen_key"])
            image_map = _image_map(images_dir)
            existing_ids: set[str] = set()
            if cur and args.skip_existing:
                existing_ids = _load_existing_ids(cur, event)
                LOGGER.info("Loaded %d existing rows for %s", len(existing_ids), event)

            missing = [s for s in specimen_list if _normalize(s) not in image_map]
            if missing:
                LOGGER.error("Missing images for %s: %s", event, missing)
                return 1

            env = CLOUDINARY_ENV[event]
            cloud_name = os.environ.get(env["cloud_name"])
            api_key = os.environ.get(env["api_key"])
            api_secret = os.environ.get(env["api_secret"])
            if not cloud_name or not api_key or not api_secret:
                LOGGER.error("Missing Cloudinary creds for %s (check .env)", event)
                return 1
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
            LOGGER.info("Processing event: %s | specimens=%d | images=%d", event, len(specimen_list), sum(len(v) for v in image_map.values()))

            for specimen in specimen_list:
                norm_name = _normalize(specimen)
                paths = image_map.get(norm_name, [])
                if not paths:
                    continue
                for path in paths:
                    row_id = _deterministic_uuid(event, specimen, path.name)
                    if row_id in existing_ids:
                        LOGGER.debug("Skipping existing row | specimen=%s | file=%s | id=%s", specimen, path.name, row_id)
                        continue
                    distractors = _pick_distractors(specimen_list, specimen, count=3)
                    LOGGER.info("Uploading %s | specimen=%s", path.name, specimen)
                    if args.dry_run:
                        url = f"DRYRUN://{path.name}"
                    else:
                        upload = cloudinary.uploader.upload(
                            str(path),
                            folder=f"{event.replace(' ', '_').lower()}",
                            public_id=path.stem,
                            overwrite=True,
                            resource_type="image",
                            transformation=None,
                            use_filename=True,
                        )
                        url = upload.get("secure_url") or upload.get("url")
                    LOGGER.debug("Cloudinary URL: %s", url)
                    row = {
                        "id": row_id,
                        "specimen": specimen,
                        "cloudinary_link": url,
                        "event_name": event,
                        "distractors": distractors,
                    }
                    if args.dry_run:
                        LOGGER.info("Dry run row: %s", row)
                    else:
                        _upsert(cur, row)
                        existing_ids.add(row_id)
                        LOGGER.debug("Upserted DB row | specimen=%s | file=%s", specimen, path.name)
                        conn.commit()
            if cur:
                pass
            LOGGER.info("Finished %s", event)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
